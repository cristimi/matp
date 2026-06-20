import logging

import ccxt.pro as ccxtpro
import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)


def make_pro_exchange(exchange_id: str):
    cls = getattr(ccxtpro, exchange_id, None)
    if cls is None:
        raise ValueError(f"Exchange '{exchange_id}' not found in ccxt.pro")
    return cls({"enableRateLimit": True})


def make_rest_exchange(exchange_id: str):
    cls = getattr(ccxt_async, exchange_id, None)
    if cls is None:
        raise ValueError(f"Exchange '{exchange_id}' not found in ccxt.async_support")
    return cls({"enableRateLimit": True})


async def resolve_symbol(exchange, canonical: str) -> str:
    """Convert BASE-QUOTE canonical symbol to ccxt unified market symbol."""
    parts = canonical.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid canonical symbol: {canonical!r} (expected BASE-QUOTE)")
    base, quote = parts

    if not exchange.markets:
        await exchange.load_markets()

    # Try linear perp first (most common on Blofin)
    candidates = [
        f"{base}/{quote}:{quote}",
        f"{base}/{quote}",
    ]
    for candidate in candidates:
        if candidate in exchange.markets:
            market = exchange.markets[candidate]
            tick_size = (market.get("precision") or {}).get("price")
            logger.info(
                "Symbol resolved: %s -> %s (tickSize=%s)", canonical, candidate, tick_size
            )
            return candidate

    # Fallback: scan swap/future markets for matching base/quote
    for sym, market in exchange.markets.items():
        if (
            market.get("base") == base
            and market.get("quote") == quote
            and market.get("type") in ("swap", "future")
        ):
            tick_size = (market.get("precision") or {}).get("price")
            logger.info(
                "Symbol resolved (scan): %s -> %s (tickSize=%s)", canonical, sym, tick_size
            )
            return sym

    raise ValueError(
        f"Cannot resolve {canonical!r} to a ccxt market on {exchange.id}. "
        f"Candidates tried: {candidates}"
    )
