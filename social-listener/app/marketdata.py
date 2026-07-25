import json
import logging

import redis.asyncio as aioredis

from app.config import settings

log = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


def _client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def get_mark(asset: str) -> float | None:
    """Return the latest mark price for asset from the 1m forming candle, or None."""
    symbol = f"{asset.upper()}-USDT"
    key = f"candle:forming:{settings.ingestion_exchange}:{symbol}:1m"
    try:
        raw = await _client().get(key)
        if not raw:
            return None
        return float(json.loads(raw)["c"])
    except Exception as e:  # noqa: BLE001
        log.warning("get_mark(%s) key=%s failed: %s", asset, key, e)
        return None


async def get_close_at(asset: str, ts_ms: int) -> float | None:
    """Close of the 1m bar covering ts_ms — the market price when the post was made.

    Used as an implied reference for signals that cite no price, so the staleness
    gate can still run. Returns None when the bar isn't in the stream (older than
    the ~2000-bar retention, or a gap in ingestion) — the caller must not treat a
    missing bar as "no movement".
    """
    symbol = f"{asset.upper()}-USDT"
    key = f"stream:candles:{settings.ingestion_exchange}:{symbol}:1m"
    # Stream IDs are ingest wall-clock (~a beat after bar close), so widen the
    # scan either side of ts_ms and pick the right bar by its own "t" field.
    lo = ts_ms - settings.implied_ref_lookback_ms
    hi = ts_ms + 180_000
    try:
        entries = await _client().xrevrange(key, max=hi, min=max(lo, 0))
    except Exception as e:  # noqa: BLE001
        log.warning("get_close_at(%s) key=%s failed: %s", asset, key, e)
        return None

    best_t, best_c = None, None
    for _id, f in entries:
        try:
            t = int(f["t"])
            if t <= ts_ms and (best_t is None or t > best_t):
                best_t, best_c = t, float(f["c"])
        except (KeyError, TypeError, ValueError):
            continue

    if best_t is None:
        return None
    # A bar far older than the post is not "the price at post time".
    if ts_ms - best_t > settings.implied_ref_max_gap_ms:
        log.info(
            "implied ref for %s rejected: nearest bar %ds before post",
            asset, (ts_ms - best_t) // 1000,
        )
        return None
    return best_c
