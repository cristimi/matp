"""
Sentiment data fetchers: Fear & Greed index, funding rate, open interest.
All functions are async. Failures are non-fatal — return None on error.
"""

import asyncio
import logging

import httpx
import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)


async def fetch_fear_greed() -> dict | None:
    """
    GET https://api.alternative.me/fng/?limit=1
    Returns: {'value': int, 'label': str}
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.alternative.me/fng/?limit=1")
            resp.raise_for_status()
            entry = resp.json()['data'][0]
            value = int(entry['value'])
            label = entry['value_classification']
            return {'value': value, 'label': label}
    except Exception as exc:
        logger.warning("fetch_fear_greed error: %s", exc)
        return None


def _funding_interpretation(rate: float) -> str:
    if rate > 0.0003:
        return 'longs paying shorts (overheated longs)'
    if rate < -0.0003:
        return 'shorts paying longs (overheated shorts)'
    return 'neutral'


async def fetch_funding_rate(exchange_id: str, symbol: str) -> dict | None:
    """
    Fetch current funding rate via ccxt async.
    Returns: {'rate': float, 'interpretation': str}
    """
    exchange = None
    try:
        cls = getattr(ccxt_async, exchange_id, None)
        if cls is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        exchange = cls({'enableRateLimit': True})
        await exchange.load_markets()

        data = await exchange.fetch_funding_rate(symbol)
        rate = float(data.get('fundingRate', 0) or 0)
        return {'rate': rate, 'interpretation': _funding_interpretation(rate)}

    except Exception as exc:
        logger.warning("fetch_funding_rate error [%s %s]: %s", exchange_id, symbol, exc)
        return None

    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass


async def fetch_open_interest(exchange_id: str, symbol: str) -> dict | None:
    """
    Fetch open interest and long/short ratio via ccxt async.
    Returns: {'open_interest_usd': float, 'change_24h_pct': float,
              'long_short_ratio': float|None, 'ls_interpretation': str}
    """
    exchange = None
    try:
        cls = getattr(ccxt_async, exchange_id, None)
        if cls is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        exchange = cls({'enableRateLimit': True})
        await exchange.load_markets()

        oi = await exchange.fetch_open_interest(symbol)
        oi_usd = float(
            oi.get('openInterestValue') or oi.get('openInterest') or 0
        )

        change_24h_pct = 0.0
        try:
            hist = await exchange.fetch_open_interest_history(symbol, '1d', limit=2)
            if hist and len(hist) >= 2:
                prev = float(hist[-2].get('openInterestValue') or 0)
                curr = float(hist[-1].get('openInterestValue') or 0)
                if prev > 0:
                    change_24h_pct = round((curr - prev) / prev * 100, 2)
        except Exception:
            pass

        long_short_ratio = None
        ls_interpretation = 'data unavailable'
        try:
            lsr = await exchange.fetch_long_short_ratio(symbol, '1d', limit=1)
            if lsr:
                ratio = float(lsr[-1].get('longShortRatio') or 0)
                long_short_ratio = round(ratio, 3)
                if ratio > 1.5:
                    ls_interpretation = 'longs dominant'
                elif ratio < 0.67:
                    ls_interpretation = 'shorts dominant'
                else:
                    ls_interpretation = 'balanced'
        except Exception:
            pass

        return {
            'open_interest_usd': oi_usd,
            'change_24h_pct':    change_24h_pct,
            'long_short_ratio':  long_short_ratio,
            'ls_interpretation': ls_interpretation,
        }

    except Exception as exc:
        logger.warning("fetch_open_interest error [%s %s]: %s", exchange_id, symbol, exc)
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
        fg = await fetch_fear_greed()
        print(f"Fear & Greed:  {fg}")

        fr = await fetch_funding_rate("binance", "BTC/USDT")
        print(f"Funding Rate:  {fr}")

        oi = await fetch_open_interest("binance", "BTC/USDT")
        print(f"Open Interest: {oi}")

    asyncio.run(_test())
