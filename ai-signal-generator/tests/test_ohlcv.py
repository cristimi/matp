"""
Unit tests for ohlcv.py — closed-candle-only filtering (Phase 2).

Indicators/geometry must never see a still-forming trailing candle: even after
Phase 1's candle-close-aligned scheduler wake, the exchange can already have
started accumulating trades into the next period by the time we fetch, so the
raw last candle isn't reliably closed. `_split_closed_candles` is the pure
filter; `fetch_ohlcv` wires it into a separate `closed_candles` field while
leaving `candles`/`current_price` on the live (possibly partial) data.
"""
import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.data.ohlcv import _split_closed_candles, fetch_ohlcv


def _candle(timestamp_s: float, close: float, volume: float = 10.0) -> dict:
    return {
        'timestamp': int(timestamp_s * 1000),
        'open': close, 'high': close, 'low': close, 'close': close,
        'volume': volume,
    }


# ── _split_closed_candles ───────────────────────────────────────────────────

def test_drops_trailing_partial_candle():
    now = time.time()
    tf_sec = 900  # 15m
    candles = [
        _candle(now - 3 * tf_sec, 100),   # closed long ago
        _candle(now - tf_sec - 5, 104),   # closed 5s ago
        _candle(now - 5, 108),            # opened 5s ago — still forming
    ]
    closed = _split_closed_candles(candles, '15m', now)
    assert len(closed) == 2
    assert closed[-1]['close'] == 104


def test_keeps_all_when_last_candle_already_closed():
    now = time.time()
    tf_sec = 900
    candles = [
        _candle(now - 2 * tf_sec, 100),
        _candle(now - tf_sec - 5, 104),  # closed 5s ago, no partial trailing candle
    ]
    closed = _split_closed_candles(candles, '15m', now)
    assert len(closed) == 2


def test_empty_input_returns_empty():
    assert _split_closed_candles([], '15m', time.time()) == []


def test_unknown_timeframe_falls_back_to_1h():
    now = time.time()
    candles = [_candle(now - 3700, 100)]  # closed 1h+ ago under the 3600s fallback
    closed = _split_closed_candles(candles, 'bogus', now)
    assert len(closed) == 1


# ── fetch_ohlcv integration: closed_candles vs candles/current_price ───────

class _FakeExchange:
    def __init__(self, raw):
        self.markets = {'BTC/USDT': {}}
        self._raw = raw

    async def load_markets(self):
        pass

    async def fetch_ohlcv(self, symbol, timeframe, limit):
        return self._raw

    async def close(self):
        pass


def test_fetch_ohlcv_separates_closed_candles_from_live_price(monkeypatch):
    now = time.time()
    tf_sec = 900
    raw = [
        [int((now - 3 * tf_sec) * 1000), 100, 100, 100, 100, 10],
        [int((now - tf_sec - 5) * 1000), 104, 104, 104, 104, 12],
        [int((now - 5) * 1000),          108, 108, 108, 108, 14],  # forming
    ]
    monkeypatch.setattr('app.data.ohlcv._make_exchange', lambda ex_id: _FakeExchange(raw))

    result = asyncio.run(fetch_ohlcv('binance', 'BTC/USDT', '15m', lookback_days=1))

    assert result is not None
    assert len(result['candles']) == 3          # raw, includes the forming candle
    assert len(result['closed_candles']) == 2    # forming candle dropped
    assert result['current_price'] == 108        # live price, from the forming candle
    assert result['closed_candles'][-1]['close'] == 104
