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


def _target_state(action_type: str, direction: str | None) -> str | None:
    if action_type == "CLOSE":
        return "FLAT"
    if action_type in ("OPEN", "FLIP"):
        if direction == "LONG":
            return "LONG"
        if direction == "SHORT":
            return "SHORT"
    return None


def evaluate(
    rec: dict,
    phase: str,
    cur_state: str,
    mark: float | None,
    implied_ref: float | None = None,
    now: datetime | None = None,
) -> dict:
    """Pure gate + staleness check. Returns decision dict; caller persists state + shadow row.

    Keys in result: decision, reason, to_state, intended_signal, mark_price, advance.
    advance=True means the state machine should advance to to_state.

    `implied_ref` is the market price at posted_at, used as the reference when the
    post cites none — without it a priceless signal has nothing to gate against.
    """
    asset = (rec.get("asset") or "").upper() or None
    conf  = rec.get("confidence") or 0.0
    ref   = rec.get("reference_price")

    def skip(reason, to=None, sig="none"):
        return {
            "decision": "skipped", "reason": reason,
            "to_state": to or cur_state, "intended_signal": sig,
            "mark_price": mark, "advance": False,
        }

    def act(reason, to, sig):
        return {
            "decision": "acted", "reason": reason,
            "to_state": to, "intended_signal": sig,
            "mark_price": mark, "advance": True,
        }

    if conf < settings.confidence_floor:
        return skip("low_confidence")
    if not asset or asset not in WHITELIST:
        return skip("not_whitelisted")

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
    posted_at = rec.get("posted_at")
    if posted_at is not None:
        age = ((now or datetime.now(timezone.utc)) - posted_at).total_seconds()
        if age > settings.max_signal_age_seconds:
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
