"""The social state machine with two legs.

The change under test: an OPEN against an existing opposite position used to be a
flip — close one side, open the other — because that is all a net-mode account can
do. On a hedge account it is a second, independent trade. Everything else follows
from that: which leg a trim/add/stop/close is about, and what happens when the post
does not say.

Both modes are covered in every relevant case, because `multi=False` must keep
behaving exactly as the single-stance version did: that is what the live net-mode
account and the backtest replay both run on.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.legs import LONG, SHORT, Legs
from app.statemachine import evaluate, resolve_leg

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
FLAT = Legs()
LONG_ONLY = Legs(long=True)
SHORT_ONLY = Legs(short=True)
BOTH = Legs(long=True, short=True)


def rec(action="OPEN", direction=LONG, *, asset="BTC", conf=0.9, ref=None,
        age_s=10, **extra):
    r = {
        "channel_msg_id": 1,
        "asset": asset,
        "action_type": action,
        "direction": direction,
        "confidence": conf,
        "reference_price": ref,
        "posted_at": NOW - timedelta(seconds=age_s),
    }
    r.update(extra)
    return r


def dec(r, legs, *, multi, mark=60000.0, phase="live", implied=None):
    return evaluate(r, phase, legs, mark, implied, now=NOW, multi=multi)


# ── the core change: a second leg instead of a flip ──────────────────────────

def test_net_mode_flips_when_the_opposite_side_is_open():
    """Regression. On a net account the exchange nets the two anyway, so a plain
    OPEN of the other side is a reversal and must stay one."""
    d = dec(rec("OPEN", SHORT), LONG_ONLY, multi=False)
    assert d["intended_signal"] == "flip_to_short"
    assert d["advance_legs"] == {LONG: False, SHORT: True}
    assert d["to_state"] == "SHORT"


def test_hedge_mode_opens_a_second_leg_instead_of_flipping():
    """The whole point: the long stays on and a short is opened beside it."""
    d = dec(rec("OPEN", SHORT), LONG_ONLY, multi=True)
    assert d["intended_signal"] == "open_short"
    assert d["advance_legs"] == {SHORT: True}
    assert d["to_state"] == "LONG+SHORT"
    assert d["leg"] == SHORT


def test_hedge_mode_second_leg_works_the_other_way_round():
    d = dec(rec("OPEN", LONG), SHORT_ONLY, multi=True)
    assert d["intended_signal"] == "open_long"
    assert d["to_state"] == "LONG+SHORT"


def test_an_explicit_flip_still_closes_the_other_side_in_hedge_mode():
    """FLIP is the author saying he REVERSED. Turning that into a second leg would
    leave him holding a trade he told us he had exited."""
    d = dec(rec("FLIP", SHORT), LONG_ONLY, multi=True)
    assert d["intended_signal"] == "flip_to_short"
    assert d["advance_legs"] == {LONG: False, SHORT: True}
    assert d["to_state"] == "SHORT"


def test_first_entry_is_a_plain_open_in_both_modes():
    for multi in (False, True):
        d = dec(rec("OPEN", LONG), FLAT, multi=multi)
        assert d["intended_signal"] == "open_long"
        assert d["to_state"] == "LONG"


def test_opening_a_side_already_held_changes_nothing():
    for legs in (LONG_ONLY, BOTH):
        d = dec(rec("OPEN", LONG), legs, multi=True)
        assert d["decision"] == "skipped"
        assert d["reason"] == "no_state_change"
        assert d["advance_legs"] == {}


def test_an_open_with_no_direction_has_no_target():
    d = dec(rec("OPEN", None), FLAT, multi=True)
    assert d["reason"] == "no_target"


# ── which leg a management post means ────────────────────────────────────────

@pytest.mark.parametrize("legs,named,expected,refusal", [
    (LONG_ONLY,  None,  LONG,  None),              # only one leg: unambiguous
    (SHORT_ONLY, None,  SHORT, None),
    (BOTH,       LONG,  LONG,  None),              # named: unambiguous
    (BOTH,       SHORT, SHORT, None),
    (BOTH,       None,  None,  "side_ambiguous"),  # the case that must refuse
    (LONG_ONLY,  SHORT, None,  "side_not_open"),
    (FLAT,       None,  None,  "no_position"),
])
def test_resolve_leg(legs, named, expected, refusal):
    side, why = resolve_leg(rec(direction=named), legs)
    assert side == expected
    assert why == refusal


def test_trim_picks_the_named_leg_with_both_open():
    d = dec(rec("TRIM", SHORT, size_fraction=0.5), BOTH, multi=True)
    assert d["decision"] == "acted"
    assert d["intended_signal"] == "partial_close_short"
    assert d["leg"] == SHORT
    assert d["advance_legs"] == {}, "a trim reduces a leg, it does not close it"


def test_trim_refuses_when_it_cannot_tell_which_leg():
    """Shaving the wrong leg is not a near miss — it reduces the trade the author
    meant to keep and leaves the other one untouched."""
    d = dec(rec("TRIM", None, size_fraction=0.5), BOTH, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "trim_side_ambiguous"


def test_trim_with_one_leg_open_needs_no_direction():
    """Regression: this is every trim the listener has ever taken."""
    d = dec(rec("TRIM", None, size_fraction=0.5), SHORT_ONLY, multi=False)
    assert d["decision"] == "acted"
    assert d["intended_signal"] == "partial_close_short"


def test_add_refuses_when_it_cannot_tell_which_leg():
    """An add INCREASES exposure, so an ambiguous one doubles down on a side the
    author may not have meant."""
    d = dec(rec("ADD", None, add_multiple=0.5), BOTH, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "add_side_ambiguous"


def test_add_picks_the_named_leg():
    d = dec(rec("ADD", LONG, add_multiple=0.5), BOTH, multi=True)
    assert d["intended_signal"] == "add_long"
    assert d["leg"] == LONG


# ── an ADD with nothing open is an entry, not a scale-in ─────────────────────

def test_add_with_nothing_open_becomes_an_entry():
    """A trade can end without a post — a stop fires, and the next thing the channel
    says is "back to full size on the short". Found on 2026-08-07: msg 9821 said
    exactly that, the BTC short had been stopped out four days earlier, and the add
    was dropped for having nothing to scale into."""
    for multi in (False, True):
        d = dec(rec("ADD", SHORT, add_multiple=1.0, ref=60000.0), FLAT, multi=multi)
        assert d["decision"] == "acted"
        assert d["reason"] == "add_as_open:ok"
        assert d["intended_signal"] == "open_short"
        assert d["advance_legs"] == {SHORT: True}
        assert d["to_state"] == "SHORT"
        # An entry, so it is sized as one — not as a multiple of a standard entry.
        assert d["is_add"] is False


def test_add_with_nothing_open_and_no_named_side_is_still_refused():
    """With both legs flat there is nothing to infer a direction from, and guessing
    which way a fresh entry goes is not a near miss."""
    d = dec(rec("ADD", None, add_multiple=1.0), FLAT, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "no_position_to_add"


def test_add_to_a_side_not_open_while_the_other_runs_is_still_refused():
    """Only the flat case converts. Holding a long and being told to add to a short
    is a state disagreement, not a re-entry, and must not silently open a leg."""
    d = dec(rec("ADD", SHORT, add_multiple=1.0), LONG_ONLY, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "add_side_mismatch"


def test_an_add_turned_entry_keeps_every_entry_gate():
    stale = dec(rec("ADD", SHORT, add_multiple=1.0, age_s=100_000), FLAT, multi=True)
    assert stale["reason"] == "add_as_open:signal_too_old"

    chased = dec(rec("ADD", SHORT, add_multiple=1.0, ref=63000.0), FLAT,
                 multi=True, mark=59850.0)
    assert chased["reason"] == "add_as_open:stale_price"
    assert chased["advance_legs"] == {}


def test_an_add_that_can_still_scale_in_is_untouched():
    d = dec(rec("ADD", SHORT, add_multiple=1.0), SHORT_ONLY, multi=True)
    assert d["is_add"] is True
    assert d["intended_signal"] == "add_short"
    assert d["reason"] == "add_at_market"


def test_trim_on_a_leg_that_is_not_open_is_refused():
    d = dec(rec("TRIM", LONG, size_fraction=0.5), SHORT_ONLY, multi=True)
    assert d["reason"] == "trim_side_mismatch"


# ── closing ──────────────────────────────────────────────────────────────────

def test_close_names_its_leg_and_leaves_the_other_running():
    d = dec(rec("CLOSE", SHORT), BOTH, multi=True)
    assert d["intended_signal"] == "close_short"
    assert d["advance_legs"] == {SHORT: False}
    assert d["to_state"] == "LONG"


def test_close_with_one_leg_open_needs_no_direction():
    d = dec(rec("CLOSE", None), LONG_ONLY, multi=False)
    assert d["intended_signal"] == "close_long"
    assert d["to_state"] == "FLAT"


def test_a_sideless_close_with_both_legs_open_takes_everything():
    """"out" / "flat" / "all out" read as being out entirely, and reducing exposure
    is the direction this codebase picks when it must pick."""
    d = dec(rec("CLOSE", None), BOTH, multi=True)
    assert d["intended_signal"] == "close_all"
    assert d["advance_legs"] == {LONG: False, SHORT: False}
    assert d["to_state"] == "FLAT"


def test_closing_a_leg_that_is_not_open_is_refused():
    d = dec(rec("CLOSE", LONG), SHORT_ONLY, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "close_side_not_open"
    assert d["advance_legs"] == {}


def test_closing_with_nothing_open_changes_nothing():
    d = dec(rec("CLOSE", None), FLAT, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "no_state_change"


# ── the gates still apply, per leg ───────────────────────────────────────────

def test_a_stale_post_cannot_open_a_second_leg():
    d = dec(rec("OPEN", SHORT, age_s=100_000), LONG_ONLY, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "signal_too_old"
    assert d["advance_legs"] == {}


def test_the_chase_gate_still_applies_to_a_second_leg():
    """A short opened after price already fell 5% is the same chase it always was —
    hedge mode does not exempt it."""
    d = dec(rec("OPEN", SHORT, ref=63000.0), LONG_ONLY, multi=True, mark=59850.0)
    assert d["decision"] == "skipped"
    assert d["reason"] == "stale_price"


def test_low_confidence_is_refused_before_anything_else():
    d = dec(rec("OPEN", SHORT, conf=0.1), LONG_ONLY, multi=True)
    assert d["reason"] == "low_confidence"


def test_an_asset_off_the_whitelist_is_refused():
    d = dec(rec("OPEN", LONG, asset="DOGE"), FLAT, multi=True)
    assert d["reason"] == "not_whitelisted"


def test_backfill_replays_the_leg_change_without_price_gates():
    d = dec(rec("OPEN", SHORT, age_s=999_999), LONG_ONLY, multi=True,
            phase="backfill", mark=None)
    assert d["decision"] == "acted"
    assert d["reason"] == "backfill_replay"
    assert d["to_state"] == "LONG+SHORT"


def test_backfill_never_fires_a_trim_or_an_add():
    """Both would re-fire a real reduce or a real scale-in on every restart."""
    assert dec(rec("TRIM", LONG, size_fraction=0.5), LONG_ONLY, multi=True,
               phase="backfill")["reason"] == "backfill_no_trim"
    assert dec(rec("ADD", LONG, add_multiple=0.5), LONG_ONLY, multi=True,
               phase="backfill")["reason"] == "backfill_no_add"


# ── the audit shape ──────────────────────────────────────────────────────────

def test_from_and_to_state_describe_the_leg_set():
    d = dec(rec("OPEN", SHORT), LONG_ONLY, multi=True)
    assert d["from_state"] == "LONG"
    assert d["to_state"] == "LONG+SHORT"
    assert d["from_legs"] == LONG_ONLY
    assert d["to_legs"] == Legs(long=True, short=True)


def test_a_skip_leaves_the_leg_set_where_it_was():
    d = dec(rec("OPEN", LONG, conf=0.1), BOTH, multi=True)
    assert d["from_state"] == d["to_state"] == "LONG+SHORT"
    assert d["advance"] is False


def test_a_plain_stance_string_is_still_accepted():
    """The backtest replay hands in "LONG"/"SHORT"/"FLAT"."""
    d = evaluate(rec("OPEN", SHORT), "live", "LONG", 60000.0, None, now=NOW, multi=False)
    assert d["intended_signal"] == "flip_to_short"
    assert d["to_state"] == "SHORT"


# ── the flip case the channel replay found ───────────────────────────────────

def test_a_flip_to_a_side_already_held_still_closes_the_other_leg():
    """Found by replaying the real channel: msg 9795 (2026-07-29, FLIP LONG) landed
    on LONG+SHORT and decided nothing, leaving a short the author had said he was
    out of. The content of a flip is the half not yet done."""
    d = dec(rec("FLIP", LONG), BOTH, multi=True)
    assert d["decision"] == "acted"
    assert d["intended_signal"] == "close_short"
    assert d["advance_legs"] == {SHORT: False}
    assert d["to_state"] == "LONG"


def test_a_flip_to_the_only_side_held_really_does_nothing():
    """No opposite leg to shed, and the side is already on."""
    d = dec(rec("FLIP", LONG), LONG_ONLY, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "no_state_change"


def test_an_open_of_a_side_already_held_never_closes_the_other():
    """An OPEN is not a reversal. With both legs on it changes nothing — the author
    said he entered, not that he exited anything."""
    d = dec(rec("OPEN", LONG), BOTH, multi=True)
    assert d["decision"] == "skipped"
    assert d["reason"] == "no_state_change"
