import logging
from datetime import datetime, timezone

from app.config import settings

log = logging.getLogger(__name__)
WHITELIST = {s.strip().upper() for s in settings.asset_whitelist.split(",") if s.strip()}

_SIGNAL = {
    ("FLAT", "LONG"):  "open_long",
    ("FLAT", "SHORT"): "open_short",
    ("LONG", "FLAT"):  "close_long",
    ("SHORT", "FLAT"): "close_short",
    ("LONG", "SHORT"): "flip_to_short",
    ("SHORT", "LONG"): "flip_to_long",
}

# A trim reduces an open position without changing which side we are on, so it has
# no entry in _SIGNAL — the state machine's three states cannot express "less of
# the same". It emits, it does not advance.
_TRIM_SIGNAL = {"LONG": "partial_close_long", "SHORT": "partial_close_short"}


def _target_state(action_type: str, direction: str | None) -> str | None:
    if action_type == "CLOSE":
        return "FLAT"
    if action_type in ("OPEN", "FLIP"):
        if direction == "LONG":
            return "LONG"
        if direction == "SHORT":
            return "SHORT"
    return None


def _trim_fraction(rec: dict) -> float:
    """The clamped share of the position a trim takes off.

    A post that names no amount gets the default. Whatever the model returns is
    clamped, so a hallucinated 1.0 cannot silently become a full close.
    """
    raw = rec.get("size_fraction")
    try:
        frac = float(raw) if raw is not None else settings.default_trim_fraction
    except (TypeError, ValueError):
        frac = settings.default_trim_fraction
    return max(settings.min_trim_fraction, min(settings.max_trim_fraction, frac))


def _too_old(rec: dict, now: datetime | None) -> bool:
    """True when the post is past the tradeable age backstop.

    A signal the catchup loop recovers hours after the post is stale no matter what
    the price says. A record with no timestamp cannot be aged, so it passes.
    """
    posted_at = rec.get("posted_at")
    if posted_at is None:
        return False
    age = ((now or datetime.now(timezone.utc)) - posted_at).total_seconds()
    return age > settings.max_signal_age_seconds


def evaluate_stop(
    rec: dict,
    cur_state: str,
    entry_price: float | None,
    mark: float | None,
    last_stop: float | None = None,
    now: datetime | None = None,
) -> tuple[float | None, str]:
    """Where the stop should go for a post that manages an open position.

    Returns (stop_price, reason). stop_price is None whenever nothing should be
    sent, and `reason` always says why.

    Three guards, and the first is the important one: a stop from a social post may
    only ever be at break-even or better. It can tighten what order-listener's
    guaranteed SL already put in place, never widen it. Widening is the only
    direction that increases how much the trade can lose, and a misread post must
    not be able to do that — the cost of the rule is that a trader legitimately
    giving a wider stop is ignored, which loses nothing we had.
    """
    named = rec.get("stop_price")
    to_be = bool(rec.get("stop_to_breakeven"))
    if named is None and not to_be:
        return None, "no_stop_instruction"

    if cur_state == "FLAT":
        return None, "no_position_for_stop"
    if _too_old(rec, now):
        return None, "signal_too_old"
    if entry_price is None:
        return None, "no_entry_price"

    # An explicitly named level wins over "break even" — it is the more specific
    # instruction. The guards below bound it either way.
    try:
        want = float(named) if named is not None else float(entry_price)
    except (TypeError, ValueError):
        return None, "no_stop_instruction"

    entry = float(entry_price)
    short = cur_state == "SHORT"

    # Guard 1 — never worse than break-even. On a short the stop sits above entry
    # while the trade is at risk, so tightening means coming DOWN to entry or below.
    if (want > entry) if short else (want < entry):
        return None, "stop_would_widen_risk"

    # Guard 2 — a stop already on the wrong side of the mark is not a stop, it is a
    # market exit wearing a stop's name. Refuse it and let the trade run to a real
    # signal instead of closing on a mis-parsed number.
    if mark is not None and ((want <= float(mark)) if short else (want >= float(mark))):
        return None, "stop_already_crossed"

    # Guard 3 — monotonic. Once we have tightened, a later post may only tighten
    # further, so a stale or re-read card cannot walk protection back out.
    if last_stop is not None:
        prev = float(last_stop)
        if (want > prev) if short else (want < prev):
            return None, "stop_not_tighter"

    return want, "ok"


def _evaluate_trim(rec, phase, cur_state, mark, now, base, skip) -> dict:
    """A partial profit-take on the position we are already recorded as holding.

    Deliberately NOT gated on `staleness_pct`. That gate stops us chasing an ENTRY
    that has already run away; a trim only ever reduces exposure, so refusing to
    bank profit because the price moved is the wrong failure direction — the same
    reasoning `partial_close` already gets in the AI engine's cooldown grouping.
    """
    if cur_state == "FLAT":
        return skip("no_position_to_trim")

    direction = (rec.get("direction") or "").upper() or None
    if direction and direction != cur_state:
        return skip("trim_side_mismatch")

    sig  = _TRIM_SIGNAL[cur_state]
    frac = _trim_fraction(rec)
    trig = rec.get("trigger_price")
    trig = float(trig) if trig is not None else None
    marks = {"sig": sig, "size_fraction": frac, "trigger_price": trig}

    # Backfill replays act unconditionally — right for rebuilding a stance out of
    # history, wrong for a trim, which has no stance to rebuild and would fire a
    # real reduce against an old post on every restart.
    if phase == "backfill":
        return skip("backfill_no_trim", **marks)

    if _too_old(rec, now):
        return skip("signal_too_old", **marks)

    def trim(reason):
        return base("acted", reason, cur_state, sig, False, True,
                    is_trim=True, size_fraction=frac, trigger_price=trig)

    # No level named: the post presents the trim as happening now, at market.
    if trig is None:
        return trim("trim_at_market")

    if mark is None:
        return skip("no_mark", **marks)

    reached = (mark <= trig) if cur_state == "SHORT" else (mark >= trig)
    if reached:
        return trim("trim_level_reached")

    # The trader named a level the market has not got to. Park it — taking profit
    # here would be at a worse price than the one they actually asked for.
    return base("pending", "trim_level_pending", cur_state, sig, False, False,
                is_trim=True, park=True, size_fraction=frac, trigger_price=trig)


def evaluate(
    rec: dict,
    phase: str,
    cur_state: str,
    mark: float | None,
    implied_ref: float | None = None,
    now: datetime | None = None,
) -> dict:
    """Pure gate + staleness check. Returns decision dict; caller persists state + shadow row.

    Keys in result: decision, reason, to_state, intended_signal, mark_price, advance,
    emit, is_trim, park, size_fraction, trigger_price.

    Two flags, deliberately separate. `advance` means the recorded stance moves to
    to_state. `emit` means orders must be sent. They line up for every full-position
    move, but a trim emits without advancing: it takes part of the position off and
    leaves the side unchanged.

    `implied_ref` is the market price at posted_at, used as the reference when the
    post cites none — without it a priceless signal has nothing to gate against.
    """
    asset = (rec.get("asset") or "").upper() or None
    conf  = rec.get("confidence") or 0.0
    ref   = rec.get("reference_price")

    def base(decision, reason, to, sig, advance, emit, **extra):
        out = {
            "decision": decision, "reason": reason,
            "to_state": to, "intended_signal": sig,
            "mark_price": mark, "advance": advance, "emit": emit,
            "is_trim": False, "park": False,
            "size_fraction": None, "trigger_price": None,
        }
        out.update(extra)
        return out

    def skip(reason, to=None, sig="none", **extra):
        return base("skipped", reason, to or cur_state, sig, False, False, **extra)

    def act(reason, to, sig):
        return base("acted", reason, to, sig, True, True)

    if conf < settings.confidence_floor:
        return skip("low_confidence")
    if not asset or asset not in WHITELIST:
        return skip("not_whitelisted")

    if rec.get("action_type") == "TRIM":
        return _evaluate_trim(rec, phase, cur_state, mark, now, base, skip)

    # A stop-only post moves no position. It still reaches the caller's stop
    # handling, which is keyed off the record, not off this decision.
    if rec.get("action_type") == "STOP":
        return skip("stop_only")

    tgt = _target_state(rec.get("action_type"), rec.get("direction"))
    if tgt is None:
        return skip("no_target")

    if tgt == cur_state:
        return skip("no_state_change", to=tgt)

    sig = _SIGNAL[(cur_state, tgt)]

    if phase == "backfill":
        return act("backfill_replay", tgt, sig)

    # ---- live path ----

    # Age backstop, before any price logic. A signal recovered by catchup hours
    # after the post is stale no matter what the price says.
    if _too_old(rec, now):
        return skip("signal_too_old", to=tgt, sig=sig)

    # No cited price: fall back to the market price at the moment of the post, so
    # the same staleness check still applies. Only when neither exists do we take
    # an ungated market entry — and by then the age gate above has already
    # established the signal is fresh.
    reason_ok = "ok"
    if ref is None:
        if settings.entry_on_missing_price != "market":
            return skip("priceless_no_entry", to=tgt, sig=sig)
        if implied_ref is None:
            return act("priceless_recent", tgt, sig)
        ref = implied_ref
        reason_ok = "ok_implied_ref"

    if mark is None:
        return skip("no_mark", to=tgt, sig=sig)

    moved = (mark - ref) / ref
    going_long = tgt == "LONG"
    chased = (moved > settings.staleness_pct) if going_long else (-moved > settings.staleness_pct)
    if chased:
        return skip("stale_price" if reason_ok == "ok" else "stale_implied_ref", to=tgt, sig=sig)

    return act(reason_ok, tgt, sig)
