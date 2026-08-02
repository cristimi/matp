import asyncio
import logging

from telethon import events

from app import db, emitter, marketdata, post_lock, statemachine
from app.config import settings
from app.extractor import extract
from app.legs import Legs
from app.statemachine import evaluate, resolve_leg
from app.telegram import build_client, merge_records, to_record

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("social-listener")

# Resolved once at startup by _load_execution_strategy(); None keeps the service
# in shadow no matter what execution_mode says.
_STRATEGY: dict | None = None

# Whether the channel may be followed into a long AND a short on the same coin.
# Mirrors the execution account's position_mode, and for a hard reason: on a net
# account the exchange nets the two legs against each other, so a second leg would
# be recorded as held while no such position exists. Resolved at startup — in
# shadow too, so a shadow decision is one the live path could actually have taken.
_MULTI_POSITION: bool = False


async def fire_trim(asset: str, side: str, fraction: float,
                    mark: float | None) -> tuple[bool, str, float | None]:
    """Send one partial close for `fraction` of the live position.

    The quantity is derived from strategy_positions, the same record order-listener
    clamps against, so a stale read can only ever under-close. Returns
    (ok, detail, close_size).
    """
    if _STRATEGY is None:
        return False, "shadow mode — no strategy armed", None

    pos = await db.open_position(settings.execution_strategy_id, asset, side)
    if pos is None:
        return False, f"no open {asset} {side} position to trim", None

    held = float(pos["size"])
    # Nothing left worth shaving. Repeated partials converge on dust — four of them
    # took a 0.0031 BTC short to 0.00029 on 2026-07-27 — and a stub that small should
    # leave on a CLOSE post, whole, not be quartered again.
    if mark is not None and mark > 0:
        floor = settings.min_trim_position_fraction * emitter.standard_entry_size(
            _STRATEGY, mark)
        if held < floor:
            return False, (f"position {held} is below the trim floor {round(floor, 8)} "
                           f"({settings.min_trim_position_fraction} of a standard entry) "
                           f"— close it whole instead"), None

    close_size = round(held * fraction, 8)
    if close_size <= 0:
        return False, f"computed close size {close_size} is not tradeable", None

    signal = "partial_close_long" if side == "LONG" else "partial_close_short"
    ok, detail = await emitter.emit(signal, asset, mark, _STRATEGY, close_size=close_size)
    return ok, detail, (close_size if ok else None)


async def apply_levels(rec: dict, asset: str, side: str | None,
                       mark: float | None) -> dict:
    """Set the stop and/or take-profit the post gives on ONE leg, subject to the guards.

    `side` is the leg the levels belong to. It is resolved by the caller and may be
    None when the post could not be pinned to a leg — with both a long and a short
    open and no side named, a stop is unattributable, and moving the wrong leg's
    stop is how a protected trade becomes an unprotected one.

    Runs BEFORE the position half of a decision on purpose. order-listener re-applies
    a position's TP/SL at their pre-close prices after a partial reduce, so a stop
    moved after a trim would race that resize and could be silently reverted; moved
    before it, the resize picks up the new level and rescales it to the smaller size.

    Both legs go out in ONE call, and always carry the level we want to end up with
    rather than only the one that changed — modify-stops cancels every trigger and
    places only what it is handed.

    Returns the four audit fields: stop_price, stop_reason, tp_price, tp_reason.
    """
    if _STRATEGY is None:
        # Still judged in shadow, so the recorded reason is the real one rather than
        # a blanket "shadow".
        _, sr = statemachine.evaluate_stop(rec, side, None, mark, None)
        _, tr = statemachine.evaluate_take_profit(rec, side, None, mark, None)
        return {"stop_price": None, "stop_reason": sr,
                "tp_price": None, "tp_reason": tr}

    if side is None:
        return {"stop_price": None, "stop_reason": "no_position_for_stop",
                "tp_price": None, "tp_reason": "no_position_for_tp"}

    pos = await db.open_position(settings.execution_strategy_id, asset, side)
    entry = float(pos["entry_price"]) if pos and pos["entry_price"] is not None else None
    held = await db.get_levels(asset, side)
    # What is resting now: what we last set, else whatever the opening order carried.
    cur_sl = held["stop_price"]
    cur_tp = held["tp_price"]
    if pos is not None:
        if cur_sl is None and pos["sl_price"] is not None:
            cur_sl = float(pos["sl_price"])
        if cur_tp is None and pos["tp_price"] is not None:
            cur_tp = float(pos["tp_price"])

    want_sl, sr = statemachine.evaluate_stop(
        rec, side, entry, mark, held["stop_price"])
    want_tp, tr = statemachine.evaluate_take_profit(
        rec, side, entry, mark, cur_tp)

    if want_sl is None and want_tp is None:
        for reason in (sr, tr):
            if reason not in ("no_stop_instruction", "no_tp_instruction"):
                log.info("levels unchanged for msg %s: sl=%s tp=%s "
                         "(entry=%s mark=%s held=%s)",
                         rec["channel_msg_id"], sr, tr, entry, mark, held)
        return {"stop_price": None, "stop_reason": sr,
                "tp_price": None, "tp_reason": tr}

    send_sl = want_sl if want_sl is not None else cur_sl
    send_tp = want_tp if want_tp is not None else cur_tp

    ok, detail = await emitter.adjust_levels(
        _STRATEGY, sl_price=send_sl, tp_price=send_tp, side=side)
    if not ok:
        # modify-stops is cancel-then-place, so a failure can leave the position
        # unprotected. Loud, and never recorded as levels we hold.
        log.error("LEVEL SET FAILED msg %s sl=%s tp=%s: %s — position triggers may be "
                  "UNCONFIRMED, check the exchange",
                  rec["channel_msg_id"], send_sl, send_tp, detail)
        return {"stop_price": None, "stop_reason": "stop_send_failed" if want_sl else sr,
                "tp_price": None, "tp_reason": "tp_send_failed" if want_tp else tr}

    # "Risk off" is a standing instruction, not a one-shot: after a scale-in blends
    # the entry, break-even means a different number and the watcher re-asserts it.
    mode = "breakeven" if (want_sl is not None and rec.get("stop_to_breakeven")
                           and rec.get("stop_price") is None) else None
    await db.set_levels(asset, side, stop_price=send_sl, tp_price=send_tp, stop_mode=mode)
    log.info("LEVELS msg %s %s %s sl=%s tp=%s (entry %s): %s",
             rec["channel_msg_id"], asset, side, send_sl, send_tp, entry, detail)
    return {"stop_price": want_sl, "stop_reason": sr if want_sl is None else "ok",
            "tp_price": want_tp, "tp_reason": tr if want_tp is None else "ok"}


async def fire_add(asset: str, side: str, multiple: float,
                   mark: float | None) -> tuple[bool, str, float | None]:
    """Scale into the live position by `multiple` standard entries.

    Two ceilings apply. order-listener clamps any single order to one
    margin_per_trade unit; this adds the cumulative one it cannot know about, so a
    run of "adding here" posts cannot compound past max_position_multiple entries.
    Returns (ok, detail, size_added).
    """
    if _STRATEGY is None:
        return False, "shadow mode — no strategy armed", None
    if mark is None or mark <= 0:
        return False, "no mark price — refusing to size a scale-in", None

    unit = emitter.standard_entry_size(_STRATEGY, mark)
    if unit <= 0:
        return False, "standard entry size is not tradeable", None

    pos = await db.open_position(settings.execution_strategy_id, asset, side)
    if pos is None:
        return False, f"no open {asset} {side} position to add to", None

    held = float(pos["size"])
    ceiling = settings.max_position_multiple * unit
    room = ceiling - held
    if room <= 0:
        return False, (f"exposure cap reached: {held} held vs ceiling {round(ceiling, 8)} "
                       f"({settings.max_position_multiple}x standard entry {unit})"), None

    size = round(min(multiple * unit, room), 8)
    if size <= 0:
        return False, f"computed add size {size} is not tradeable", None
    if size < multiple * unit:
        log.warning("add trimmed by exposure cap: wanted %s, sending %s "
                    "(held %s, ceiling %s)", round(multiple * unit, 8), size, held,
                    round(ceiling, 8))

    signal = "add_long" if side == "LONG" else "add_short"
    ok, detail = await emitter.emit(signal, asset, mark, _STRATEGY, open_size=size)
    if ok:
        # The blended entry price is about to move, so the stop we had set no longer
        # means what it meant. Drop it and let the standing intent re-assert.
        await db.clear_stop_price(asset, side)
    return ok, detail, (size if ok else None)


async def _sweep_standing_stops() -> None:
    """Re-assert break-even for legs whose trader asked to be de-risked.

    "Risk off the trade" is a standing instruction. A scale-in blends the entry
    price, so the number that means break-even changes underneath it; without this,
    a de-risked trade silently reverts to the wide guaranteed SL that order-listener
    attaches to the add order.

    Per leg: a de-risked long and a de-risked short break even at two different
    prices and need two separate stop moves.
    """
    if _STRATEGY is None:
        return
    for row in await db.legs_with_standing_stop():
        asset, side = row["asset"], row["side"]
        pos = await db.open_position(settings.execution_strategy_id, asset, side)
        if pos is None:
            continue
        entry = float(pos["entry_price"])
        held_sl = float(row["stop_price"]) if row["stop_price"] is not None else None
        if held_sl is not None and abs(held_sl - entry) / entry < 1e-6:
            continue   # already at break-even for the current entry

        mark = await marketdata.get_mark(asset)
        rec = {"stop_to_breakeven": True, "channel_msg_id": row.get("last_msg_id")}
        # last_stop is deliberately not passed: the entry legitimately moved, so the
        # monotonic guard would refuse the very correction this exists to make.
        want, reason = statemachine.evaluate_stop(rec, side, entry, mark, None)
        if want is None:
            log.info("standing break-even for %s not re-asserted: %s "
                     "(entry=%s mark=%s held=%s)", asset, reason, entry, mark, held_sl)
            continue

        tp = float(row["tp_price"]) if row["tp_price"] is not None else (
            float(pos["tp_price"]) if pos["tp_price"] is not None else None)
        ok, detail = await emitter.adjust_levels(
            _STRATEGY, sl_price=want, tp_price=tp, side=side)
        if ok:
            await db.set_levels(asset, side, stop_price=want, tp_price=tp)
            log.info("STANDING break-even re-asserted for %s %s: %s -> %s (entry %s): %s",
                     asset, side, held_sl, want, entry, detail)
        else:
            log.error("STANDING break-even FAILED for %s %s -> %s: %s — triggers may be "
                      "UNCONFIRMED, check the exchange", asset, side, want, detail)


async def _sweep_pending_trims() -> None:
    """One pass over parked trims: retire the dead, fire the ones the market reached."""
    expired = await db.expire_pending_trims()
    if expired:
        log.info("pending trims: %d expired unreached", expired)

    for t in await db.pending_trims():
        asset, side = t["asset"], t["side"]

        # The leg can close without this asset's handler running (a flip on another
        # message, a manual state edit). Re-check before every fire — and check the
        # LEG, not the asset: with a long and a short both open, the short's parked
        # trim must survive the long closing.
        if not (await db.get_legs(asset)).is_open(side):
            await db.resolve_pending_trim(t["id"], "cancelled", f"{side} leg no longer open")
            log.info("parked trim msg %s cancelled: %s %s leg closed",
                     t["channel_msg_id"], asset, side)
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
        try:
            await _sweep_standing_stops()
        except Exception:  # noqa: BLE001
            log.exception("standing stop loop error")


async def handle(msgs, phase: str, source: str = "live"):
    """Judge one post under the per-post lock, so the live handler and the catchup
    loop can never judge the same burst at the same time."""
    if not isinstance(msgs, (list, tuple)):
        msgs = [msgs]
    if not msgs:
        return
    key_id = max(m.id for m in msgs)
    async with post_lock.post_slot(key_id, source) as owned:
        if owned:
            await _handle_locked(msgs, phase, key_id)


async def _handle_locked(msgs, phase: str, key_id: int):
    """
    Judge one post. `msgs` is the burst of Telegram messages that make it up —
    usually one, but the author often splits a single thought across two or three
    seconds apart (a comment, then the X link whose preview repeats it in full).
    They are merged before extraction so one post costs one LLM call and produces
    one verdict, instead of one per message.

    The merged record is keyed on the highest message id, so `already_seen` and
    `max_channel_msg_id` both refer to the burst as a whole.
    """
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

    asset = (rec["asset"] or "").upper() or None
    legs = await db.get_legs(asset) if asset else Legs()
    cur = legs.label()
    carries_levels = (
        rec.get("stop_price") is not None
        or rec.get("stop_to_breakeven")
        or rec.get("take_profit_price") is not None
    )

    # A post that changes nothing can still carry the trade's numbers. The trader
    # routinely calls a trade in plain text and only shows entry/SL/TP on a chart in
    # a LATER post, which is a recap by every other measure. Those levels belong to
    # the position we are holding right now, so they are applied on their own.
    if not rec["is_actionable"]:
        if carries_levels and asset and phase == "live" and not legs.flat:
            mark = await marketdata.get_mark(asset)
            # Which leg the numbers belong to. With both open and no side named
            # this is None, and apply_levels records why rather than guessing.
            lv_side, _ = resolve_leg(rec, legs)
            lv = await apply_levels(rec, asset, lv_side, mark)
            await db.insert_shadow_order({
                "channel_msg_id": rec["channel_msg_id"], "posted_at": rec["posted_at"],
                "phase": phase, "asset": asset, "action_type": rec["action_type"],
                "from_state": cur, "to_state": cur, "intended_signal": "none",
                "reference_price": rec["reference_price"], "mark_price": mark,
                "confidence": rec["confidence"], "decision": "skipped",
                "reason": "levels_only", "mode": "shadow",
                "stop_price": lv["stop_price"], "stop_reason": lv["stop_reason"],
                "tp_price": lv["tp_price"], "tp_reason": lv["tp_reason"],
            })
            log.info("LEVELS-ONLY msg %s %s leg=%s sl=%s/%s tp=%s/%s",
                     rec["channel_msg_id"], cur, lv_side,
                     lv["stop_price"], lv["stop_reason"],
                     lv["tp_price"], lv["tp_reason"])
        return

    # Live signals need a mark whether or not the post cited a price — a
    # priceless one is gated against the market price at posted_at instead.
    mark = implied_ref = None
    if phase == "live" and asset:
        mark = await marketdata.get_mark(asset)
        if rec["reference_price"] is None and rec.get("posted_at") is not None:
            implied_ref = await marketdata.get_close_at(
                asset, int(rec["posted_at"].timestamp() * 1000)
            )

    d = evaluate(rec, phase, legs, mark, implied_ref, multi=_MULTI_POSITION)

    # The author re-posts the same trade card as the trade develops — msg 9794 was
    # msg 9790's card again with TP2 filled in, and the 64.4k trim it asked for had
    # already been taken eight hours earlier. A repost is not a new instruction, so
    # a trim is checked against the ones already carried out for this stance.
    if d["is_trim"] and asset and phase == "live" and _STRATEGY is not None:
        pos = await db.open_position(settings.execution_strategy_id, asset, d["leg"])
        if pos is not None:
            dup = await db.trim_already_taken(
                asset, d["leg"], d["trigger_price"], pos["opened_at"],
                settings.min_trim_interval_minutes)
            if dup:
                log.warning("msg %s trim refused: %s", rec["channel_msg_id"], dup)
                d = {**d, "decision": "skipped", "reason": dup.split(" ")[0],
                     "emit": False, "park": False, "advance": False,
                     "advance_legs": {}, "to_legs": legs, "to_state": cur}

    # Emit BEFORE recording, and only on the live path — "backfill" acts
    # unconditionally by design, so emitting there would re-fire every old
    # post on each restart. Fail-closed: a failed emission leaves the state
    # unchanged, so social_position_state never claims a position the
    # exchange does not hold. The cost of that choice is a missed trade,
    # which is the safe direction to be wrong in.
    # Levels first, and only while the post leaves us on the side we are already
    # on — a CLOSE or FLIP takes its triggers with it, and an OPEN has no position
    # yet (order-listener injects its guaranteed SL at entry instead).
    lv = {"stop_price": None, "stop_reason": "no_stop_instruction",
          "tp_price": None, "tp_reason": "no_tp_instruction"}
    if asset and phase == "live" and not d["advance_legs"] and not legs.flat:
        lv_side, _ = resolve_leg(rec, legs)
        lv = await apply_levels(rec, asset, lv_side, mark)

    mode = "shadow"
    close_size = add_size = None
    if d["emit"] and asset and phase == "live" and _STRATEGY is not None:
        if d["is_trim"]:
            ok, detail, close_size = await fire_trim(
                asset, d["leg"], float(d["size_fraction"]), mark)
        elif d["is_add"]:
            ok, detail, add_size = await fire_add(
                asset, d["leg"], float(d["add_multiple"]), mark)
        else:
            ok, detail = await emitter.emit(d["intended_signal"], asset, mark, _STRATEGY)
        if ok:
            mode = "live"
            log.info("LIVE msg %s %s -> %s", rec["channel_msg_id"],
                     d["intended_signal"], detail)
            if d["is_trim"]:
                # Into the same ledger parked trims use, so a later repost of this
                # instruction can see it was already carried out.
                await db.record_fired_trim({
                    "channel_msg_id": rec["channel_msg_id"], "asset": asset,
                    "side": d["leg"], "size_fraction": d["size_fraction"],
                    "trigger_price": d["trigger_price"],
                }, settings.pending_trim_ttl_hours, detail)
        else:
            # A trim or add that cannot be sent is dropped, not parked: the
            # instruction was for right now, and the reason it failed (no position,
            # no size, exposure cap) will not fix itself by waiting.
            d = {**d, "advance": False, "emit": False, "park": False,
                 "decision": "skipped", "reason": "emit_failed",
                 "advance_legs": {}, "to_legs": legs, "to_state": cur}
            close_size = add_size = None
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
        "add_size":        add_size,
        "stop_price":      lv["stop_price"],
        "stop_reason":     lv["stop_reason"],
        "tp_price":        lv["tp_price"],
        "tp_reason":       lv["tp_reason"],
    })

    # Park a trim whose level the market has not reached, so the watcher can
    # take it at the price the trader actually named. Recorded after the shadow
    # row so the watcher can only ever find a trim that already has its audit row.
    if d["park"] and asset and phase == "live":
        await db.insert_pending_trim({
            "channel_msg_id": rec["channel_msg_id"],
            "asset":          asset,
            "side":           d["leg"],
            "size_fraction":  d["size_fraction"],
            "trigger_price":  d["trigger_price"],
        }, settings.pending_trim_ttl_hours)
        log.info("PARKED trim msg %s %s %s %.0f%% at %s (mark %s)",
                 rec["channel_msg_id"], asset, d["leg"], d["size_fraction"] * 100,
                 d["trigger_price"], d["mark_price"])

    if d["advance"] and asset:
        await db.apply_leg_changes(asset, d["advance_legs"], rec["channel_msg_id"])
        # A leg that just CLOSED takes its parked trims with it. Scoped to that leg:
        # the other one may still be running, and its parked level is still valid.
        for side, is_open in d["advance_legs"].items():
            if is_open:
                continue
            n = await db.cancel_pending_trims(
                asset, side, f"{side} leg closed ({cur}->{d['to_state']})")
            if n:
                log.info("cancelled %d parked trim(s) for %s %s: %s->%s",
                         n, asset, side, cur, d["to_state"])

    log.info(
        "BRAIN msg %s %s->%s %s leg=%s [%s/%s] mode=%s sl=%s/%s tp=%s/%s",
        rec["channel_msg_id"], cur, d["to_state"],
        d["intended_signal"], d["leg"], d["decision"], d["reason"], mode,
        lv["stop_price"], lv["stop_reason"], lv["tp_price"], lv["tp_reason"],
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
                await handle(burst, "live", source="live-buffer")
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
                    await handle(burst, "live", source="catchup")
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
        "margin/trade=%s leverage=%sx %s position_mode=%s",
        s["id"], s["name"], s["account_id"], s["capital_allocation"],
        s["margin_per_trade"], s["default_leverage"], s["margin_mode"],
        s.get("position_mode"),
    )
    return s


async def _resolve_multi_position() -> bool:
    """Whether the channel may be followed into both sides of one coin.

    Read from the execution account, not from config: holding a long and a short at
    once is a property of the ACCOUNT (BloFin hedge mode), and claiming it while the
    account is in net mode would record two legs where the exchange holds one netted
    position. Resolved even in shadow, so shadow decisions stay ones the live path
    could actually have taken.
    """
    if not settings.execution_strategy_id:
        log.info("no execution_strategy_id — multi-position off (single stance per asset)")
        return False
    try:
        mode = await db.account_position_mode(settings.execution_strategy_id)
    except Exception:  # noqa: BLE001
        log.exception("could not read the execution account's position mode — "
                      "multi-position off")
        return False
    if mode == "hedge":
        log.warning("MULTI-POSITION on: the account is in hedge mode, so an OPEN "
                    "against an existing opposite leg becomes a SECOND position "
                    "instead of a flip")
        return True
    log.info("multi-position off: account position_mode=%s — an OPEN against an "
             "existing opposite leg stays a flip", mode)
    return False


async def main():
    global _STRATEGY, _MULTI_POSITION
    await db.init_db()

    from app.config_secrets import apply_llm_key_overrides
    await apply_llm_key_overrides(db.pool(), settings)

    _STRATEGY = await _load_execution_strategy()
    _MULTI_POSITION = await _resolve_multi_position()

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
        await handle(burst, "backfill", source="backfill")
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
