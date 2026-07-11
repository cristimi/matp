"""
Phase-2 stream collector — multi-venue order-flow and liquidation accumulation.

One background supervisor task (started from the FastAPI lifespan) runs one
watch-task per (venue, symbol, stream-kind) over ccxt.pro websockets for every
enabled AI strategy's symbol, accumulating into Redis:

  cvd:{venue}:{symbol}:{minute}   hash {d: taker delta USD, g: gross USD,
                                        n: trades, p: last price}, TTL ~25h
  liq:{venue}:{symbol}            zset score=ts_ms, member=JSON event, pruned 24h
  px:{symbol}                     last trade price (any venue)
  collector:status:{venue}:{symbol}:{kind}
                                  hash {state, connected_since_ms,
                                        last_event_ms, last_error, ...}

Readers (`read_cvd_window`, `read_liquidations_window`) enforce coverage
honesty: a venue only contributes to a window it actually has data/uptime
for, and callers fall back to the Phase-1 REST methods when coverage is
insufficient — the collector improves data, it never blocks a cycle.

Design: docs/design/ai_prompts/21_reference_exchange_sourcing.md §3.2.
Known caveat (probe-verified): venues throttle public liquidation streams
(binance ~1 event/s/symbol), so the liquidation aggregate under-reports
during cascades — renderers must carry that label.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from app.config import settings
from app.data.signal_sources import configured_venues, resolve_signal_venues

logger = logging.getLogger(__name__)

RETENTION_S        = 25 * 3600   # bucket TTL — 24h window + margin
LIQ_RETENTION_MS   = 24 * 3600 * 1000
REFRESH_INTERVAL_S = 300         # strategy-set / dead-task sweep cadence
RECONNECT_BASE_S   = 5
RECONNECT_MAX_S    = 300
COVERAGE_FRACTION  = 0.9         # bucket-minutes present to call a window covered

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _minute(ts_ms: float) -> int:
    return int(ts_ms // 60_000) * 60


def _status_key(venue: str, symbol: str, kind: str) -> str:
    return f"collector:status:{venue}:{symbol}:{kind}"


# ── Writers ────────────────────────────────────────────────────────────────────

async def _record_trades(r: aioredis.Redis, venue: str, symbol: str, trades: list) -> None:
    pipe = r.pipeline(transaction=False)
    last_price = None
    for t in trades:
        ts   = t.get('timestamp')
        side = t.get('side')
        if not ts or side not in ('buy', 'sell'):
            continue
        price    = float(t.get('price') or 0)
        notional = float(t.get('cost') or 0) or price * float(t.get('amount') or 0)
        if notional <= 0:
            continue
        key = f"cvd:{venue}:{symbol}:{_minute(ts)}"
        pipe.hincrbyfloat(key, 'd', notional if side == 'buy' else -notional)
        pipe.hincrbyfloat(key, 'g', notional)
        pipe.hincrby(key, 'n', 1)
        if price > 0:
            pipe.hset(key, 'p', price)
            last_price = price
        pipe.expire(key, RETENTION_S)
    if last_price:
        pipe.set(f"px:{symbol}", last_price, ex=RETENTION_S)
    await pipe.execute()


async def _record_liquidations(r: aioredis.Redis, venue: str, symbol: str, liqs: list) -> None:
    now_ms = time.time() * 1000
    key    = f"liq:{venue}:{symbol}"
    pipe   = r.pipeline(transaction=False)
    for l in liqs:
        ts    = l.get('timestamp') or now_ms
        price = float(l.get('price') or 0)
        notional = float(l.get('quoteValue') or 0) or price * float(
            l.get('contracts') or l.get('amount') or 0
        )
        side = (l.get('side') or (l.get('info') or {}).get('side') or '').lower()
        if notional <= 0 or side not in ('buy', 'sell'):
            continue
        member = json.dumps(
            {'t': int(ts), 's': side, 'p': price, 'v': round(notional, 2)},
            separators=(',', ':'),
        )
        pipe.zadd(key, {member: int(ts)})
    pipe.zremrangebyscore(key, 0, int(now_ms - LIQ_RETENTION_MS))
    pipe.expire(key, RETENTION_S)
    await pipe.execute()


# ── Stream task (one per venue × symbol × kind, SHARED venue exchange) ─────────
# One ccxt.pro instance per venue is shared by all that venue's stream tasks —
# ccxt.pro multiplexes watch_* loops over one connection pool, and load_markets
# happens once per venue. (v1 gave each task its own instance: 14 concurrent
# load_markets calls hammered the venues' full catalog endpoints into timeouts
# and most streams sat in reconnect loops.)

async def _stream_task(ex, venue: str, venue_symbol: str, symbol: str, kind: str) -> None:
    from ccxt.base.errors import NotSupported

    r      = get_redis()
    status = _status_key(venue, symbol, kind)
    backoff = RECONNECT_BASE_S
    connected_since: int | None = None

    try:
        while True:
            try:
                # Mark connected optimistically, before the (possibly long) watch_*
                # await — for a sparse stream like liquidations on a rarely-liquidated
                # symbol, waiting for the first actual event to flip this could take
                # far longer than any read window, leaving a healthy, subscribed
                # stream indistinguishable from "never connected" the whole time
                # (read_liquidations_window requires state=='connected' to count a
                # venue at all, so it would silently drop out of every window). An
                # actual subscribe/connection failure below still demotes this to
                # 'reconnecting' immediately.
                if connected_since is None:
                    connected_since = int(time.time() * 1000)
                    await r.hset(status, mapping={
                        'state': 'connected',
                        'connected_since_ms': connected_since,
                    })
                    await r.expire(status, RETENTION_S)
                    logger.info("collector: %s %s %s connected", venue, symbol, kind)

                if kind == 'trades':
                    items = await ex.watch_trades(venue_symbol)
                    if items:
                        await _record_trades(r, venue, symbol, items)
                else:
                    items = await ex.watch_liquidations(venue_symbol)
                    if items:
                        await _record_liquidations(r, venue, symbol, items)

                backoff = RECONNECT_BASE_S
                await r.hset(status, mapping={
                    'state': 'connected',
                    'last_event_ms': int(time.time() * 1000),
                })
                await r.expire(status, RETENTION_S)

            except asyncio.CancelledError:
                raise
            except NotSupported as exc:
                await r.hset(status, mapping={
                    'state': 'unsupported', 'last_error': str(exc)[:200],
                })
                logger.info("collector: %s %s %s unsupported — task exiting", venue, symbol, kind)
                return
            except Exception as exc:
                connected_since = None
                await r.hset(status, mapping={
                    'state': 'reconnecting',
                    'connected_since_ms': '',
                    'last_error': str(exc)[:200],
                    'last_error_ms': int(time.time() * 1000),
                })
                await r.expire(status, RETENTION_S)
                logger.warning("collector: %s %s %s error (%s) — reconnect in %ss",
                               venue, symbol, kind, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)
    finally:
        # Shared exchange: closed by the Collector, not per task.
        try:
            await r.hset(status, mapping={'state': 'stopped'})
        except Exception:
            pass


# ── Supervisor ─────────────────────────────────────────────────────────────────

def _pro_has(venue: str, capability: str) -> bool:
    import ccxt.pro as ccxtpro
    cls = getattr(ccxtpro, venue, None)
    if cls is None:
        return False
    return bool(cls().describe().get('has', {}).get(capability))


class Collector:
    def __init__(self):
        self.tasks: dict[tuple, asyncio.Task] = {}
        self.exchanges: dict[str, object] = {}   # venue -> shared ccxt.pro instance
        self.started_at: float | None = None

    async def _ensure_exchange(self, venue: str):
        """Shared per-venue ccxt.pro instance, markets loaded once. None on failure
        (venue skipped this reconcile round, retried next)."""
        ex = self.exchanges.get(venue)
        if ex is not None:
            return ex
        try:
            import ccxt.pro as ccxtpro
            ex = getattr(ccxtpro, venue)({'enableRateLimit': True, 'newUpdates': True})
            await ex.load_markets()
            self.exchanges[venue] = ex
            return ex
        except Exception as exc:
            logger.warning("collector: %s exchange init failed (%s) — retrying next sweep", venue, exc)
            if ex is not None:
                try:
                    await ex.close()
                except Exception:
                    pass
            return None

    async def _active_symbols(self) -> list[str]:
        from app.database import get_pool
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT s.symbol
            FROM strategies s
            JOIN ai_strategy_config a ON a.strategy_id = s.id
            WHERE s.enabled = true
            """
        )
        out = []
        for row in rows:
            sym = row['symbol']
            parts = sym.split('-', 1) if '-' in sym else sym.split('/', 1)
            if len(parts) == 2:
                out.append(f"{parts[0]}/{parts[1]}")
        return out

    async def _desired_streams(self) -> dict[tuple, str]:
        desired: dict[tuple, str] = {}
        for symbol in await self._active_symbols():
            for venue, venue_symbol in await resolve_signal_venues(symbol):
                if _pro_has(venue, 'watchTrades'):
                    desired[(venue, symbol, 'trades')] = venue_symbol
                if _pro_has(venue, 'watchLiquidations'):
                    desired[(venue, symbol, 'liquidations')] = venue_symbol
        return desired

    async def _reconcile(self) -> None:
        desired = await self._desired_streams()

        for key, task in list(self.tasks.items()):
            if key not in desired:
                task.cancel()
                del self.tasks[key]
                logger.info("collector: stream %s removed", key)
            elif task.done():
                # Watchdog: a stream task must only exit on cancel/unsupported —
                # anything else gets logged and restarted.
                exc = task.exception() if not task.cancelled() else None
                if exc:
                    logger.error("collector: stream %s died (%s) — restarting", key, exc)
                    del self.tasks[key]
                else:
                    continue  # clean exit (unsupported) — leave it done, don't respawn

        for key, venue_symbol in desired.items():
            existing = self.tasks.get(key)
            if existing is None:
                venue, symbol, kind = key
                ex = await self._ensure_exchange(venue)
                if ex is None:
                    continue
                self.tasks[key] = asyncio.create_task(
                    _stream_task(ex, venue, venue_symbol, symbol, kind),
                    name=f"collector:{venue}:{symbol}:{kind}",
                )

    async def run(self) -> None:
        self.started_at = time.time()
        logger.info("collector: supervisor starting (venues=%s)", configured_venues())
        while True:
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("collector: reconcile failed: %s", exc)
            await asyncio.sleep(REFRESH_INTERVAL_S)

    async def stop(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        for ex in self.exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass
        self.exchanges.clear()

    def status(self) -> dict:
        alive = sum(1 for t in self.tasks.values() if not t.done())
        return {
            'running':    self.started_at is not None,
            'streams':    len(self.tasks),
            'alive':      alive,
            'started_at': self.started_at,
        }


collector = Collector()


# ── Readers (used by app/data fetchers; never raise) ──────────────────────────

async def read_cvd_window(symbol: str, minutes: int) -> dict | None:
    """
    Aggregate taker delta over the trailing `minutes` across venues whose
    bucket coverage passes COVERAGE_FRACTION. Returns
    {'delta_usd', 'gross_usd', 'trades', 'venues', 'covered_minutes',
     'first_price', 'last_price'} or None when no venue covers the window.
    """
    try:
        r = get_redis()
        now_min = _minute(time.time() * 1000)
        mins = [now_min - 60 * i for i in range(minutes, 0, -1)]

        total_d = total_g = 0.0
        total_n = 0
        venues: list[str] = []
        best_cover = 0
        first_price = last_price = None

        for venue in configured_venues():
            pipe = r.pipeline(transaction=False)
            for m in mins:
                pipe.hgetall(f"cvd:{venue}:{symbol}:{m}")
            buckets = await pipe.execute()
            present = [b for b in buckets if b]
            if len(present) < COVERAGE_FRACTION * minutes:
                continue
            venues.append(venue)
            best_cover = max(best_cover, len(present))
            total_d += sum(float(b.get('d', 0)) for b in present)
            total_g += sum(float(b.get('g', 0)) for b in present)
            total_n += sum(int(b.get('n', 0)) for b in present)
            for b in buckets:
                if b and b.get('p'):
                    if first_price is None:
                        first_price = float(b['p'])
                    last_price = float(b['p'])

        if not venues:
            return None
        return {
            'delta_usd':       total_d,
            'gross_usd':       total_g,
            'trades':          total_n,
            'venues':          venues,
            'covered_minutes': best_cover,
            'first_price':     first_price,
            'last_price':      last_price,
        }
    except Exception as exc:
        logger.warning("read_cvd_window error [%s %sm]: %s", symbol, minutes, exc)
        return None


async def read_liquidations_window(symbol: str, window_hours: int) -> dict | None:
    """
    Liquidation events over the trailing window across venues whose liq stream
    is connected. Interpretation: a forced SELL closes a long (long liq), a
    forced BUY closes a short. Returns
    {'events': [{'t','s','p','v'}...], 'venues', 'covered_from_ms', 'ref_price'}
    or None when no venue's liquidation stream is up (keeps no-op behavior
    when the collector is down).
    """
    try:
        r = get_redis()
        now_ms   = int(time.time() * 1000)
        start_ms = now_ms - window_hours * 3_600_000

        venues: list[str] = []
        covered_from = start_ms
        events: list[dict] = []

        for venue in configured_venues():
            status = await r.hgetall(_status_key(venue, symbol, 'liquidations'))
            if status.get('state') != 'connected':
                continue
            since = int(status.get('connected_since_ms') or now_ms)
            venues.append(venue)
            covered_from = max(covered_from, since)
            raw = await r.zrangebyscore(f"liq:{venue}:{symbol}", start_ms, now_ms)
            events.extend(json.loads(m) for m in raw)

        if not venues:
            return None
        ref_price = await r.get(f"px:{symbol}")
        return {
            'events':          sorted(events, key=lambda e: e['t']),
            'venues':          venues,
            'covered_from_ms': covered_from,
            'ref_price':       float(ref_price) if ref_price else None,
        }
    except Exception as exc:
        logger.warning("read_liquidations_window error [%s %sh]: %s", symbol, window_hours, exc)
        return None
