import logging

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


def evaluate(rec: dict, phase: str, cur_state: str, mark: float | None) -> dict:
    """Pure gate + staleness check. Returns decision dict; caller persists state + shadow row.

    Keys in result: decision, reason, to_state, intended_signal, mark_price, advance.
    advance=True means the state machine should advance to to_state.
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

    # live path: check staleness
    if ref is None:
        if settings.entry_on_missing_price == "market":
            return act("priceless_market", tgt, sig)
        return skip("priceless_no_entry", to=tgt, sig=sig)

    if mark is None:
        return skip("no_mark", to=tgt, sig=sig)

    moved = (mark - ref) / ref
    going_long = tgt == "LONG"
    chased = (moved > settings.staleness_pct) if going_long else (-moved > settings.staleness_pct)
    if chased:
        return skip("stale_price", to=tgt, sig=sig)

    return act("ok", tgt, sig)
