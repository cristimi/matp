"""
Macro data fetchers: BTC dominance (CoinGecko), DXY and US10Y (yfinance).
All functions are async. Failures are non-fatal — return None on error.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import yfinance as yf

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='yf')


async def fetch_btc_dominance() -> dict | None:
    """
    Fetch BTC market dominance from CoinGecko /global endpoint.
    Returns: {'btc_dominance': float, 'btc_dom_trend': str}
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.coingecko.com/api/v3/global")
            resp.raise_for_status()
            data = resp.json().get('data', {})
            pct = data.get('market_cap_percentage', {}).get('btc', 0)
            dominance = round(float(pct), 2)
            return {'btc_dominance': dominance, 'btc_dom_trend': 'stable'}
    except Exception as exc:
        logger.warning("fetch_btc_dominance error: %s", exc)
        return None


def _yfinance_fetch(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period='5d', interval='1d')
        if hist.empty or len(hist) < 2:
            return None
        current = float(hist['Close'].iloc[-1])
        prev    = float(hist['Close'].iloc[-2])
        change  = current - prev
        trend   = 'rising' if change > 0 else ('falling' if change < 0 else 'flat')
        return {'value': round(current, 4), 'trend': trend}
    except Exception as exc:
        logger.warning("yfinance error [%s]: %s", ticker, exc)
        return None


async def fetch_macro() -> dict | None:
    """
    Fetch DXY (US Dollar Index) and US10Y (10-year Treasury yield) via yfinance.
    Returns: {'dxy': float, 'dxy_trend': str, 'us10y': float, 'us10y_trend': str}
    Returns None if both fetches fail.
    """
    loop = asyncio.get_event_loop()
    try:
        # DX-Y.NYB = ICE US Dollar Index (more reliable than ^DXY in yfinance)
        dxy_fut   = loop.run_in_executor(_executor, _yfinance_fetch, 'DX-Y.NYB')
        us10y_fut = loop.run_in_executor(_executor, _yfinance_fetch, '^TNX')

        dxy_data, us10y_data = await asyncio.gather(dxy_fut, us10y_fut)

        if dxy_data is None and us10y_data is None:
            logger.warning("fetch_macro: both DXY and US10Y unavailable")
            return None

        result: dict = {}
        if dxy_data:
            result['dxy']       = dxy_data['value']
            result['dxy_trend'] = dxy_data['trend']
        if us10y_data:
            result['us10y']       = us10y_data['value']
            result['us10y_trend'] = us10y_data['trend']

        return result

    except Exception as exc:
        logger.warning("fetch_macro error: %s", exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        dom = await fetch_btc_dominance()
        print(f"BTC Dominance: {dom}")

        macro = await fetch_macro()
        print(f"Macro:         {macro}")

    asyncio.run(_test())
