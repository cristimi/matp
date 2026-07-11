"""
Sentiment data fetchers: Fear & Greed index, funding rate, open interest.
All functions are async. Failures are non-fatal — return None on error.
"""

import asyncio
import logging
import time

import httpx
import ccxt.async_support as ccxt_async

from app.data.ohlcv import load_markets_cached, resolve_ccxt_symbol
from app.data.signal_sources import resolve_signal_venues, venue_has

logger = logging.getLogger(__name__)

# The index moves at most once/day, but node_ingest's concurrent per-strategy
# fan-out was hitting this endpoint with a fresh TLS connection from every
# strategy on every cycle — cheap to cache, and it was a repeat offender in
# missing_inputs under the resulting connection contention. Failure TTL is
# short so a transient blip doesn't sideline every concurrent caller for the
# full window (same pattern as signal_sources.py's resolve cache).
_FEAR_GREED_TTL_S         = 600.0
_FEAR_GREED_FAILURE_TTL_S = 60.0
_fear_greed_cache: tuple[float, dict | None] | None = None  # (expires_monotonic, value)
_fear_greed_lock = asyncio.Lock()


async def fetch_fear_greed() -> dict | None:
    """
    GET https://api.alternative.me/fng/?limit=1 (process-wide cached, see above)
    Returns: {'value': int, 'label': str}
    """
    global _fear_greed_cache
    now = time.monotonic()
    if _fear_greed_cache and _fear_greed_cache[0] > now:
        return _fear_greed_cache[1]

    async with _fear_greed_lock:
        now = time.monotonic()
        if _fear_greed_cache and _fear_greed_cache[0] > now:
            return _fear_greed_cache[1]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://api.alternative.me/fng/?limit=1")
                resp.raise_for_status()
                entry = resp.json()['data'][0]
            value = {'value': int(entry['value']), 'label': entry['value_classification']}
            _fear_greed_cache = (now + _FEAR_GREED_TTL_S, value)
            return value
        except Exception as exc:
            logger.warning("fetch_fear_greed error: %s", exc)
            _fear_greed_cache = (now + _FEAR_GREED_FAILURE_TTL_S, None)
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
        exchange = cls({'enableRateLimit': True, 'timeout': 25000})
        await load_markets_cached(exchange, exchange_id)
        symbol = resolve_ccxt_symbol(exchange, symbol)

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
        exchange = cls({'enableRateLimit': True, 'timeout': 25000})
        await load_markets_cached(exchange, exchange_id)
        symbol = resolve_ccxt_symbol(exchange, symbol)

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


async def _fetch_venue_oi(venue: str, venue_symbol: str) -> dict | None:
    """One venue's perp OI in USD (+ previous-day value where history exists)."""
    exchange = None
    try:
        cls = getattr(ccxt_async, venue, None)
        if cls is None:
            raise ValueError(f"Unknown venue: {venue}")
        exchange = cls({'enableRateLimit': True, 'timeout': 25000})
        await load_markets_cached(exchange, venue)

        oi = await exchange.fetch_open_interest(venue_symbol)
        value  = oi.get('openInterestValue')
        amount = oi.get('openInterestAmount') or oi.get('openInterest')
        px = None
        if not value and amount:
            # Stage-A probe: binance/bybit report OI in contracts/base amount —
            # normalize to USD via last price so venues can be summed.
            ticker = await exchange.fetch_ticker(venue_symbol)
            px = float(ticker.get('last') or 0)
            value = float(amount) * px if px else None
        if not value:
            return None
        value = float(value)

        prev = None
        try:
            hist = await exchange.fetch_open_interest_history(venue_symbol, '1d', limit=2)
            if hist and len(hist) >= 2:
                h = hist[-2]
                prev = h.get('openInterestValue')
                if not prev:
                    h_amt = h.get('openInterestAmount') or h.get('openInterest')
                    if h_amt and px:
                        prev = float(h_amt) * px  # same-price approximation
                prev = float(prev) if prev else None
        except Exception:
            pass

        return {'venue': venue, 'oi_usd': value, 'prev_oi_usd': prev}

    except Exception as exc:
        logger.warning("_fetch_venue_oi error [%s %s]: %s", venue, venue_symbol, exc)
        return None

    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass


async def fetch_open_interest_aggregate(exchange_id: str, symbol: str) -> dict | None:
    """
    Market-wide open interest: sum of perp OI (USD) across the configured
    signal venues (venues that error or don't list the symbol drop out and the
    label shrinks). The execution venue's own OI is attached as a suffix when
    it responds; long/short ratio keeps today's execution-venue behavior.

    Returns the legacy shape plus: {'venues': [...], 'own_venue_usd': float|None}.
    None when zero venues respond AND the execution venue has nothing either
    (honest absence).
    """
    try:
        targets = await resolve_signal_venues(symbol, 'fetchOpenInterest')
        results = await asyncio.gather(
            *(_fetch_venue_oi(v, s) for v, s in targets), return_exceptions=True,
        )
        per_venue = [r for r in results if isinstance(r, dict)]

        total = sum(r['oi_usd'] for r in per_venue)
        with_prev = [r for r in per_venue if r.get('prev_oi_usd')]
        change_24h_pct = 0.0
        if with_prev:
            curr = sum(r['oi_usd'] for r in with_prev)
            prev = sum(r['prev_oi_usd'] for r in with_prev)
            if prev > 0:
                change_24h_pct = round((curr - prev) / prev * 100, 2)

        # Own-venue suffix + long/short ratio via the existing single-venue path.
        own = None
        if exchange_id not in [r['venue'] for r in per_venue] and venue_has(exchange_id, 'fetchOpenInterest'):
            own = await fetch_open_interest(exchange_id, symbol)

        long_short_ratio  = own.get('long_short_ratio') if own else None
        ls_interpretation = own.get('ls_interpretation') if own else 'data unavailable'
        own_usd = float(own['open_interest_usd']) if own and own.get('open_interest_usd') else None

        if not per_venue:
            if own_usd:
                return {**own, 'venues': [exchange_id], 'own_venue_usd': None}
            return None

        return {
            'open_interest_usd': total,
            'change_24h_pct':    change_24h_pct,
            'long_short_ratio':  long_short_ratio,
            'ls_interpretation': ls_interpretation,
            'venues':            [r['venue'] for r in per_venue],
            'own_venue_usd':     own_usd,
        }

    except Exception as exc:
        logger.warning("fetch_open_interest_aggregate error [%s %s]: %s", exchange_id, symbol, exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        fg = await fetch_fear_greed()
        print(f"Fear & Greed:  {fg}")

        fr = await fetch_funding_rate("binance", "BTC/USDT")
        print(f"Funding Rate:  {fr}")

        oi = await fetch_open_interest("binance", "BTC/USDT")
        print(f"Open Interest: {oi}")

        agg = await fetch_open_interest_aggregate("hyperliquid", "BTC/USDT")
        print(f"OI Aggregate:  {agg}")

    asyncio.run(_test())
