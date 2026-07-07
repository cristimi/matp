"""
Cumulative Volume Delta (CVD) from public trades, taker-side classified.

Async ccxt public-endpoint fetcher on the sentiment.py lifecycle (class lookup
→ enableRateLimit → load_markets → resolve_ccxt_symbol → close() in finally;
failures are non-fatal — return None on error).

Fetch strategy (the spec §4 "realistic v1"): ONE fetch_trades call capped at
MAX_TRADES — no pagination, so a cycle can never hang on a busy tape and the
rate-limit budget is one call per cycle. The exchange decides how much history
that returns (blofin: 100 trades ≈ minutes), so the covered window is measured
and labeled honestly rather than pretending to a full 1h/4h read: a requested
window only gets a number when the snapshot actually spans it, and the
whole-snapshot delta + coverage are always reported.

Known per-exchange gap: hyperliquid's ccxt fetch_trades requires a `user`
wallet param (user fills only — no public trades over REST), so CVD returns
None there and the section is honestly absent.
"""

import asyncio
import logging

import ccxt.async_support as ccxt_async

from app.data.ohlcv import resolve_ccxt_symbol

logger = logging.getLogger(__name__)

MAX_TRADES   = 1000  # single-call cap; exchanges may return fewer (blofin: 100)
MIN_TRADES   = 20    # below this a delta is noise, not order flow
FLAT_THR_PCT = 5.0   # |half-window delta| under this % of gross volume → flat


def _delta_usd(trades: list[dict]) -> tuple[float, float]:
    """(net taker delta, gross volume) in quote currency over the trades."""
    net = gross = 0.0
    for t in trades:
        notional = t.get('cost')
        if not notional:
            price  = float(t.get('price') or 0)
            amount = float(t.get('amount') or 0)
            notional = price * amount
        notional = float(notional)
        gross += notional
        if t.get('side') == 'buy':
            net += notional
        elif t.get('side') == 'sell':
            net -= notional
    return net, gross


async def _fetch_cvd_klines_binance(
    venue_symbol: str,
    windows_hours: tuple[int, ...] = (1, 4),
) -> dict | None:
    """
    Full-window CVD from Binance USDⓈ-M klines taker-buy volume — one API call.

    Binance kline rows carry takerBuyQuoteAssetVolume (index 10) next to total
    quote volume (index 7), so per-candle taker delta = 2*takerBuyQuote - total.
    ccxt's generic fetch_ohlcv drops those fields → this uses the implicit API
    (fapiPublicGetKlines), deliberately confined to this one function (plan
    §4.2); any shape change falls through to the trades-snapshot fallback.
    """
    exchange = None
    try:
        exchange = ccxt_async.binance({'enableRateLimit': True})
        await exchange.load_markets()
        market_id = exchange.market(venue_symbol)['id']

        max_w   = max(windows_hours)
        buckets = max_w * 12  # 5m candles per window
        raw = await exchange.fapiPublicGetKlines({
            'symbol': market_id, 'interval': '5m', 'limit': buckets,
        })
        if not raw or len(raw) < 12:
            return None

        deltas = [2 * float(r[10]) - float(r[7]) for r in raw]
        gross  = [float(r[7]) for r in raw]
        trades = sum(int(r[8]) for r in raw)

        result: dict = {}
        for w in windows_hours:
            n = w * 12
            result[f'cvd_{w}h'] = round(sum(deltas[-n:]), 2) if len(deltas) >= n else None

        # Trend = direction of the most recent hour's flow, flat-guarded vs gross.
        recent_net   = sum(deltas[-12:])
        recent_gross = sum(gross[-12:])
        if recent_gross > 0 and abs(recent_net) / recent_gross * 100 < FLAT_THR_PCT:
            trend = 'flat'
        else:
            trend = 'rising' if recent_net > 0 else 'falling'

        # Divergence: full-window CVD sign vs price direction, flat-guarded.
        full_net    = sum(deltas)
        full_gross  = sum(gross)
        first_open  = float(raw[0][1])
        last_close  = float(raw[-1][4])
        divergence = 'none'
        if first_open > 0 and full_gross > 0 and abs(full_net) / full_gross * 100 >= FLAT_THR_PCT:
            price_up = last_close > first_open
            if price_up and full_net < 0:
                divergence = 'bearish'
            elif not price_up and full_net > 0:
                divergence = 'bullish'

        result.update({
            'cvd_window_usd':   round(full_net, 2),
            'cvd_trend':        trend,
            'cvd_divergence':   divergence,
            'coverage_minutes': round(len(raw) * 5.0, 1),
            'trades_count':     trades,
            'source':           'binance',
            'method':           'klines_taker',
        })
        return result

    except Exception as exc:
        logger.warning("_fetch_cvd_klines_binance error [%s]: %s", venue_symbol, exc)
        return None

    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass


async def _fetch_cvd_stream_aggregate(
    symbol: str,
    windows_hours: tuple[int, ...] = (1, 4),
) -> dict | None:
    """
    True multi-venue CVD from the Phase-2 stream collector's Redis buckets
    (plan §4.2 stream_aggregate). The venue set is whoever covers the SMALLEST
    requested window; larger windows are only claimed when that same set covers
    them too — no silently shrinking denominators. Returns None when no venue
    covers even the smallest window (collector cold/down) so the caller falls
    back to the Phase-1 methods.
    """
    from app.collector import read_cvd_window

    smallest = min(windows_hours)
    base = await read_cvd_window(symbol, smallest * 60)
    if not base:
        return None
    venues = base['venues']

    result: dict = {f'cvd_{smallest}h': round(base['delta_usd'], 2)}
    largest_covered = base
    for w in sorted(windows_hours):
        if w == smallest:
            continue
        win = await read_cvd_window(symbol, w * 60)
        # Claim the window only if the base venue set fully carried over.
        if win and set(venues).issubset(set(win['venues'])):
            result[f'cvd_{w}h'] = round(win['delta_usd'], 2)
            largest_covered = win
        else:
            result[f'cvd_{w}h'] = None

    # Trend from the most recent hour (or the smallest window if sub-hour).
    recent = await read_cvd_window(symbol, min(60, smallest * 60))
    trend = 'flat'
    if recent and recent['gross_usd'] > 0:
        if abs(recent['delta_usd']) / recent['gross_usd'] * 100 >= FLAT_THR_PCT:
            trend = 'rising' if recent['delta_usd'] > 0 else 'falling'

    # Divergence over the largest covered window: CVD sign vs price direction.
    divergence = 'none'
    lc = largest_covered
    if (lc['first_price'] and lc['last_price'] and lc['gross_usd'] > 0
            and abs(lc['delta_usd']) / lc['gross_usd'] * 100 >= FLAT_THR_PCT):
        price_up = lc['last_price'] > lc['first_price']
        if price_up and lc['delta_usd'] < 0:
            divergence = 'bearish'
        elif not price_up and lc['delta_usd'] > 0:
            divergence = 'bullish'

    result.update({
        'cvd_window_usd':   round(lc['delta_usd'], 2),
        'cvd_trend':        trend,
        'cvd_divergence':   divergence,
        'coverage_minutes': float(lc['covered_minutes']),
        'trades_count':     lc['trades'],
        'source':           '+'.join(venues),
        'method':           'stream_aggregate',
    })
    return result


async def fetch_cvd(
    exchange_id: str,
    symbol: str,
    windows_hours: tuple[int, ...] = (1, 4),
) -> dict | None:
    """
    Taker-side CVD, sourced per plan §4.2 (multi-venue Phase 2):
      1. stream_aggregate — true multi-venue windows from the collector's
         Redis buckets, when coverage exists;
      2. Binance klines taker-volume — full 1h/4h windows, one call — when a
         signal venue resolves the symbol on binance;
      3. else trades snapshot on the first resolving signal venue;
      4. else trades snapshot on the execution venue (Wave-3 behavior);
      5. else None (honest absence).

    Returns the Wave-3 shape plus {'source', 'method':
    'stream_aggregate' | 'klines_taker' | 'trades_snapshot'}.
    """
    try:
        result = await _fetch_cvd_stream_aggregate(symbol, windows_hours)
        if result:
            return result

        from app.data.signal_sources import resolve_signal_venues
        venues = await resolve_signal_venues(symbol)

        for venue, vsym in venues:
            if venue == 'binance':
                result = await _fetch_cvd_klines_binance(vsym, windows_hours)
                if result:
                    return result

        for venue, vsym in venues:
            result = await _fetch_cvd_trades(venue, vsym, windows_hours)
            if result:
                return result

        return await _fetch_cvd_trades(exchange_id, symbol, windows_hours)

    except Exception as exc:
        logger.warning("fetch_cvd error [%s %s]: %s", exchange_id, symbol, exc)
        return None


async def _fetch_cvd_trades(
    exchange_id: str,
    symbol: str,
    windows_hours: tuple[int, ...] = (1, 4),
) -> dict | None:
    """
    Wave-3 fallback: taker-side CVD from one public-trades snapshot
    (windows that the snapshot doesn't span stay None — labeled, not faked).
    """
    exchange = None
    try:
        cls = getattr(ccxt_async, exchange_id, None)
        if cls is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        exchange = cls({'enableRateLimit': True})
        await exchange.load_markets()
        symbol = resolve_ccxt_symbol(exchange, symbol)

        trades = await exchange.fetch_trades(symbol, limit=MAX_TRADES)
        trades = [
            t for t in (trades or [])
            if t.get('timestamp') and t.get('side') in ('buy', 'sell')
        ]
        if len(trades) < MIN_TRADES:
            return None
        trades.sort(key=lambda t: t['timestamp'])

        newest_ms = trades[-1]['timestamp']
        oldest_ms = trades[0]['timestamp']
        coverage_minutes = round((newest_ms - oldest_ms) / 60_000, 1)

        result: dict = {}
        for w in windows_hours:
            cutoff = newest_ms - w * 3_600_000
            if oldest_ms <= cutoff:
                in_window = [t for t in trades if t['timestamp'] >= cutoff]
                result[f'cvd_{w}h'] = round(_delta_usd(in_window)[0], 2)
            else:
                result[f'cvd_{w}h'] = None  # snapshot too short — labeled, not faked

        net, _ = _delta_usd(trades)

        # Trend: taker delta of the newer half of the snapshot.
        half = trades[len(trades) // 2:]
        half_net, half_gross = _delta_usd(half)
        if half_gross > 0 and abs(half_net) / half_gross * 100 < FLAT_THR_PCT:
            trend = 'flat'
        else:
            trend = 'rising' if half_net > 0 else 'falling'

        # Divergence: CVD direction vs price direction over the snapshot.
        first_price = float(trades[0]['price'])
        last_price  = float(trades[-1]['price'])
        divergence = 'none'
        if first_price > 0 and trend != 'flat':
            price_up = last_price > first_price
            if price_up and trend == 'falling':
                divergence = 'bearish'
            elif not price_up and trend == 'rising':
                divergence = 'bullish'

        result.update({
            'cvd_window_usd':   round(net, 2),
            'cvd_trend':        trend,
            'cvd_divergence':   divergence,
            'coverage_minutes': coverage_minutes,
            'trades_count':     len(trades),
            'source':           exchange_id,
            'method':           'trades_snapshot',
        })
        return result

    except Exception as exc:
        logger.warning("_fetch_cvd_trades error [%s %s]: %s", exchange_id, symbol, exc)
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
        for eid in ("blofin", "hyperliquid"):
            result = await fetch_cvd(eid, "BTC/USDT")
            print(f"{eid}: {json.dumps(result, indent=2)}")

    asyncio.run(_test())
