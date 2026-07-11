"""
Signal-venue resolution for market-flow fields (multi-venue aggregation core).

Market-flow signals (OI, CVD, liquidations) answer "what is the MARKET doing?",
so they are sourced from the liquid venues in settings.signal_venues
(SIGNAL_VENUES env, default binance,bybit,okx) rather than the strategy's
execution exchange. Execution-mechanics fields (order book, OHLCV-derived)
deliberately stay venue-local — see
docs/design/ai_prompts/21_reference_exchange_sourcing.md §2.

Resolution prefers the LINEAR PERP over spot (Stage-A probe: OI/liquidations
exist only on contract markets; ccxt raises "supports contract markets only"
on spot symbols), falling back to any linear swap with the base asset, then
spot (still meaningful for trade-tape fields).

A venue that doesn't list the symbol simply drops out of that symbol's
aggregate — this replaces any per-strategy venue pinning. Empty result is
valid: callers degrade to execution-venue behavior, then honest absence.

Market catalogs are cached in module memory (~1h TTL) so resolution costs at
most len(signal_venues) load_markets calls per TTL, shared across strategies
and fields. Capability checks are static (class-level ccxt `has` map — no
network).
"""

import logging
import time

import ccxt.async_support as ccxt_async

from app.config import settings
from app.data.ohlcv import load_markets_cached

logger = logging.getLogger(__name__)

CACHE_TTL_S         = 3600.0
FAILURE_CACHE_TTL_S = 120.0   # transient venue/REST failures must not park a
                              # venue out of the aggregate for a whole hour

# (venue, requested_symbol) -> (expires_epoch, resolved_symbol | None)
_resolve_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
# venue -> static `has` capability map (never expires — it's class-level)
_has_cache: dict[str, dict] = {}


def configured_venues() -> list[str]:
    return [v.strip() for v in settings.signal_venues.split(',') if v.strip()]


def venue_has(venue: str, capability: str) -> bool:
    """Static capability check — no network."""
    if venue not in _has_cache:
        cls = getattr(ccxt_async, venue, None)
        _has_cache[venue] = cls().describe().get('has', {}) if cls else {}
    return bool(_has_cache[venue].get(capability))


def _resolve_perp_first(exchange, symbol: str) -> str | None:
    """Linear perp first, any base-matching linear swap, then spot."""
    parts = symbol.split('/')
    if len(parts) != 2:
        return symbol if symbol in exchange.markets else None
    base, quote = parts
    linear = f"{base}/{quote}:{quote}"
    if linear in exchange.markets:
        return linear
    candidates = sorted(
        s for s, m in exchange.markets.items()
        if m.get('base') == base and m.get('type') == 'swap' and m.get('linear')
    )
    if candidates:
        return candidates[0]
    return symbol if symbol in exchange.markets else None


async def _resolve_on_venue(venue: str, symbol: str) -> str | None:
    key = (venue, symbol)
    hit = _resolve_cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]

    resolved: str | None = None
    failed = False
    exchange = None
    try:
        cls = getattr(ccxt_async, venue, None)
        if cls is None:
            raise ValueError(f"Unknown venue: {venue}")
        exchange = cls({'enableRateLimit': True, 'timeout': 25000})
        await load_markets_cached(exchange, venue)
        resolved = _resolve_perp_first(exchange, symbol)
    except Exception as exc:
        failed = True
        logger.warning("signal_sources resolve error [%s %s]: %s", venue, symbol, exc)
    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass

    # Successful lookups (incl. genuine not-listed) cache long; transient
    # failures cache short so one REST hiccup doesn't park the venue out of
    # every aggregate for an hour.
    ttl = FAILURE_CACHE_TTL_S if failed else CACHE_TTL_S
    _resolve_cache[key] = (now + ttl, resolved)
    return resolved


async def resolve_signal_venues(
    symbol: str,
    capability: str | None = None,
) -> list[tuple[str, str]]:
    """
    Every configured venue that lists `symbol` (perp-preferred) and satisfies
    `capability`. Returns [(venue_id, venue_symbol), ...] — possibly empty.
    """
    out: list[tuple[str, str]] = []
    for venue in configured_venues():
        if capability and not venue_has(venue, capability):
            continue
        resolved = await _resolve_on_venue(venue, symbol)
        if resolved:
            out.append((venue, resolved))
    return out
