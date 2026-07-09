import asyncio
import logging

from telethon import events

from app import db, marketdata
from app.config import settings
from app.extractor import extract
from app.statemachine import evaluate
from app.telegram import build_client, to_record

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("social-listener")

if settings.execution_mode != "shadow":
    log.warning("execution_mode=%s — live emission is not built yet; running as shadow", settings.execution_mode)


async def handle(msg, phase: str):
    # Skip messages already fully evaluated by the brain (idempotent restarts).
    if await db.already_shadow_evaluated(msg.id):
        return

    base = to_record(msg)

    if await db.already_seen(msg.id):
        # Already extracted — load from DB to avoid re-calling the LLM.
        rec = await db.load_signal(msg.id)
        if rec is None:
            return
    else:
        ext = await extract(base["raw_text"], base["preview_text"])
        rec = {**base, **ext}
        if await db.insert_signal(rec):
            flag = "ACTIONABLE" if rec["is_actionable"] else "·"
            log.info(
                "msg %s [%s] %s %s ref=%s conf=%.2f",
                msg.id, flag, rec["action_type"], rec["asset"] or "-",
                rec["reference_price"], rec["confidence"],
            )

    if rec["is_actionable"]:
        asset = (rec["asset"] or "").upper() or None
        cur = await db.get_state(asset) if asset else "FLAT"

        # fetch mark only for live priced signals (backfill: skip mark; priceless: no mark needed)
        mark = None
        if phase == "live" and asset and rec["reference_price"] is not None:
            mark = await marketdata.get_mark(asset)

        d = evaluate(rec, phase, cur, mark)

        await db.insert_shadow_order({
            "channel_msg_id": rec["channel_msg_id"],
            "posted_at":       rec["posted_at"],
            "phase":           phase,
            "asset":           asset,
            "action_type":     rec["action_type"],
            "from_state":      cur,
            "to_state":        d["to_state"],
            "intended_signal": d["intended_signal"],
            "reference_price": rec["reference_price"],
            "mark_price":      d["mark_price"],
            "confidence":      rec["confidence"],
            "decision":        d["decision"],
            "reason":          d["reason"],
        })

        if d["advance"] and asset:
            await db.set_state(asset, d["to_state"], rec["channel_msg_id"])

        log.info(
            "BRAIN msg %s %s->%s %s [%s/%s]",
            rec["channel_msg_id"], cur, d["to_state"],
            d["intended_signal"], d["decision"], d["reason"],
        )


async def _catchup_loop(client, channel):
    """Periodically reconcile Telegram's message history against what's recorded.

    The live NewMessage handler can silently miss events (Telethon reconnects,
    dropped updates) without the process ever crashing or restarting — the only
    recovery used to be the one-shot backfill at process startup, which could
    leave a real gap open for days. This closes that gap continuously, and
    replays anything found via the "live" phase (mark price + staleness gate),
    not "backfill" (which acts unconditionally and would skip that gate).
    """
    while True:
        await asyncio.sleep(settings.catchup_interval_seconds)
        try:
            last_id = await db.max_channel_msg_id()
            if last_id is None:
                continue

            gap = []
            async for m in client.iter_messages(
                channel, min_id=last_id, limit=settings.catchup_limit, reverse=True
            ):
                gap.append(m)

            if gap:
                log.warning("catchup: recovering %d missed message(s) after id %s", len(gap), last_id)
                for m in gap:
                    await handle(m, "live")
        except Exception:  # noqa: BLE001
            log.exception("catchup loop error")


async def main():
    await db.init_db()

    from app.config_secrets import apply_llm_key_overrides
    await apply_llm_key_overrides(db.pool(), settings)

    client = build_client()
    await client.start()  # StringSession is pre-authorized -> non-interactive
    me = await client.get_me()
    log.info("Telegram connected as %s", getattr(me, "username", None) or me.id)

    channel = await client.get_entity(settings.tg_channel)

    log.info("Backfilling last %d messages from %s", settings.backfill_limit, settings.tg_channel)
    msgs = []
    async for m in client.iter_messages(channel, limit=settings.backfill_limit):
        msgs.append(m)
    for m in reversed(msgs):  # oldest -> newest
        await handle(m, "backfill")
    log.info("Backfill complete (%d messages)", len(msgs))

    @client.on(events.NewMessage(chats=channel))
    async def _live(event):
        try:
            await handle(event.message, "live")
        except Exception:  # noqa: BLE001
            log.exception("live handler error")

    asyncio.create_task(_catchup_loop(client, channel))

    log.info("Listening for new messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
