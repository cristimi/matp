import logging
from datetime import datetime, timezone

from app.config import settings
from app.legs import LONG, SHORT, Legs, opposite

log = logging.getLogger(__name__)
WHITELIST = {s.strip().upper() for s in settings.asset_whitelist.split(",") if s.strip()}

# Which webhook signal opens or closes a given leg. A flip is still one signal
# carrying two steps (close the other side, open this one) — the emitter owns the
# order those go out in, and that has not changed.
_OPEN_SIGNAL  = {LONG: "open_long",  SHORT: "open_short"}
_CLOSE_SIGNAL = {LONG: "close_long", SHORT: "close_short"}
_FLIP_SIGNAL  = {LONG: "flip_to_long", SHORT: "flip_to_short"}

# A trim reduces an open leg without closing it, so it has no open/close signal —
# it emits, it does not move the leg's state.
_TRIM_SIGNAL = {LONG: "partial_close_long", SHORT: "partial_close_short"}
_ADD_SIGNAL  = {LONG: "add_long", SHORT: "add_short"}

# Both legs out at once. The emitter expands it into the two closes; it exists as
# one signal so a single post still produces a single audit row.
CLOSE_ALL = "close_all"


def _direction(rec: dict) -> str | None:
    d = (rec.get("direction") or "").upper()
    return d if d in (LONG, SHORT) else None


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


def _add_multiple(rec: dict) -> float:
    """The clamped size of a scale-in, in standard entries.

    A post that names no amount gets the default. The clamp bounds a single add;
    the cumulative ceiling across adds is applied by the caller, which is the only
    place that knows the position's current size.
    """
    raw = rec.get("add_multiple")
    try:
        mult = float(raw) if raw is not None else settings.default_add_multiple
    except (TypeError, ValueError):
        mult = settings.default_add_multiple
    return max(0.0, min(settings.max_add_multiple, mult))


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


def resolve_leg(rec: dict, legs: Legs) -> tuple[str | None, str | None]:
    """Which open leg a management post (TRIM / ADD / STOP / bare CLOSE) is about.

    Returns (side, refusal_reason). Exactly one of the two is None.

    Three cases, and the third is the one that matters. A post naming its side is
    unambiguous. A post naming none is unambiguous too, as long as only one leg is
    open. With BOTH legs open and no side named there is no honest answer — and
    acting on the wrong leg is not a near miss: it trims a trade the author meant to
    keep and leaves the one he meant to reduce untouched. So it refuses, and the
    reason says exactly that instead of hiding behind a generic skip.
    """
    named = _direction(rec)
    if named is not None:
        return (named, None) if legs.is_open(named) else (None, "side_not_open")
    if legs.flat:
        return None, "no_position"
    sole = legs.sole_open()
    if sole is not None:
        return sole, None
    return None, "side_ambiguous"


def evaluate_stop(
    rec: dict,
    side: str | None,
    entry_price: float | None,
    mark: float | None,
    last_stop: float | None = None,
    now: datetime | None = None,
) -> tuple[float | None, str]:
    """Where the stop should go for a post that manages an open leg.

    `side` is the leg being protected — LONG, SHORT, or None when no leg is open or
    the post could not be pinned to one. Returns (stop_price, reason). stop_price is
    None whenever nothing should be sent, and `reason` always says why.

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

    if side not in (LONG, SHORT):
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
    short = side == SHORT

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


def evaluate_take_profit(
    rec: dict,
    side: str | None,
    entry_price: float | None,
    mark: float | None,
    last_tp: float | None = None,
    now: datetime | None = None,
) -> tuple[float | None, str]:
    """Where the take-profit should go for one leg. Returns (tp_price, reason).

    A take-profit can only ever close a trade in profit, so it needs none of the
    tighten-only reasoning the stop guards carry. What it does need is to be a
    target rather than an instant exit: a level already on the wrong side of the
    mark would fill the moment it is placed.
    """
    want = rec.get("take_profit_price")
    if want is None:
        return None, "no_tp_instruction"
    if side not in (LONG, SHORT):
        return None, "no_position_for_tp"
    if _too_old(rec, now):
        return None, "signal_too_old"
    try:
        want = float(want)
    except (TypeError, ValueError):
        return None, "no_tp_instruction"

    short = side == SHORT

    # Profit sits below entry on a short. A "take-profit" on the losing side is a
    # misread level, not a target.
    if entry_price is not None and ((want >= float(entry_price)) if short
                                    else (want <= float(entry_price))):
        return None, "tp_wrong_side"

    # Already past the mark: placing it would fill immediately at market, turning a
    # target into an unplanned full exit.
    if mark is not None and ((want >= float(mark)) if short else (want <= float(mark))):
        return None, "tp_already_crossed"

    if last_tp is not None and abs(float(last_tp) - want) / max(want, 1e-9) < 1e-6:
        return None, "tp_unchanged"

    return want, "ok"


_ADD_REFUSAL = {
    "no_position":    "no_position_to_add",
    "side_not_open":  "add_side_mismatch",
    "side_ambiguous": "add_side_ambiguous",
}

_TRIM_REFUSAL = {
    "no_position":    "no_position_to_trim",
    "side_not_open":  "trim_side_mismatch",
    "side_ambiguous": "trim_side_ambiguous",
}


def _evaluate_add(rec, phase, legs, mark, now, base, skip) -> dict:
    """Scaling into a leg the trader already holds.

    Unlike a trim, an add INCREASES exposure, so it keeps every gate an entry has —
    including the `staleness_pct` chase gate. Adding late into a move that already
    ran is exactly the mistake that gate exists to prevent. And when it cannot tell
    which leg to grow it refuses: guessing here doubles down on the wrong side.
    """
    side, refusal = resolve_leg(rec, legs)
    if side is None:
        return skip(_ADD_REFUSAL[refusal])

    sig  = _ADD_SIGNAL[side]
    mult = _add_multiple(rec)
    marks = {"sig": sig, "leg": side, "add_multiple": mult}

    if mult <= 0:
        return skip("add_size_zero", **marks)

    # Backfill acts unconditionally; an add fired against an old post at every
    # restart would silently multiply real exposure.
    if phase == "backfill":
        return skip("backfill_no_add", **marks)

    if _too_old(rec, now):
        return skip("signal_too_old", **marks)

    ref = rec.get("reference_price")
    if ref is not None and mark is not None:
        moved = (float(mark) - float(ref)) / float(ref)
        chased = (moved > settings.staleness_pct) if side == LONG \
            else (-moved > settings.staleness_pct)
        if chased:
            return skip("stale_price", **marks)

    return base("acted", "add_at_market", None, sig, {}, True,
                leg=side, is_add=True, add_multiple=mult)


def _evaluate_trim(rec, phase, legs, mark, now, base, skip) -> dict:
    """A partial profit-take on one leg we are already recorded as holding.

    Deliberately NOT gated on `staleness_pct`. That gate stops us chasing an ENTRY
    that has already run away; a trim only ever reduces exposure, so refusing to
    bank profit because the price moved is the wrong failure direction — the same
    reasoning `partial_close` already gets in the AI engine's cooldown grouping.

    It IS gated on knowing which leg, though. "Reducing is the safe direction" is
    only true of the leg the author meant; shaving the other one is a plain error.
    """
    side, refusal = resolve_leg(rec, legs)
    if side is None:
        return skip(_TRIM_REFUSAL[refusal])

    sig  = _TRIM_SIGNAL[side]
    frac = _trim_fraction(rec)
    trig = rec.get("trigger_price")
    trig = float(trig) if trig is not None else None
    marks = {"sig": sig, "leg": side, "size_fraction": frac, "trigger_price": trig}

    # Backfill replays act unconditionally — right for rebuilding a stance out of
    # history, wrong for a trim, which has no stance to rebuild and would fire a
    # real reduce against an old post on every restart.
    if phase == "backfill":
        return skip("backfill_no_trim", **marks)

    if _too_old(rec, now):
        return skip("signal_too_old", **marks)

    def trim(reason):
        return base("acted", reason, None, sig, {}, True,
                    leg=side, is_trim=True, size_fraction=frac, trigger_price=trig)

    # No level named: the post presents the trim as happening now, at market.
    if trig is None:
        return trim("trim_at_market")

    if mark is None:
        return skip("no_mark", **marks)

    reached = (mark <= trig) if side == SHORT else (mark >= trig)
    if reached:
        return trim("trim_level_reached")

    # The trader named a level the market has not got to. Park it — taking profit
    # here would be at a worse price than the one they actually asked for.
    return base("pending", "trim_level_pending", None, sig, {}, False,
                leg=side, is_trim=True, park=True,
                size_fraction=frac, trigger_price=trig)


def _plan_close(rec, legs) -> tuple[str | None, str | None, dict, str | None]:
    """Which legs a CLOSE post takes off. Returns (signal, leg, advance_legs, refusal).

    With one leg open this is what it always was. With BOTH open it needs a rule,
    and the rule is: a named side closes that side; an unnamed one closes
    EVERYTHING. The words the extractor maps to CLOSE are "closed", "out", "flat",
    "all out", "done with it" — they read as being out entirely, and reducing
    exposure is the direction this codebase already picks when it has to pick (the
    trim gate says so in as many words). Being wrong this way costs a trade left
    early; being wrong the other way holds a position the author has abandoned.
    """
    if legs.flat:
        return None, None, {}, "no_state_change"

    named = _direction(rec)
    if named is not None:
        if not legs.is_open(named):
            return None, named, {}, "close_side_not_open"
        return _CLOSE_SIGNAL[named], named, {named: False}, None

    sole = legs.sole_open()
    if sole is not None:
        return _CLOSE_SIGNAL[sole], sole, {sole: False}, None

    return CLOSE_ALL, None, {LONG: False, SHORT: False}, None


def evaluate(
    rec: dict,
    phase: str,
    legs: Legs | str | None,
    mark: float | None,
    implied_ref: float | None = None,
    now: datetime | None = None,
    multi: bool = False,
) -> dict:
    """Pure gate + staleness check. Returns decision dict; caller persists state + shadow row.

    `legs` is what the channel is recorded as holding in this asset. A plain stance
    string ("LONG"/"SHORT"/"FLAT") is still accepted and read as a single leg, which
    is what the backtest replay hands in.

    `multi` says whether a SECOND leg may be opened — i.e. whether the execution
    account is in hedge mode. It is not a preference: on a net account the exchange
    nets the two against each other, so opening a second leg there would make the
    recorded state a lie. With multi=False an OPEN against an existing opposite leg
    stays a flip, exactly as before.

    Keys in the result: decision, reason, from_legs, to_legs, from_state, to_state,
    leg, advance_legs, intended_signal, mark_price, advance, emit, is_trim, is_add,
    park, size_fraction, trigger_price, add_multiple.

    `advance` means the recorded legs move as `advance_legs` says. `emit` means
    orders must be sent. They line up for every full-position move, but a trim emits
    without advancing: it takes part of a leg off and leaves it open.
    """
    if not isinstance(legs, Legs):
        legs = Legs.from_stance(legs)

    asset = (rec.get("asset") or "").upper() or None
    conf  = rec.get("confidence") or 0.0
    ref   = rec.get("reference_price")

    def base(decision, reason, to_legs, sig, advance_legs, emit, **extra):
        advance_legs = dict(advance_legs or {})
        if to_legs is None:
            to_legs = legs
            for s, is_open in advance_legs.items():
                to_legs = to_legs.with_side(s, is_open)
        out = {
            "decision": decision, "reason": reason,
            "from_legs": legs, "to_legs": to_legs,
            "from_state": legs.label(), "to_state": to_legs.label(),
            "leg": None, "advance_legs": advance_legs,
            "intended_signal": sig,
            "mark_price": mark, "advance": bool(advance_legs), "emit": emit,
            "is_trim": False, "is_add": False, "park": False,
            "size_fraction": None, "trigger_price": None, "add_multiple": None,
        }
        out.update(extra)
        return out

    def skip(reason, sig="none", **extra):
        return base("skipped", reason, legs, sig, {}, False, **extra)

    def act(reason, sig, advance_legs, **extra):
        return base("acted", reason, None, sig, advance_legs, True, **extra)

    if conf < settings.confidence_floor:
        return skip("low_confidence")
    if not asset or asset not in WHITELIST:
        return skip("not_whitelisted")

    action = rec.get("action_type")

    if action == "TRIM":
        return _evaluate_trim(rec, phase, legs, mark, now, base, skip)

    if action == "ADD":
        return _evaluate_add(rec, phase, legs, mark, now, base, skip)

    # A stop-only post moves no position. It still reaches the caller's stop
    # handling, which is keyed off the record, not off this decision.
    if action == "STOP":
        return skip("stop_only")

    if action == "CLOSE":
        sig, want, advance_legs, refusal = _plan_close(rec, legs)
        if refusal:
            return skip(refusal, leg=want)
        # A close has no direction to chase, so it keeps the gate it has always
        # had: the SHORT-side test, because the old single-stance code computed
        # `going_long = (target == "LONG")` and a close targeted "FLAT". Preserved
        # deliberately rather than quietly re-derived per leg — that is a change to
        # when the system exits trades, and it belongs in its own decision.
        going_long = False
    elif action in ("OPEN", "FLIP"):
        want = _direction(rec)
        if want is None:
            return skip("no_target")

        other = opposite(want)
        other_open = legs.is_open(other)

        if legs.is_open(want):
            # Already on this side. Growing it is what an ADD post is for — EXCEPT
            # for a flip, whose real content is the half we may not have done yet:
            # "I've reversed to long" while holding a long AND a short means the
            # short is gone. Found by replaying the channel: msg 9795 (2026-07-29,
            # FLIP LONG) landed on exactly this state and decided nothing at all,
            # leaving a short the author had told us he had exited.
            if action == "FLIP" and other_open:
                sig = _CLOSE_SIGNAL[other]
                advance_legs = {other: False}
                going_long = other == LONG
            else:
                return skip("no_state_change")
        else:
            # THE multi-position decision. A FLIP is the author saying he reversed,
            # so it closes the other side whatever the mode. A plain OPEN against an
            # existing opposite leg is only a reversal on a net account, where the
            # exchange would net them anyway; on a hedge account it is a second,
            # separate trade — the thing this whole feature exists to allow.
            if other_open and (action == "FLIP" or not multi):
                sig = _FLIP_SIGNAL[want]
                advance_legs = {other: False, want: True}
            else:
                sig = _OPEN_SIGNAL[want]
                advance_legs = {want: True}
            going_long = want == LONG
    else:
        return skip("no_target")

    if phase == "backfill":
        return act("backfill_replay", sig, advance_legs, leg=want)

    # ---- live path ----

    # Age backstop, before any price logic. A signal recovered by catchup hours
    # after the post is stale no matter what the price says.
    if _too_old(rec, now):
        return skip("signal_too_old", sig=sig, leg=want)

    # No cited price: fall back to the market price at the moment of the post, so
    # the same staleness check still applies. Only when neither exists do we take an
    # ungated market entry — and by then the age gate above has already established
    # the signal is fresh.
    reason_ok = "ok"
    if ref is None:
        if settings.entry_on_missing_price != "market":
            return skip("priceless_no_entry", sig=sig, leg=want)
        if implied_ref is None:
            return act("priceless_recent", sig, advance_legs, leg=want)
        ref = implied_ref
        reason_ok = "ok_implied_ref"

    if mark is None:
        return skip("no_mark", sig=sig, leg=want)

    moved = (mark - ref) / ref
    chased = (moved > settings.staleness_pct) if going_long \
        else (-moved > settings.staleness_pct)
    if chased:
        return skip("stale_price" if reason_ok == "ok" else "stale_implied_ref",
                    sig=sig, leg=want)

    return act(reason_ok, sig, advance_legs, leg=want)
