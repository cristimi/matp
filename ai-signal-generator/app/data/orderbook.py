"""
Order book depth snapshot (aggregated notional near mid + resting walls).

Async ccxt public-endpoint fetcher, modeled on sentiment.py::fetch_open_interest
(same lifecycle: class lookup → enableRateLimit → load_markets →
resolve_ccxt_symbol → work → close() in finally; failures are non-fatal —
return None on error).

A book snapshot is cycle-time evidence only — walls are ephemeral/spoofable;
the prompt templates treat this as corroboration, not primary signal.
"""

import asyncio
import logging

import ccxt.async_support as ccxt_async

from app.data.ohlcv import resolve_ccxt_symbol

logger = logging.getLogger(__name__)

BOOK_LIMIT = 500  # levels per side requested; falls back to exchange default


async def fetch_orderbook_depth(
    exchange_id: str,
    symbol: str,
    depth_pct: tuple[float, float] = (1.0, 2.0),
) -> dict | None:
    """
    Snapshot the order book and aggregate resting notional near the mid price.

    Returns:
        {'bid_depth_1pct_usd', 'ask_depth_1pct_usd',
         'bid_depth_2pct_usd', 'ask_depth_2pct_usd',
         'depth_imbalance_ratio',                     # 1%-band bid/ask notional
         'largest_bid_wall': {'price', 'size_usd'},   # within the outer band
         'largest_ask_wall': {'price', 'size_usd'}}
    or None on error/empty book.
    """
    exchange = None
    try:
        cls = getattr(ccxt_async, exchange_id, None)
        if cls is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        exchange = cls({'enableRateLimit': True})
        await exchange.load_markets()
        symbol = resolve_ccxt_symbol(exchange, symbol)

        try:
            book = await exchange.fetch_order_book(symbol, limit=BOOK_LIMIT)
        except Exception:
            # Some exchanges only accept specific limit values — retry default.
            book = await exchange.fetch_order_book(symbol)

        bids = book.get('bids') or []
        asks = book.get('asks') or []
        if not bids or not asks:
            return None

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return None

        inner_pct, outer_pct = depth_pct

        def _depth(levels: list, lo: float, hi: float) -> float:
            return sum(
                float(p) * float(s) for p, s, *_ in levels if lo <= float(p) <= hi
            )

        bid_inner = _depth(bids, mid * (1 - inner_pct / 100), mid)
        bid_outer = _depth(bids, mid * (1 - outer_pct / 100), mid)
        ask_inner = _depth(asks, mid, mid * (1 + inner_pct / 100))
        ask_outer = _depth(asks, mid, mid * (1 + outer_pct / 100))

        imbalance = round(bid_inner / ask_inner, 3) if ask_inner > 0 else None

        def _largest_wall(levels: list, lo: float, hi: float) -> dict | None:
            in_band = [
                (float(p), float(p) * float(s))
                for p, s, *_ in levels if lo <= float(p) <= hi
            ]
            if not in_band:
                return None
            price, size_usd = max(in_band, key=lambda t: t[1])
            return {'price': round(price, 6), 'size_usd': round(size_usd, 2)}

        return {
            'bid_depth_1pct_usd':    round(bid_inner, 2),
            'ask_depth_1pct_usd':    round(ask_inner, 2),
            'bid_depth_2pct_usd':    round(bid_outer, 2),
            'ask_depth_2pct_usd':    round(ask_outer, 2),
            'depth_imbalance_ratio': imbalance,
            'largest_bid_wall':      _largest_wall(bids, mid * (1 - outer_pct / 100), mid),
            'largest_ask_wall':      _largest_wall(asks, mid, mid * (1 + outer_pct / 100)),
        }

    except Exception as exc:
        logger.warning("fetch_orderbook_depth error [%s %s]: %s", exchange_id, symbol, exc)
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
        result = await fetch_orderbook_depth("binance", "BTC/USDT")
        print(json.dumps(result, indent=2))

    asyncio.run(_test())
