"""
OHLCV data fetcher using ccxt async.
"""

import asyncio
import logging
from datetime import datetime, timezone

import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)

_TIMEFRAME_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '8h': 28800, '1d': 86400,
}


def _candles_needed(timeframe: str, lookback_days: int) -> int:
    tf_sec = _TIMEFRAME_SECONDS.get(timeframe, 3600)
    return max(200, (lookback_days * 86400) // tf_sec + 50)


def _make_exchange(exchange_id: str):
    cls = getattr(ccxt_async, exchange_id, None)
    if cls is None:
        raise ValueError(f"Unknown ccxt exchange: {exchange_id}")
    return cls({'enableRateLimit': True})


async def fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    lookback_days: int,
) -> dict | None:
    """
    Fetch OHLCV candles for the given exchange and symbol.

    Returns dict with keys:
        symbol, timeframe, candles (list of dicts), current_price,
        price_change_24h_pct, price_change_7d_pct
    Returns None on any error.
    """
    exchange = None
    try:
        exchange = _make_exchange(exchange_id)
        await exchange.load_markets()

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

        current_price = candles[-1]['close']
        tf_sec = _TIMEFRAME_SECONDS.get(timeframe, 3600)
        candles_per_day = max(1, 86400 // tf_sec)

        idx_24h = max(0, len(candles) - candles_per_day)
        idx_7d  = max(0, len(candles) - candles_per_day * 7)

        price_24h_ago = candles[idx_24h]['close']
        price_7d_ago  = candles[idx_7d]['close']

        pct_24h = (current_price - price_24h_ago) / price_24h_ago * 100 if price_24h_ago else 0.0
        pct_7d  = (current_price - price_7d_ago)  / price_7d_ago  * 100 if price_7d_ago  else 0.0

        return {
            'symbol':              symbol,
            'timeframe':           timeframe,
            'candles':             candles,
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
