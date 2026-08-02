"""The handler wiring behind multi-position, end to end but offline.

The state machine decides; this file checks the decision is carried out against the
right leg. Every one of these was a place that used to say "the stance" and had to be
made to say which of the two positions it meant — the trim's size, the stop's
position, the parked trim's side, the leg rows written afterwards.

Telegram, the LLM, the exchange and the DB are all mocked. The record is fed in via
the already-extracted path (`already_seen` -> `load_signal`), which is the same path
a restart takes.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.legs import LONG, SHORT, Legs

NOW = datetime.now(timezone.utc)


def signal_rec(action="OPEN", direction=LONG, **extra):
    r = {
        "channel_msg_id": 100,
        "posted_at": NOW - timedelta(seconds=5),
        "asset": "BTC",
        "action_type": action,
        "direction": direction,
        "confidence": 0.9,
        "reference_price": None,
        "is_actionable": True,
        "size_fraction": None,
        "trigger_price": None,
        "stop_price": None,
        "stop_to_breakeven": None,
        "take_profit_price": None,
        "add_multiple": None,
    }
    r.update(extra)
    return r


class Recorder:
    """Collects what the handler did, so assertions read as behaviour not as mocks."""

    def __init__(self):
        self.emitted = []          # (signal, asset)
        self.leg_changes = []      # (asset, changes dict)
        self.cancelled = []        # (asset, side)
        self.parked = []           # pending trim payloads
        self.shadow = []           # shadow order rows
        self.levels = []           # (strategy, sl, tp, side)
        self.trim_positions = []   # (asset, side) open_position lookups for trims


async def _run(rec, legs, *, multi, strategy=None, position=None,
               mark=60000.0, phase="live"):
    """Drive _handle_locked for one already-extracted record."""
    from app import main as app_main

    r = Recorder()

    async def fake_emit(signal, asset, mk, strat, **kw):
        r.emitted.append((signal, asset))
        return True, f"ok:{signal}"

    async def fake_adjust(strat, sl_price=None, tp_price=None, dry_run=False, side=None):
        r.levels.append((sl_price, tp_price, side))
        return True, "confirmed"

    async def fake_open_position(strategy_id, asset, side=None):
        r.trim_positions.append((asset, side))
        if position is None:
            return None
        if side is not None and (position["side"] or "").lower() != side.lower():
            return None
        return position

    db_mocks = dict(
        already_shadow_evaluated=AsyncMock(return_value=False),
        already_seen=AsyncMock(return_value=True),
        load_signal=AsyncMock(return_value=rec),
        get_legs=AsyncMock(return_value=legs),
        insert_shadow_order=AsyncMock(side_effect=lambda row: r.shadow.append(row)),
        open_position=AsyncMock(side_effect=fake_open_position),
        trim_already_taken=AsyncMock(return_value=None),
        record_fired_trim=AsyncMock(),
        insert_pending_trim=AsyncMock(side_effect=lambda p, ttl: r.parked.append(p)),
        apply_leg_changes=AsyncMock(
            side_effect=lambda asset, changes, msg: r.leg_changes.append((asset, changes))),
        cancel_pending_trims=AsyncMock(
            side_effect=lambda asset, side, why: r.cancelled.append((asset, side)) or 1),
        get_levels=AsyncMock(return_value={"stop_price": None, "tp_price": None,
                                           "stop_mode": None}),
        set_levels=AsyncMock(),
        clear_stop_price=AsyncMock(),
    )

    with patch.multiple("app.main.db", **db_mocks), \
         patch("app.main.emitter.emit", AsyncMock(side_effect=fake_emit)), \
         patch("app.main.emitter.adjust_levels", AsyncMock(side_effect=fake_adjust)), \
         patch("app.main.emitter.standard_entry_size", lambda s, m: 0.001), \
         patch("app.main.marketdata.get_mark", AsyncMock(return_value=mark)), \
         patch("app.main.marketdata.get_close_at", AsyncMock(return_value=None)), \
         patch("app.main._STRATEGY", strategy or {"webhook_secret": "s",
                                                  "margin_per_trade": 10,
                                                  "default_leverage": 20}), \
         patch("app.main._MULTI_POSITION", multi):
        await app_main._handle_locked([], phase, rec["channel_msg_id"])

    return r


# ── opening a second leg ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hedge_open_against_a_long_emits_a_plain_open_and_keeps_the_long():
    r = await _run(signal_rec("OPEN", SHORT), Legs(long=True), multi=True)
    assert r.emitted == [("open_short", "BTC")]
    assert r.leg_changes == [("BTC", {SHORT: True})]
    assert r.cancelled == [], "the long leg is untouched, so its parked trims stand"
    assert r.shadow[0]["to_state"] == "LONG+SHORT"


@pytest.mark.asyncio
async def test_net_open_against_a_long_still_flips():
    """Regression: this is what the live account does today."""
    r = await _run(signal_rec("OPEN", SHORT), Legs(long=True), multi=False)
    assert r.emitted == [("flip_to_short", "BTC")]
    assert r.leg_changes == [("BTC", {LONG: False, SHORT: True})]
    assert r.cancelled == [("BTC", LONG)], "the long closed, so its parked trims go"
    assert r.shadow[0]["to_state"] == "SHORT"


# ── closing one leg of two ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closing_one_leg_leaves_the_other_and_only_cancels_its_own_trims():
    r = await _run(signal_rec("CLOSE", SHORT), Legs(long=True, short=True), multi=True)
    assert r.emitted == [("close_short", "BTC")]
    assert r.leg_changes == [("BTC", {SHORT: False})]
    assert r.cancelled == [("BTC", SHORT)]
    assert r.shadow[0]["to_state"] == "LONG"


@pytest.mark.asyncio
async def test_a_sideless_close_with_both_open_closes_both():
    r = await _run(signal_rec("CLOSE", None), Legs(long=True, short=True), multi=True)
    assert r.emitted == [("close_all", "BTC")]
    assert r.leg_changes == [("BTC", {LONG: False, SHORT: False})]
    assert sorted(r.cancelled) == [("BTC", LONG), ("BTC", SHORT)]
    assert r.shadow[0]["to_state"] == "FLAT"


# ── trimming the right leg ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_trim_sizes_itself_from_the_named_leg_not_from_whatever_is_newest():
    pos = {"id": "p1", "symbol": "BTC-USDT", "side": "short", "size": 0.004,
           "entry_price": 60000, "opened_at": NOW, "tp_price": None, "sl_price": None}
    r = await _run(signal_rec("TRIM", SHORT, size_fraction=0.5),
                   Legs(long=True, short=True), multi=True, position=pos)

    assert ("BTC", SHORT) in r.trim_positions, "the position lookup must name the leg"
    assert r.emitted == [("partial_close_short", "BTC")]
    assert r.leg_changes == [], "a trim reduces a leg, it does not close it"


@pytest.mark.asyncio
async def test_an_ambiguous_trim_sends_nothing():
    pos = {"id": "p1", "symbol": "BTC-USDT", "side": "short", "size": 0.004,
           "entry_price": 60000, "opened_at": NOW, "tp_price": None, "sl_price": None}
    r = await _run(signal_rec("TRIM", None, size_fraction=0.5),
                   Legs(long=True, short=True), multi=True, position=pos)

    assert r.emitted == []
    assert r.shadow[0]["reason"] == "trim_side_ambiguous"


@pytest.mark.asyncio
async def test_a_parked_trim_records_the_leg_it_belongs_to():
    """A level the market has not reached is parked. It must carry its own side, or
    the watcher fires it against the other leg."""
    pos = {"id": "p1", "symbol": "BTC-USDT", "side": "long", "size": 0.004,
           "entry_price": 60000, "opened_at": NOW, "tp_price": None, "sl_price": None}
    r = await _run(signal_rec("TRIM", LONG, size_fraction=0.5, trigger_price=70000.0),
                   Legs(long=True, short=True), multi=True, position=pos, mark=60000.0)

    assert len(r.parked) == 1
    assert r.parked[0]["side"] == LONG
    assert r.parked[0]["trigger_price"] == 70000.0
    assert r.emitted == []


# ── stops land on the right leg ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_stop_on_a_named_leg_is_sent_with_that_side():
    """order-listener resolves the position by side; without it the stop lands on
    whichever leg opened most recently."""
    pos = {"id": "p1", "symbol": "BTC-USDT", "side": "short", "size": 0.004,
           "entry_price": 60000, "opened_at": NOW, "tp_price": None, "sl_price": None}
    rec = signal_rec("STOP", SHORT, stop_price=59000.0, is_actionable=False)
    r = await _run(rec, Legs(long=True, short=True), multi=True, position=pos,
                   mark=58000.0)

    assert len(r.levels) == 1
    sl, tp, side = r.levels[0]
    assert side == SHORT
    assert sl == 59000.0


@pytest.mark.asyncio
async def test_an_unattributable_stop_is_not_sent_at_all():
    """With both legs open and no side named there is no honest answer, and moving
    the wrong leg's stop is how a protected trade becomes an unprotected one."""
    pos = {"id": "p1", "symbol": "BTC-USDT", "side": "short", "size": 0.004,
           "entry_price": 60000, "opened_at": NOW, "tp_price": None, "sl_price": None}
    rec = signal_rec("STOP", None, stop_price=59000.0, is_actionable=False)
    r = await _run(rec, Legs(long=True, short=True), multi=True, position=pos,
                   mark=58000.0)

    assert r.levels == []
    assert r.shadow[0]["stop_reason"] == "no_position_for_stop"


@pytest.mark.asyncio
async def test_a_stop_with_one_leg_open_still_needs_no_side_named():
    """Regression: every levels-only post the listener has ever applied."""
    pos = {"id": "p1", "symbol": "BTC-USDT", "side": "short", "size": 0.004,
           "entry_price": 60000, "opened_at": NOW, "tp_price": None, "sl_price": None}
    rec = signal_rec("STOP", None, stop_price=59000.0, is_actionable=False)
    r = await _run(rec, Legs(short=True), multi=False, position=pos, mark=58000.0)

    assert len(r.levels) == 1
    assert r.levels[0][2] == SHORT
