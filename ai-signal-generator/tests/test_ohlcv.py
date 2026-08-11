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

from app.data.ohlcv import _split_closed_candles, fetch_ohlcv, resolve_timeframe


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
    """Mirrors the slice of the ccxt async API that ohlcv.py actually uses.

    fetch_ohlcv() goes through load_markets_cached(), which calls
    fetch_markets() and hands the result to set_markets() — the fake predated
    that caching layer and only offered load_markets(), so market resolution
    blew up with AttributeError and fetch_ohlcv swallowed it into a None
    return.
    """
    id = 'binance'

    def __init__(self, raw):
        self.markets = {}
        self._raw = raw

    async def fetch_markets(self):
        return [{'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'type': 'spot'}]

    def set_markets(self, markets):
        self.markets = {m['symbol']: m for m in markets}

    async def fetch_ohlcv(self, symbol, timeframe, limit):
        self.asked_timeframe = timeframe
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
    # load_markets_cached memoizes per exchange_id at module scope — clear it so
    # this test resolves through the fake regardless of what ran before it.
    monkeypatch.setattr('app.data.ohlcv._markets_cache', {})
    monkeypatch.setattr('app.data.ohlcv._markets_locks', {})

    result = asyncio.run(fetch_ohlcv('binance', 'BTC/USDT', '15m', lookback_days=1))

    assert result is not None
    assert len(result['candles']) == 3          # raw, includes the forming candle
    assert len(result['closed_candles']) == 2    # forming candle dropped
    assert result['current_price'] == 108        # live price, from the forming candle
    assert result['closed_candles'][-1]['close'] == 104


# ── resolve_timeframe ───────────────────────────────────────────────────────
#
# A strategy's cycle_interval is a polling cadence that doubles as the candle
# timeframe. '10m' is selectable in the UI and no exchange lists a 10-minute
# candle, so hyperliquid answered every BTC fetch with 422 and the strategy ran
# with no price data at all. Unsupported cadences now round down to a real one.

class _Venue:
    """Just the attributes resolve_timeframe reads off a ccxt exchange."""
    def __init__(self, id_, timeframes):
        self.id = id_
        self.timeframes = {tf: tf for tf in timeframes}


_HYPERLIQUID = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '8h', '12h', '1d']


def test_supported_timeframe_passes_through():
    assert resolve_timeframe(_Venue('hyperliquid', _HYPERLIQUID), '15m') == '15m'


def test_10m_rounds_down_to_5m():
    # down, not up: a 5m candle has always closed by the next 10m wake, while a
    # 15m one would be handed back unchanged on every other cycle
    assert resolve_timeframe(_Venue('hyperliquid', _HYPERLIQUID), '10m') == '5m'


def test_rounds_down_to_nearest_the_venue_actually_has():
    sparse = _Venue('sparse', ['1m', '1h', '1d'])
    assert resolve_timeframe(sparse, '10m') == '1m'
    assert resolve_timeframe(sparse, '4h') == '1h'


def test_unknown_string_is_left_for_ccxt_to_reject():
    assert resolve_timeframe(_Venue('hyperliquid', _HYPERLIQUID), '7x') == '7x'


def test_no_shorter_candle_available_leaves_request_untouched():
    assert resolve_timeframe(_Venue('coarse', ['1d']), '5m') == '5m'


def test_fetch_ohlcv_asks_the_venue_for_a_candle_it_has(monkeypatch):
    now = time.time()
    raw = [
        [int((now - 1200) * 1000), 100, 100, 100, 100, 10],
        [int((now - 600) * 1000),  104, 104, 104, 104, 12],
    ]
    fake = _FakeExchange(raw)
    fake.timeframes = {tf: tf for tf in _HYPERLIQUID}
    monkeypatch.setattr('app.data.ohlcv._make_exchange', lambda ex_id: fake)
    monkeypatch.setattr('app.data.ohlcv._markets_cache', {})
    monkeypatch.setattr('app.data.ohlcv._markets_locks', {})

    result = asyncio.run(fetch_ohlcv('binance', 'BTC/USDT', '10m', lookback_days=1))

    assert result is not None                  # used to be None: 422 from the venue
    assert fake.asked_timeframe == '5m'
    assert result['timeframe'] == '5m'         # reported honestly, not as '10m'
