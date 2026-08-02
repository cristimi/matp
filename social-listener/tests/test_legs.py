"""Unit tests for the Legs value type.

Small, but the whole multi-position change rests on it: every "which side are we
on" question in the state machine now goes through here, and `sole_open` is what
decides whether a post that names no side is actionable or refused.
"""
import pytest

from app.legs import LONG, SHORT, Legs, opposite


def test_flat_by_default():
    legs = Legs()
    assert legs.flat
    assert legs.count == 0
    assert legs.open_sides == []
    assert legs.label() == "FLAT"
    assert legs.sole_open() is None


@pytest.mark.parametrize("side", [LONG, SHORT])
def test_one_leg_is_its_own_sole_open(side):
    legs = Legs.from_sides([side])
    assert legs.is_open(side)
    assert not legs.is_open(opposite(side))
    assert legs.sole_open() == side
    assert legs.label() == side


def test_both_legs_have_no_sole_open():
    """This is the case that makes a side-less management post unactionable."""
    legs = Legs(long=True, short=True)
    assert legs.count == 2
    assert legs.sole_open() is None
    assert legs.label() == "LONG+SHORT"


def test_label_matches_the_old_single_stance_spelling():
    """Historic shadow rows say LONG / SHORT / FLAT. New single-leg rows must read
    the same or the backtest's seed query silently stops matching."""
    assert Legs.from_stance("LONG").label() == "LONG"
    assert Legs.from_stance("SHORT").label() == "SHORT"
    assert Legs.from_stance("FLAT").label() == "FLAT"
    assert Legs.from_stance(None).label() == "FLAT"


def test_with_side_does_not_mutate_the_original():
    """Decisions build the resulting leg set from the current one; a shared mutable
    would let a skipped decision alter the state it was judged against."""
    before = Legs(long=True)
    after = before.with_side(SHORT, True)
    assert after.label() == "LONG+SHORT"
    assert before.label() == "LONG"


def test_closing_the_only_leg_gives_flat():
    assert Legs(short=True).with_side(SHORT, False).flat


def test_from_sides_ignores_junk_and_case():
    assert Legs.from_sides(["long"]).label() == "LONG"
    assert Legs.from_sides([]).flat
    assert Legs.from_sides(None).flat


def test_opposite():
    assert opposite(LONG) == SHORT
    assert opposite(SHORT) == LONG
