"""
Unit tests for node_ingest._unrealized_pnl_pct.

The schedulers seed position_unrealized_pnl_pct as None (no price is known when
initial state is built) and ingest fills it in. Before this was wired up the
prompt rendered "Current P&L: N/A%" on every exit-evaluation cycle.

Unit is the price move in the position's favour — the same unit the model sets
stop_loss_pct/take_profit_pct in — not the leveraged return on margin.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.graph.nodes.node_ingest import _unrealized_pnl_pct


def _state(**kw):
    st = {
        'position_open': True,
        'position_side': 'long',
        'position_entry_price': 100.0,
        'position_unrealized_pnl_pct': None,
    }
    st.update(kw)
    return st


def _ohlcv(price):
    return {'current_price': price}


def test_long_in_profit():
    assert _unrealized_pnl_pct(_state(), _ohlcv(102.0)) == 2.0


def test_long_in_loss():
    assert _unrealized_pnl_pct(_state(), _ohlcv(99.0)) == -1.0


def test_short_in_profit():
    # Price down on a short is a gain — sign is flipped, not the magnitude.
    assert _unrealized_pnl_pct(_state(position_side='short'), _ohlcv(98.0)) == 2.0


def test_short_in_loss():
    assert _unrealized_pnl_pct(_state(position_side='short'), _ohlcv(101.5)) == -1.5


def test_flat_at_entry():
    assert _unrealized_pnl_pct(_state(), _ohlcv(100.0)) == 0.0


def test_matches_take_profit_unit():
    # A long entered at 100 with take_profit_pct=2.0 targets 102.0; at 101.0 the
    # model must be able to read "half the target" straight off this number.
    assert _unrealized_pnl_pct(_state(), _ohlcv(101.0)) == 1.0


def test_no_position_returns_incoming():
    assert _unrealized_pnl_pct(_state(position_open=False), _ohlcv(120.0)) is None


def test_missing_price_stays_none():
    # Must not fabricate 0.0% — N/A is the honest render when price is absent.
    assert _unrealized_pnl_pct(_state(), None) is None
    assert _unrealized_pnl_pct(_state(), {}) is None


def test_missing_entry_stays_none():
    assert _unrealized_pnl_pct(_state(position_entry_price=None), _ohlcv(102.0)) is None


def test_zero_entry_price_stays_none():
    # Guards the division; a 0 entry is corrupt state, not a 100% move.
    assert _unrealized_pnl_pct(_state(position_entry_price=0.0), _ohlcv(102.0)) is None


def test_non_numeric_entry_does_not_raise():
    assert _unrealized_pnl_pct(_state(position_entry_price='abc'), _ohlcv(102.0)) is None


def test_rounds_to_three_places():
    assert _unrealized_pnl_pct(_state(), _ohlcv(100.123456)) == 0.123
