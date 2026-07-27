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


async def fire_trim(asset: str, side: str, fraction: float,
                    mark: float | None) -> tuple[bool, str, float | None]:
    """Send one partial close for `fraction` of the live position.

    The quantity is derived from strategy_positions, the same record order-listener
    clamps against, so a stale read can only ever under-close. Returns
    (ok, detail, close_size).
    """
    if _STRATEGY is None:
        return False, "shadow mode — no strategy armed", None

    pos = await db.open_position(settings.execution_strategy_id, asset)
    if pos is None:
        return False, f"no open {asset} position to trim", None
    if (pos["side"] or "").upper() != side:
        return False, f"position is {pos['side']}, expected {side}", None

    close_size = round(float(pos["size"]) * fraction, 8)
    if close_size <= 0:
        return False, f"computed close size {close_size} is not tradeable", None

    signal = "partial_close_long" if side == "LONG" else "partial_close_short"
    ok, detail = await emitter.emit(signal, asset, mark, _STRATEGY, close_size=close_size)
    return ok, detail, (close_size if ok else None)


async def _sweep_pending_trims() -> None:
    """One pass over parked trims: retire the dead, fire the ones the market reached."""
    expired = await db.expire_pending_trims()
    if expired:
        log.info("pending trims: %d expired unreached", expired)

    for t in await db.pending_trims():
        asset, side = t["asset"], t["side"]

        # The stance can move without this asset's handler running (a flip on
        # another message, a manual state edit). Re-check before every fire.
        if await db.get_state(asset) != side:
            await db.resolve_pending_trim(t["id"], "cancelled", "stance no longer " + side)
            log.info("parked trim msg %s cancelled: stance left %s", t["channel_msg_id"], side)
            continue

        mark = await marketdata.get_mark(asset)
        if mark is None:
            continue
        trig = float(t["trigger_price"])
        if not ((mark <= trig) if side == "SHORT" else (mark >= trig)):
            continue

        frac = float(t["size_fraction"])
        if _STRATEGY is None:
            await db.resolve_pending_trim(t["id"], "fired", "shadow mode")
            await db.resolve_shadow_order(t["channel_msg_id"], "acted",
                                          "trim_level_reached", mark, None, "shadow")
            log.info("SHADOW trim msg %s %s %.0f%% — level %s reached at %s",
                     t["channel_msg_id"], side, frac * 100, trig, mark)
            continue

        ok, detail, close_size = await fire_trim(asset, side, frac, mark)
        if ok:
            await db.resolve_pending_trim(t["id"], "fired", detail)
            await db.resolve_shadow_order(t["channel_msg_id"], "acted",
                                          "trim_level_reached", mark, close_size, "live")
            log.info("LIVE trim msg %s %s %.0f%% (%s) at %s -> %s",
                     t["channel_msg_id"], side, frac * 100, close_size, mark, detail)
        else:
            # Left pending on purpose: a transient failure gets the next sweep, and
            # the TTL is what eventually retires a trim that can never be sent.
            log.error("TRIM FAILED msg %s %s: %s — left parked",
                      t["channel_msg_id"], side, detail)


async def _pending_trim_loop() -> None:
    while True:
        await asyncio.sleep(settings.pending_trim_check_seconds)
        try:
            await _sweep_pending_trims()
        except Exception:  # noqa: BLE001
            log.exception("pending trim loop error")


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
        close_size = None
        if d["emit"] and asset and phase == "live" and _STRATEGY is not None:
            if d["is_trim"]:
                ok, detail, close_size = await fire_trim(
                    asset, cur, float(d["size_fraction"]), mark)
            else:
                ok, detail = await emitter.emit(d["intended_signal"], asset, mark, _STRATEGY)
            if ok:
                mode = "live"
                log.info("LIVE msg %s %s -> %s", rec["channel_msg_id"],
                         d["intended_signal"], detail)
            else:
                # A trim that cannot be sent is dropped, not parked: the instruction
                # was for right now, and the reason it failed (no position, no size)
                # will not fix itself by waiting.
                d = {**d, "advance": False, "emit": False, "park": False,
                     "decision": "skipped", "reason": "emit_failed", "to_state": cur}
                close_size = None
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
            "size_fraction":   d["size_fraction"],
            "close_size":      close_size,
        })

        # Park a trim whose level the market has not reached, so the watcher can
        # take it at the price the trader actually named. Recorded after the shadow
        # row so the watcher can only ever find a trim that already has its audit row.
        if d["park"] and asset and phase == "live":
            await db.insert_pending_trim({
                "channel_msg_id": rec["channel_msg_id"],
                "asset":          asset,
                "side":           cur,
                "size_fraction":  d["size_fraction"],
                "trigger_price":  d["trigger_price"],
            }, settings.pending_trim_ttl_hours)
            log.info("PARKED trim msg %s %s %.0f%% at %s (mark %s)",
                     rec["channel_msg_id"], cur, d["size_fraction"] * 100,
                     d["trigger_price"], d["mark_price"])

        if d["advance"] and asset:
            await db.set_state(asset, d["to_state"], rec["channel_msg_id"])
            # The stance just moved; any level parked against the old one belongs
            # to a trade that no longer exists.
            n = await db.cancel_pending_trims(asset, f"stance moved {cur}->{d['to_state']}")
            if n:
                log.info("cancelled %d parked trim(s) for %s: stance %s->%s",
                         n, asset, cur, d["to_state"])

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
    asyncio.create_task(_pending_trim_loop())

    log.info("Listening for new messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
