"""
Unit tests for node_guard risk-unit sizing (_resolve_entry_sizing) and the
wrong-side stop validation on adjust_stops / amend_order.

No DB: get_pool is monkeypatched with a fake whose cooldown lookup returns
None (no recent action).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.graph.nodes import node_guard as ng


class FakeConn:
    async def fetchval(self, *_a):
        return None  # no cooldown hit


class FakePool:
    def acquire(self):
        class _Ctx:
            async def __aenter__(self):
                return FakeConn()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _state(action, sc_extra=None, **kw):
    sc = {
        'default_leverage': 20, 'margin_per_trade': 10.0,
        'confidence_threshold': 0.5, 'cooldown_entry_minutes': 0,
        **(sc_extra or {}),
    }
    signal = {
        'action': action, 'confidence': 0.9, 'size_pct': 100.0,
        'stop_loss_pct': kw.pop('sl_pct', 1.0), 'take_profit_pct': kw.pop('tp_pct', 2.0),
        'reasoning': 't',
        **kw.pop('signal_extra', {}),
    }
    return {
        'strategy_id': 'test', 'strategy_config': sc, 'risk_config': {},
        'llm_signal': signal, 'position_open': kw.pop('position_open', False),
        'position_side': kw.pop('position_side', None),
        'ohlcv_data': {'current_price': kw.pop('price', 100.0)},
        'open_orders': kw.pop('open_orders', []),
        **kw,
    }


def _run_guard(state, monkeypatch):
    monkeypatch.setattr(ng, 'get_pool', lambda: FakePool())
    return asyncio.run(ng.node_guard(state))


# ── _resolve_entry_sizing ─────────────────────────────────────────────────────

def test_margin_mode_matches_legacy_formula():
    qty, meta = ng._resolve_entry_sizing(
        {'default_leverage': 20, 'margin_per_trade': 10.0}, 100.0, 1.0)
    assert qty == round((10.0 * 20) / 100.0, 4)   # 2.0 — notional $200
    assert meta['sizing_mode'] == 'margin'
    assert meta['risk_at_sl_usd'] == 2.0          # 1% of $200


def test_risk_mode_sizes_to_target_risk():
    sc = {'default_leverage': 20, 'margin_per_trade': 50.0,
          'sizing_mode': 'risk', 'risk_per_trade': 10.0}
    qty, meta = ng._resolve_entry_sizing(sc, 100.0, 1.0)
    # notional = 10 / 0.01 = $1000 (cap 50*20=1000 — exactly at cap, not over)
    assert qty == 10.0
    assert meta['sizing_mode'] == 'risk'
    assert meta['effective_risk_usd'] == 10.0
    assert meta['target_risk_usd'] == 10.0
    assert meta['risk_clamped_by_margin_cap'] is False
    assert meta['margin_usd'] == 50.0


def test_risk_mode_clamped_by_margin_cap():
    sc = {'default_leverage': 20, 'margin_per_trade': 10.0,   # cap notional $200
          'sizing_mode': 'risk', 'risk_per_trade': 10.0}      # wants $1000
    qty, meta = ng._resolve_entry_sizing(sc, 100.0, 1.0)
    assert qty == 2.0                                          # capped at $200
    assert meta['risk_clamped_by_margin_cap'] is True
    assert meta['effective_risk_usd'] == 2.0                   # 1% of $200
    assert meta['margin_usd'] == 10.0


def test_risk_mode_wide_stop_small_notional():
    sc = {'default_leverage': 10, 'margin_per_trade': 100.0,
          'sizing_mode': 'risk', 'risk_per_trade': 10.0}
    qty, meta = ng._resolve_entry_sizing(sc, 100.0, 5.0)
    # notional = 10 / 0.05 = $200 << cap $1000 — wide stop → small position
    assert qty == 2.0
    assert meta['effective_risk_usd'] == 10.0
    assert meta['margin_usd'] == 20.0


# ── node_guard integration ────────────────────────────────────────────────────

def test_open_long_risk_mode_end_to_end(monkeypatch):
    st = _state('open_long',
                sc_extra={'sizing_mode': 'risk', 'risk_per_trade': 5.0,
                          'margin_per_trade': 50.0},
                sl_pct=1.0, tp_pct=2.0, price=100.0)
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is True
    assert out['resolved_size'] == 5.0          # $500 notional = 5 / 0.01 / 100
    assert out['sizing_meta']['effective_risk_usd'] == 5.0
    assert out['resolved_sl_price'] == 99.0
    assert out['resolved_tp_price'] == 102.0


def test_open_long_margin_mode_unchanged(monkeypatch):
    st = _state('open_long', sl_pct=1.0, price=100.0)
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is True
    assert out['resolved_size'] == 2.0          # 10 * 20 / 100 — legacy formula
    assert out['sizing_meta']['sizing_mode'] == 'margin'


def test_place_limit_risk_mode(monkeypatch):
    st = _state('place_limit_long',
                sc_extra={'sizing_mode': 'risk', 'risk_per_trade': 5.0,
                          'margin_per_trade': 50.0},
                sl_pct=2.0, signal_extra={'limit_price': 50.0})
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is True
    # notional = 5 / 0.02 = $250 → qty 5.0 at price 50
    assert out['resolved_size'] == 5.0
    assert out['sizing_meta']['effective_risk_usd'] == 5.0


# ── adjust_stops side validation ──────────────────────────────────────────────

def test_adjust_stops_long_sl_above_price_rejected(monkeypatch):
    st = _state('adjust_stops', position_open=True, position_side='long',
                price=100.0, signal_extra={'new_sl_price': 101.0})
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is False
    assert out['gate_rejection_reason'] == 'stop_wrong_side'


def test_adjust_stops_long_valid_passes(monkeypatch):
    st = _state('adjust_stops', position_open=True, position_side='long',
                price=100.0, signal_extra={'new_sl_price': 98.0, 'new_tp_price': 105.0})
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is True
    assert out['resolved_sl_price'] == 98.0


def test_adjust_stops_short_tp_above_price_rejected(monkeypatch):
    st = _state('adjust_stops', position_open=True, position_side='short',
                price=100.0, signal_extra={'new_tp_price': 103.0})
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is False
    assert out['gate_rejection_reason'] == 'stop_wrong_side'


# ── amend_order side validation ───────────────────────────────────────────────

def _amend_state(limit, sl=None, tp=None, side='buy', order_id='abc'):
    extra = {'target_order_id': 'abc', 'limit_price': limit}
    if sl is not None:
        extra['new_sl_price'] = sl
    if tp is not None:
        extra['new_tp_price'] = tp
    return _state('amend_order', position_open=False,
                  open_orders=[{'order_id': order_id, 'side': side}],
                  signal_extra=extra)


def test_amend_buy_tp_below_limit_rejected(monkeypatch):
    # The 2026-07-10 ETH incident shape: long entry amended to 1796.81 with
    # TP 1783.11 below it.
    st = _amend_state(limit=1796.81, sl=1755.34, tp=1783.11, side='buy')
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is False
    assert out['gate_rejection_reason'] == 'stop_wrong_side'


def test_amend_buy_valid_passes(monkeypatch):
    st = _amend_state(limit=100.0, sl=98.0, tp=104.0, side='buy')
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is True
    assert out['resolved_limit_price'] == 100.0
    assert out['resolved_sl_price'] == 98.0


def test_amend_sell_sl_below_limit_rejected(monkeypatch):
    st = _amend_state(limit=100.0, sl=99.0, side='sell')
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is False
    assert out['gate_rejection_reason'] == 'stop_wrong_side'


def test_amend_unknown_order_skips_side_validation(monkeypatch):
    # Order not in open_orders context — side unknown, validation skipped.
    st = _amend_state(limit=100.0, sl=101.0, side='buy', order_id='other-id')
    out = _run_guard(st, monkeypatch)
    assert out['gate_passed'] is True
