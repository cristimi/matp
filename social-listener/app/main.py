import asyncio
import logging

from telethon import events

from app import db, emitter, marketdata
from app.config import settings
from app.extractor import extract
from app.statemachine import evaluate
from app.telegram import build_client, merge_records, to_record

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("social-listener")

# Resolved once at startup by _load_execution_strategy(); None keeps the service
# in shadow no matter what execution_mode says.
_STRATEGY: dict | None = None


async def handle(msgs, phase: str):
    """
    Judge one post. `msgs` is the burst of Telegram messages that make it up —
    usually one, but the author often splits a single thought across two or three
    seconds apart (a comment, then the X link whose preview repeats it in full).
    They are merged before extraction so one post costs one LLM call and produces
    one verdict, instead of one per message.

    The merged record is keyed on the highest message id, so `already_seen` and
    `max_channel_msg_id` both refer to the burst as a whole.
    """
    if not isinstance(msgs, (list, tuple)):
        msgs = [msgs]
    if not msgs:
        return

    key_id = max(m.id for m in msgs)

    # Skip messages already fully evaluated by the brain (idempotent restarts).
    if await db.already_shadow_evaluated(key_id):
        return

    if await db.already_seen(key_id):
        # Already extracted — load from DB to avoid re-calling the LLM (and
        # re-downloading the image), before doing any Telegram work.
        rec = await db.load_signal(key_id)
        if rec is None:
            return
    else:
        base = merge_records([await to_record(m) for m in msgs])
        if len(msgs) > 1:
            log.info("merged %d messages into one post: %s",
                     len(msgs), base["merged_msg_ids"])
        ext = await extract(base["raw_text"], base["preview_text"], base["image_bytes"])
        if ext["failed"]:
            # The LLM call never returned a verdict. Don't record anything: an
            # inserted placeholder would make already_seen() true forever and the
            # message would never be re-extracted once the provider recovers.
            # Leaving it unrecorded also keeps max_channel_msg_id back, so the
            # catchup loop picks it up again on the next pass.
            log.error("msg %s: extraction unavailable, leaving unrecorded for retry", key_id)
            return
        rec = {**base, **ext}
        if await db.insert_signal(rec):
            flag = "ACTIONABLE" if rec["is_actionable"] else "·"
            log.info(
                "msg %s [%s] %s %s ref=%s conf=%.2f img=%s",
                key_id, flag, rec["action_type"], rec["asset"] or "-",
                rec["reference_price"], rec["confidence"],
                "y" if rec["has_image"] else "n",
            )

    if rec["is_actionable"]:
        asset = (rec["asset"] or "").upper() or None
        cur = await db.get_state(asset) if asset else "FLAT"

        # Live signals need a mark whether or not the post cited a price — a
        # priceless one is gated against the market price at posted_at instead.
        mark = implied_ref = None
        if phase == "live" and asset:
            mark = await marketdata.get_mark(asset)
            if rec["reference_price"] is None and rec.get("posted_at") is not None:
                implied_ref = await marketdata.get_close_at(
                    asset, int(rec["posted_at"].timestamp() * 1000)
                )

        d = evaluate(rec, phase, cur, mark, implied_ref)

        # Emit BEFORE recording, and only on the live path — "backfill" acts
        # unconditionally by design, so emitting there would re-fire every old
        # post on each restart. Fail-closed: a failed emission leaves the state
        # unchanged, so social_position_state never claims a position the
        # exchange does not hold. The cost of that choice is a missed trade,
        # which is the safe direction to be wrong in.
        mode = "shadow"
        if d["advance"] and asset and phase == "live" and _STRATEGY is not None:
            ok, detail = await emitter.emit(d["intended_signal"], asset, mark, _STRATEGY)
            if ok:
                mode = "live"
                log.info("LIVE msg %s %s -> %s", rec["channel_msg_id"],
                         d["intended_signal"], detail)
            else:
                d = {**d, "advance": False, "decision": "skipped",
                     "reason": "emit_failed", "to_state": cur}
                log.error("EMIT FAILED msg %s %s: %s — state left at %s",
                          rec["channel_msg_id"], d["intended_signal"], detail, cur)

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
            "mode":            mode,
        })

        if d["advance"] and asset:
            await db.set_state(asset, d["to_state"], rec["channel_msg_id"])

        log.info(
            "BRAIN msg %s %s->%s %s [%s/%s] mode=%s",
            rec["channel_msg_id"], cur, d["to_state"],
            d["intended_signal"], d["decision"], d["reason"], mode,
        )


def group_bursts(msgs: list, window_seconds: float, max_size: int) -> list[list]:
    """
    Split an ordered run of messages into bursts that belong to the same post.

    A new burst starts when the gap to the previous message exceeds the window,
    or when the current burst is already at max_size — the cap stops a busy
    stretch of unrelated posts from being welded into one giant prompt.
    """
    bursts: list[list] = []
    for m in sorted(msgs, key=lambda x: x.id):
        if (
            bursts
            and len(bursts[-1]) < max_size
            and (m.date - bursts[-1][-1].date).total_seconds() <= window_seconds
        ):
            bursts[-1].append(m)
        else:
            bursts.append([m])
    return bursts


class _LiveBuffer:
    """
    Holds just-arrived messages until the burst looks finished.

    A burst is only known to be complete once the window has elapsed with nothing
    new, so every live signal is delayed by that long. That is the price of one
    verdict per post; keep merge_window_seconds well under the state machine's
    max_signal_age_seconds.
    """

    def __init__(self):
        self._msgs: list = []
        self._task: asyncio.Task | None = None

    def add(self, msg) -> None:
        self._msgs.append(msg)
        if self._task and not self._task.done():
            self._task.cancel()
        # At the cap, flush at once rather than waiting for a quiet window.
        if len(self._msgs) >= settings.merge_max_messages:
            self._task = asyncio.create_task(self._flush(0))
        else:
            self._task = asyncio.create_task(self._flush(settings.merge_window_seconds))

    async def _flush(self, delay: float) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return   # a newer message arrived; it rescheduled the flush

        batch, self._msgs = self._msgs, []
        if not batch:
            return
        try:
            for burst in group_bursts(batch, settings.merge_window_seconds,
                                      settings.merge_max_messages):
                await handle(burst, "live")
        except Exception:  # noqa: BLE001
            log.exception("live flush error")


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
                bursts = group_bursts(gap, settings.merge_window_seconds,
                                      settings.merge_max_messages)
                log.warning("catchup: recovering %d missed message(s) after id %s as %d post(s)",
                            len(gap), last_id, len(bursts))
                for burst in bursts:
                    await handle(burst, "live")
        except Exception:  # noqa: BLE001
            log.exception("catchup loop error")


async def _load_execution_strategy() -> dict | None:
    """Validate live wiring at startup. Any problem degrades to shadow, loudly.

    Refusing to trade on a half-configured strategy is the whole point: a wrong
    account_id or a stale leverage here spends real money.
    """
    if settings.execution_mode != "live":
        log.info("execution_mode=shadow — decisions recorded, no orders sent")
        return None

    if not settings.execution_strategy_id:
        log.error("execution_mode=live but execution_strategy_id is unset — staying in shadow")
        return None

    s = await db.load_execution_strategy(settings.execution_strategy_id)
    if s is None:
        log.error("execution_mode=live but strategy %s does not exist — staying in shadow",
                  settings.execution_strategy_id)
        return None

    problems = []
    if s["is_deleted"]:
        problems.append("strategy is deleted")
    if not s["enabled"]:
        problems.append("strategy is disabled")
    if not s["account_id"]:
        problems.append("strategy has no account_id")
    if not s["webhook_secret"]:
        problems.append("strategy has no webhook_secret")
    if float(s["margin_per_trade"] or 0) <= 0:
        problems.append("margin_per_trade is not positive")
    if int(s["default_leverage"] or 0) > int(s["max_leverage"] or 0):
        problems.append(f"default_leverage {s['default_leverage']} exceeds "
                        f"max_leverage {s['max_leverage']}")
    if problems:
        log.error("execution_mode=live rejected for %s (%s) — staying in shadow",
                  s["id"], "; ".join(problems))
        return None

    log.warning(
        "LIVE execution armed: strategy=%s (%s) account=%s allocation=%s "
        "margin/trade=%s leverage=%sx %s",
        s["id"], s["name"], s["account_id"], s["capital_allocation"],
        s["margin_per_trade"], s["default_leverage"], s["margin_mode"],
    )
    return s


async def main():
    global _STRATEGY
    await db.init_db()

    from app.config_secrets import apply_llm_key_overrides
    await apply_llm_key_overrides(db.pool(), settings)

    _STRATEGY = await _load_execution_strategy()

    client = build_client()
    await client.start()  # StringSession is pre-authorized -> non-interactive
    me = await client.get_me()
    log.info("Telegram connected as %s", getattr(me, "username", None) or me.id)

    channel = await client.get_entity(settings.tg_channel)

    log.info("Backfilling last %d messages from %s", settings.backfill_limit, settings.tg_channel)
    msgs = []
    async for m in client.iter_messages(channel, limit=settings.backfill_limit):
        msgs.append(m)
    bursts = group_bursts(msgs, settings.merge_window_seconds, settings.merge_max_messages)
    for burst in bursts:  # oldest -> newest
        await handle(burst, "backfill")
    log.info("Backfill complete (%d messages, %d post(s))", len(msgs), len(bursts))

    buffer = _LiveBuffer()

    @client.on(events.NewMessage(chats=channel))
    async def _live(event):
        # Buffered rather than handled inline: the follow-up message that
        # completes the post has not arrived yet.
        try:
            buffer.add(event.message)
        except Exception:  # noqa: BLE001
            log.exception("live handler error")

    asyncio.create_task(_catchup_loop(client, channel))

    log.info("Listening for new messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
