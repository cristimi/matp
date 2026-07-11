"""
OHLCV data fetcher using ccxt async.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)

# node_ingest fans out ~8 concurrent fetchers per cycle, each spinning up its
# own ccxt instance and calling load_markets() — multiplied across strategies
# that share a candle-close boundary (most run hourly), that's dozens of
# simultaneous load_markets() hits on the same exchange, slow enough under
# load to blow past ccxt's 10s timeout and surface to callers as missing data.
# A market list changes rarely, so cache it process-wide and hand every new
# exchange instance the cached copy via set_markets() instead of refetching.
_MARKETS_TTL   = 3600  # seconds
_markets_cache: dict[str, tuple[float, list]] = {}
_markets_locks: dict[str, asyncio.Lock] = {}


async def load_markets_cached(exchange, exchange_id: str) -> None:
    """Populate exchange.markets from the shared cache, refetching at most
    once per exchange per _MARKETS_TTL window (concurrent callers during a
    refetch share the one in-flight request via the per-exchange lock)."""
    cached = _markets_cache.get(exchange_id)
    if cached and time.monotonic() - cached[0] < _MARKETS_TTL:
        exchange.set_markets(cached[1])
        return

    lock = _markets_locks.setdefault(exchange_id, asyncio.Lock())
    async with lock:
        cached = _markets_cache.get(exchange_id)
        if cached and time.monotonic() - cached[0] < _MARKETS_TTL:
            exchange.set_markets(cached[1])
            return
        raw_markets = await exchange.fetch_markets()
        _markets_cache[exchange_id] = (time.monotonic(), raw_markets)
        exchange.set_markets(raw_markets)

_TIMEFRAME_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '8h': 28800, '1d': 86400,
}


def _candles_needed(timeframe: str, lookback_days: int) -> int:
    tf_sec = _TIMEFRAME_SECONDS.get(timeframe, 3600)
    return max(200, (lookback_days * 86400) // tf_sec + 50)


def _split_closed_candles(candles: list[dict], timeframe: str, now_epoch: float) -> list[dict]:
    """Drop any trailing candle(s) whose period hasn't closed yet.

    Exchanges return the still-forming current-period candle as the last entry
    once it has any trades, even right after our candle-close-aligned wake
    (Phase 1's buffer only guarantees the *previous* period is final, not that
    the exchange hasn't already started accumulating the next one). Indicators
    and geometry must not see that mutable last candle, or a pattern/level can
    appear and vanish between cycles as it fills in.
    """
    tf_sec = _TIMEFRAME_SECONDS.get(timeframe, 3600)
    return [c for c in candles if (c['timestamp'] / 1000 + tf_sec) <= now_epoch]


def _make_exchange(exchange_id: str):
    cls = getattr(ccxt_async, exchange_id, None)
    if cls is None:
        raise ValueError(f"Unknown ccxt exchange: {exchange_id}")
    return cls({'enableRateLimit': True, 'timeout': 25000})


def resolve_ccxt_symbol(exchange, symbol: str) -> str:
    """Return the symbol as listed in this exchange's loaded markets.

    Resolution order:
      1. Exact spot match (BASE/QUOTE)
      2. Linear perp with same settle currency (BASE/QUOTE:QUOTE)
      3. Any swap/future with matching base asset (for exchanges whose settle differs, e.g. HL BTC/USDC:USDC)
    Raises ValueError if nothing is found.
    """
    if symbol in exchange.markets:
        return symbol
    parts = symbol.split('/')
    if len(parts) == 2:
        base, quote = parts
        linear = f"{base}/{quote}:{quote}"
        if linear in exchange.markets:
            return linear
        candidates = sorted(
            s for s, m in exchange.markets.items()
            if m.get('base') == base and m.get('type') in ('swap', 'future')
        )
        if candidates:
            return candidates[0]
    raise ValueError(f"{exchange.id} does not have market symbol {symbol!r}")


async def fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    lookback_days: int,
) -> dict | None:
    """
    Fetch OHLCV candles for the given exchange and symbol.

    Returns dict with keys:
        symbol, timeframe, candles (list of dicts, may include a still-forming
        trailing candle), closed_candles (candles with any not-yet-closed
        trailing candle dropped — use this for indicators/geometry),
        current_price, price_change_24h_pct, price_change_7d_pct
    Returns None on any error.
    """
    exchange = None
    try:
        exchange = _make_exchange(exchange_id)
        await load_markets_cached(exchange, exchange_id)
        symbol = resolve_ccxt_symbol(exchange, symbol)

        # Request enough candles for all indicators (EMA200 needs 200+).
        # Do NOT pass `since` — exchanges cap limit (Binance: 1000) so a
        # far-back `since` makes the window end in the past, giving stale
        # "current price". Without `since`, the exchange returns the most
        # recent N candles ending at now.
        limit = max(500, _candles_needed(timeframe, lookback_days))

        raw = await exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, limit=limit
        )
        if not raw:
            logger.warning("fetch_ohlcv: no data for %s %s %s", exchange_id, symbol, timeframe)
            return None

        candles = [
            {
                'timestamp': c[0],
                'open':      c[1],
                'high':      c[2],
                'low':       c[3],
                'close':     c[4],
                'volume':    c[5],
            }
            for c in raw
        ]

        # current_price stays the freshest trade price available, even if that
        # comes from a still-forming candle — execution/sizing needs live price,
        # not a stale closed-candle close.
        current_price = candles[-1]['close']

        now_epoch = datetime.now(timezone.utc).timestamp()
        closed_candles = _split_closed_candles(candles, timeframe, now_epoch)

        tf_sec = _TIMEFRAME_SECONDS.get(timeframe, 3600)
        candles_per_day = max(1, 86400 // tf_sec)

        idx_24h = max(0, len(closed_candles) - candles_per_day)
        idx_7d  = max(0, len(closed_candles) - candles_per_day * 7)

        price_24h_ago = closed_candles[idx_24h]['close'] if closed_candles else current_price
        price_7d_ago  = closed_candles[idx_7d]['close']  if closed_candles else current_price

        pct_24h = (current_price - price_24h_ago) / price_24h_ago * 100 if price_24h_ago else 0.0
        pct_7d  = (current_price - price_7d_ago)  / price_7d_ago  * 100 if price_7d_ago  else 0.0

        return {
            'symbol':              symbol,
            'timeframe':           timeframe,
            'candles':             candles,
            'closed_candles':      closed_candles,
            'current_price':       current_price,
            'price_change_24h_pct': round(pct_24h, 2),
            'price_change_7d_pct':  round(pct_7d,  2),
        }

    except Exception as exc:
        logger.warning("fetch_ohlcv error [%s %s %s]: %s", exchange_id, symbol, timeframe, exc)
        return None

    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        result = await fetch_ohlcv("binance", "BTC/USDT", "4h", 7)
        if result:
            print(f"symbol:              {result['symbol']}")
            print(f"timeframe:           {result['timeframe']}")
            print(f"candles fetched:     {len(result['candles'])}")
            print(f"current_price:       {result['current_price']}")
            print(f"price_change_24h:    {result['price_change_24h_pct']}%")
            print(f"price_change_7d:     {result['price_change_7d_pct']}%")
            print(f"last candle:         {result['candles'][-1]}")
        else:
            print("FAIL: No data returned")

    asyncio.run(_test())
