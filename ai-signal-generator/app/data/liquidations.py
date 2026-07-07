"""
Liquidation data — sourced from the Phase-2 stream collector.

History: REST liquidations are unusable market-wide (Wave-3 + Stage-A probes:
neither configured execution exchange supports ccxt fetch_liquidations, and
the five exchanges that implement it returned 0–6 entries per 4h window). The
only viable free source is the venues' public websocket streams — collected
by app/collector.py (watchLiquidations on the SIGNAL_VENUES set) into Redis,
read here per cycle.

Interpretation: a liquidation printed as a forced SELL closes a long (long
liquidated); a forced BUY closes a short.

Honesty constraints:
  - None when no venue's liquidation stream is connected (collector cold or
    down) — the section stays absent, exactly like the old no-op.
  - A connected-but-quiet window returns real zeros — "no liquidations" is
    information, distinct from "no data".
  - Venue streams are throttled at the source (probe: binance ~1 event/s per
    symbol), so totals UNDER-REPORT during cascades — the renderer carries
    this caveat permanently.

Design: docs/design/ai_prompts/21_reference_exchange_sourcing.md §4.3 (Phase 2).
"""

import logging

logger = logging.getLogger(__name__)

CLUSTER_GRID_PCT = 0.25  # price-bin width as % of reference price
CLUSTER_BAND_PCT = 5.0   # only cluster events within ±this % of reference
MAX_CLUSTERS     = 3


async def fetch_liquidations(
    exchange_id: str,
    symbol: str,
    window_hours: int = 4,
) -> dict | None:
    """
    Aggregate liquidations over the trailing window from the stream collector.

    Returns:
        {'liq_long_volume_4h': float, 'liq_short_volume_4h': float,
         'liq_clusters': [{'price', 'volume_usd'}, ...],   # near current price
         'venues': [...], 'events': int, 'covered_hours': float,
         'source': 'stream'}
    (window keys are named for the requested window)
    or None when no liquidation stream is connected — honest absence.
    """
    try:
        from app.collector import read_liquidations_window

        win = await read_liquidations_window(symbol, window_hours)
        if win is None:
            return None

        events = win['events']
        long_vol  = sum(e['v'] for e in events if e['s'] == 'sell')
        short_vol = sum(e['v'] for e in events if e['s'] == 'buy')

        clusters: list[dict] = []
        ref = win.get('ref_price')
        if ref and events:
            grid = ref * CLUSTER_GRID_PCT / 100.0
            band = ref * CLUSTER_BAND_PCT / 100.0
            bins: dict[int, float] = {}
            for e in events:
                if e['p'] and abs(e['p'] - ref) <= band:
                    bins[round(e['p'] / grid)] = bins.get(round(e['p'] / grid), 0.0) + e['v']
            top = sorted(bins.items(), key=lambda kv: kv[1], reverse=True)[:MAX_CLUSTERS]
            clusters = [
                {'price': round(k * grid, 6), 'volume_usd': round(v, 2)}
                for k, v in sorted(top)
            ]

        import time
        covered_hours = round(
            (time.time() * 1000 - win['covered_from_ms']) / 3_600_000, 1
        )

        return {
            f'liq_long_volume_{window_hours}h':  round(long_vol, 2),
            f'liq_short_volume_{window_hours}h': round(short_vol, 2),
            'liq_clusters':  clusters,
            'venues':        win['venues'],
            'events':        len(events),
            'covered_hours': min(covered_hours, float(window_hours)),
            'source':        'stream',
        }

    except Exception as exc:
        logger.warning("fetch_liquidations error [%s %s]: %s", exchange_id, symbol, exc)
        return None


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        result = await fetch_liquidations("hyperliquid", "BTC/USDT")
        print(result)

    asyncio.run(_test())
