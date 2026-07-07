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


async def fetch_cvd(
    exchange_id: str,
    symbol: str,
    windows_hours: tuple[int, ...] = (1, 4),
) -> dict | None:
    """
    Compute taker-side CVD from one public-trades snapshot.

    Returns:
        {'cvd_1h', 'cvd_4h', ...:  float | None,   # None when the snapshot
                                                    # doesn't span that window
         'cvd_window_usd': float,                   # delta over the full snapshot
         'cvd_trend': 'rising|falling|flat',
         'cvd_divergence': 'bullish|bearish|none',  # CVD vs price over the snapshot
         'coverage_minutes': float,
         'trades_count': int}
    or None on error / unclassifiable or too few trades.
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
        })
        return result

    except Exception as exc:
        logger.warning("fetch_cvd error [%s %s]: %s", exchange_id, symbol, exc)
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
