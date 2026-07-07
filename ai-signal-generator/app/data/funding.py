"""
Funding rate history (percentile + consecutive same-sign streak).

Async ccxt public-endpoint fetcher, modeled on sentiment.py::fetch_funding_rate
(same lifecycle: class lookup → enableRateLimit → load_markets →
resolve_ccxt_symbol → work → close() in finally; failures are non-fatal —
return None on error).

Uses fetch_funding_rate_history. Exchange limit caps vary — request the full
window but degrade to whatever the exchange returns rather than failing
(spec §7: "fall back to fewer days rather than failing").

Sentiment-class data: the caller stores this inside sentiment_data alongside
fear_greed/funding_rate/open_interest, and it renders inside the SENTIMENT
section — the deliberate exception to one-renderer-one-section.
"""

import asyncio
import logging

import ccxt.async_support as ccxt_async

from app.data.ohlcv import resolve_ccxt_symbol

logger = logging.getLogger(__name__)

MIN_SETTLEMENTS = 10  # below this a percentile is meaningless — return None


async def fetch_funding_history(
    exchange_id: str,
    symbol: str,
    days: int = 30,
) -> dict | None:
    """
    Fetch the trailing funding-rate history and rank the current rate.

    Returns:
        {'funding_percentile': float,   # current rate's rank vs trailing window, 0-100
         'funding_streak': int,         # consecutive same-sign settlements incl. latest
         'streak_direction': 'positive|negative'}
    or None on error/insufficient history.
    """
    exchange = None
    try:
        cls = getattr(ccxt_async, exchange_id, None)
        if cls is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        exchange = cls({'enableRateLimit': True})
        await exchange.load_markets()
        symbol = resolve_ccxt_symbol(exchange, symbol)

        since = exchange.milliseconds() - days * 86_400_000
        try:
            hist = await exchange.fetch_funding_rate_history(symbol, since=since)
        except Exception:
            # Some exchanges reject `since` — take whatever the default window gives.
            hist = await exchange.fetch_funding_rate_history(symbol)

        rates = [
            float(h['fundingRate']) for h in (hist or [])
            if h.get('fundingRate') is not None
        ]
        if len(rates) < MIN_SETTLEMENTS:
            return None

        current = rates[-1]
        percentile = round(sum(1 for r in rates if r <= current) / len(rates) * 100, 1)

        streak = 0
        sign = 1 if current >= 0 else -1
        for r in reversed(rates):
            if (1 if r >= 0 else -1) == sign:
                streak += 1
            else:
                break

        return {
            'funding_percentile': percentile,
            'funding_streak':     streak,
            'streak_direction':   'positive' if sign > 0 else 'negative',
        }

    except Exception as exc:
        logger.warning("fetch_funding_history error [%s %s]: %s", exchange_id, symbol, exc)
        return None

    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        result = await fetch_funding_history("binance", "BTC/USDT")
        print(json.dumps(result, indent=2))

    asyncio.run(_test())
