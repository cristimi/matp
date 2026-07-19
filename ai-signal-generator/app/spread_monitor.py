"""
Cross-venue funding-spread monitor + armed planner — phase 1 of the staged
spread-harvest automation (docs/design/SPREAD_HARVEST.md).

Validated by edge-research phases 5-6: walk-forward OOS +14.2%/yr on notional
with the exact signal used here (168h trailing mean of the hourly-equivalent
HL-vs-Blofin funding spread, enter |trailing| > 50%/yr, exit < 10%/yr).

Hourly, per universe coin: fetch the last ~7 days of HL hourly funding and
Blofin settlements (Blofin mixes 4h/8h cadences — each settlement's interval
is inferred from timestamp gaps), compute the trailing annualized spread, run
per-coin hysteresis with state in Redis. On cool->hot with a free slot (max 3
armed, highest |trailing| wins): build the delta-neutral two-leg plan — short
the venue with higher funding, long the other, live book walk on BOTH venues
to the target notional, est. daily collect, breakeven, ±25% abort prices —
persist to spread_plans and emit `spread.hot`. On hot->cool: expire armed
plans, emit `spread.cooled`. Read-only: nothing here places orders.
"""

import asyncio
import json
import logging
import time

import httpx

from app.collector import get_redis
from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

HOURS_ANN = 24 * 365
STATE_KEY = "spread_monitor:state"
STREAM_KEY = "notifications:events"
HL_INFO = "https://api.hyperliquid.xyz/info"
BLOFIN = "https://openapi.blofin.com/api/v1/market"


async def _hl_post(client, payload):
    r = await client.post(HL_INFO, json=payload)
    r.raise_for_status()
    return r.json()


async def _blofin_get(client, path, **params):
    r = await client.get(f"{BLOFIN}/{path}", params=params)
    r.raise_for_status()
    data = r.json()
    if data.get("code") not in ("0", 0):
        raise RuntimeError(f"blofin {path}: {data.get('msg')}")
    return data["data"]


async def trailing_spread(client, coin: str) -> float | None:
    """168h trailing mean of hourly-equivalent (HL - Blofin) funding, annualized."""
    horizon_ms = (settings.spread_trail_hours + 2) * 3600 * 1000
    now_ms = int(time.time() * 1000)
    hl = await _hl_post(client, {"type": "fundingHistory", "coin": coin,
                                 "startTime": now_ms - horizon_ms})
    bf = await _blofin_get(client, "funding-rate-history",
                           instId=f"{coin}-USDT", limit="100")
    if len(hl) < settings.spread_trail_hours // 2 or len(bf) < 3:
        return None
    hl_rates = [float(r["fundingRate"]) for r in hl][-settings.spread_trail_hours:]
    hl_hourly = sum(hl_rates) / len(hl_rates)
    # Blofin: newest-first settlements; per-hour equivalent via gap inference
    bf_pts = sorted((int(r["fundingTime"]), float(r["fundingRate"])) for r in bf)
    cutoff = now_ms - settings.spread_trail_hours * 3600 * 1000
    total, hours = 0.0, 0.0
    for (t0, _), (t1, rate) in zip(bf_pts, bf_pts[1:]):
        gap_h = min(max((t1 - t0) / 3600_000, 1), 8)
        if t1 >= cutoff:
            total += rate
            hours += gap_h
    if hours < settings.spread_trail_hours / 2:
        return None
    bf_hourly = total / hours
    return (hl_hourly - bf_hourly) * HOURS_ANN


def _walk(levels, notional, px_key=0, sz_key=1) -> tuple[float, float] | None:
    """Average fill + slippage bps walking one book side to `notional` USD.
    Levels are [price, size] pairs (Blofin) or {px, sz} dicts (HL)."""
    if not levels:
        return None
    def _px(l): return float(l["px"] if isinstance(l, dict) else l[px_key])
    def _sz(l): return float(l["sz"] if isinstance(l, dict) else l[sz_key])
    best = _px(levels[0])
    remaining, cost, qty = notional, 0.0, 0.0
    for lvl in levels:
        take = min(remaining, _px(lvl) * _sz(lvl))
        cost += take
        qty += take / _px(lvl)
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        return None
    avg = cost / qty
    return avg, abs(avg - best) / best * 1e4


class SpreadMonitor:
    def __init__(self):
        self._status: dict[str, dict] = {}
        self._last_run: float | None = None
        self._running = False

    def status(self) -> dict:
        return {
            "enabled": settings.spread_monitor_enabled,
            "enter_ann": settings.spread_enter_ann,
            "exit_ann": settings.spread_exit_ann,
            "trail_hours": settings.spread_trail_hours,
            "max_concurrent": settings.spread_max_concurrent,
            "last_run_epoch": self._last_run,
            "coins": self._status,
        }

    async def run(self) -> None:
        if not settings.spread_monitor_enabled:
            logger.info("Spread monitor disabled via settings")
            return
        self._running = True
        logger.info(
            "Spread monitor started: %d coins, enter>|%.0f%%|/yr exit<%.0f%%/yr "
            "trail %dh every %ds",
            len(self._coins()), settings.spread_enter_ann * 100,
            settings.spread_exit_ann * 100, settings.spread_trail_hours,
            settings.spread_monitor_interval_s,
        )
        while self._running:
            try:
                await self._check_all()
            except Exception as exc:  # noqa: BLE001 — monitor survives any cycle error
                logger.error("Spread monitor cycle failed: %s", exc)
            await asyncio.sleep(settings.spread_monitor_interval_s)

    def stop(self) -> None:
        self._running = False

    def _coins(self) -> list[str]:
        return [c.strip().upper() for c in settings.spread_monitor_symbols.split(",")
                if c.strip()]

    async def _check_all(self) -> None:
        redis = get_redis()
        async with httpx.AsyncClient(timeout=20) as client:
            for coin in self._coins():
                try:
                    tr = await trailing_spread(client, coin)
                except Exception as exc:  # noqa: BLE001 — one coin failing is non-fatal
                    logger.warning("Spread monitor: %s fetch failed: %s", coin, exc)
                    continue
                if tr is None:
                    continue
                prev = await redis.hget(STATE_KEY, coin) or "cool"
                state = prev
                if prev != "hot" and abs(tr) > settings.spread_enter_ann:
                    state = "hot"
                    plan = None
                    if await self._armed_count() < settings.spread_max_concurrent:
                        plan = await build_plan(coin, tr)
                    await self._emit("spread.hot", coin, tr, plan=plan)
                elif prev == "hot" and abs(tr) < settings.spread_exit_ann:
                    state = "cool"
                    expired = await expire_plans(coin)
                    await self._emit("spread.cooled", coin, tr, expired_plans=expired)
                if state != prev:
                    await redis.hset(STATE_KEY, coin, state)
                self._status[coin] = {"trailing_ann_pct": round(tr * 100, 2),
                                      "state": state}
                await asyncio.sleep(0.2)   # spread requests out; both APIs public
        self._last_run = time.time()
        hot = [c for c, s in self._status.items() if s["state"] == "hot"]
        logger.info("Spread monitor cycle: %d coins checked, hot=%s",
                    len(self._status), hot or "none")

    async def _armed_count(self) -> int:
        pool = get_pool()
        row = await pool.fetchrow("SELECT count(*) AS n FROM spread_plans WHERE status='armed'")
        return int(row["n"])

    async def _emit(self, event, coin, trailing_ann, **extra) -> None:
        try:
            data = {
                "event": event, "symbol": coin,
                "trailing_ann": round(trailing_ann, 4),
                "enter_ann": settings.spread_enter_ann,
                "exit_ann": settings.spread_exit_ann,
                **{k: v for k, v in extra.items() if v is not None},
            }
            await get_redis().xadd(STREAM_KEY, {"data": json.dumps(data)})
            logger.info("Spread monitor: emitted %s for %s (trailing %.1f%%/yr)",
                        event, coin, trailing_ann * 100)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Spread monitor: emit %s for %s failed: %s", event, coin, exc)


async def build_plan(coin: str, trailing_ann: float, persist: bool = True) -> dict | None:
    """Two-leg delta-neutral plan: short the venue with higher funding. Never
    raises — a failed plan degrades to a plain notification."""
    try:
        capital = settings.spread_capital_usd
        leverage = settings.spread_leg_leverage
        notional = capital / 2      # margin N/lev per leg, rest is top-up buffer
        short_venue = "hyperliquid" if trailing_ann > 0 else "blofin"
        long_venue = "blofin" if trailing_ann > 0 else "hyperliquid"

        async with httpx.AsyncClient(timeout=20) as client:
            hl_book = await _hl_post(client, {"type": "l2Book", "coin": coin})
            bf_book = (await _blofin_get(client, "books",
                                         instId=f"{coin}-USDT", size="100"))[0]
        # short leg sells into bids; long leg buys from asks
        hl_walk = _walk(hl_book["levels"][0 if short_venue == "hyperliquid" else 1], notional)
        bf_walk = _walk(bf_book["bids" if short_venue == "blofin" else "asks"], notional)
        if hl_walk is None or bf_walk is None:
            logger.warning("Spread plan: %s book too thin for $%.0f/leg", coin, notional)
            return None
        hl_px, hl_slip = hl_walk
        bf_px, bf_slip = bf_walk

        est_daily = abs(trailing_ann) * notional / 365
        roundtrip = settings.spread_roundtrip_cost * notional
        breakeven = roundtrip / est_daily if est_daily > 0 else None
        mid = (hl_px + bf_px) / 2
        plan = {
            "coin": coin,
            "trailing_spread_ann": round(trailing_ann, 4),
            "short_venue": short_venue, "long_venue": long_venue,
            "capital_usd": capital, "notional_usd": round(notional, 2),
            "leg_leverage": leverage,
            "hl_price": hl_px, "blofin_price": bf_px,
            "hl_slippage_bps": round(hl_slip, 2),
            "blofin_slippage_bps": round(bf_slip, 2),
            "est_daily_usd": round(est_daily, 4),
            "est_roundtrip_usd": round(roundtrip, 4),
            "breakeven_days": round(breakeven, 2) if breakeven is not None else None,
            "abort_up_price": round(mid * (1 + settings.spread_abort_pct), 8),
            "abort_down_price": round(mid * (1 - settings.spread_abort_pct), 8),
            "details": {"basis_bps": round((hl_px - bf_px) / bf_px * 1e4, 2),
                        "abort_pct": settings.spread_abort_pct},
        }
        if persist:
            plan["id"] = await _persist(plan)
        logger.info(
            "Spread plan: armed %s short-%s/long-%s $%.0f/leg ~$%.2f/day "
            "(%.0f%%/yr), breakeven %.1fd",
            coin, short_venue, long_venue, notional, est_daily,
            abs(trailing_ann) * 100, breakeven if breakeven is not None else -1)
        return plan
    except Exception as exc:  # noqa: BLE001
        logger.error("Spread plan for %s failed: %s", coin, exc)
        return None


async def _persist(plan: dict) -> str:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE spread_plans SET status='expired', updated_at=now() "
                "WHERE coin=$1 AND status='armed'", plan["coin"])
            row = await conn.fetchrow(
                """
                INSERT INTO spread_plans
                    (coin, trailing_spread_ann, short_venue, long_venue, capital_usd,
                     notional_usd, leg_leverage, hl_price, blofin_price,
                     hl_slippage_bps, blofin_slippage_bps, est_daily_usd,
                     est_roundtrip_usd, breakeven_days, abort_up_price,
                     abort_down_price, details)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb)
                RETURNING id
                """,
                plan["coin"], plan["trailing_spread_ann"], plan["short_venue"],
                plan["long_venue"], plan["capital_usd"], plan["notional_usd"],
                plan["leg_leverage"], plan["hl_price"], plan["blofin_price"],
                plan["hl_slippage_bps"], plan["blofin_slippage_bps"],
                plan["est_daily_usd"], plan["est_roundtrip_usd"],
                plan["breakeven_days"], plan["abort_up_price"],
                plan["abort_down_price"], json.dumps(plan["details"]))
    return str(row["id"])


async def expire_plans(coin: str) -> int:
    try:
        pool = get_pool()
        result = await pool.execute(
            "UPDATE spread_plans SET status='expired', updated_at=now() "
            "WHERE coin=$1 AND status='armed'", coin)
        n = int(result.split()[-1])
        if n:
            logger.info("Spread plans: expired %d for %s", n, coin)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.error("Spread plans: expire for %s failed: %s", coin, exc)
        return 0


async def list_plans(limit: int = 20) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM spread_plans ORDER BY created_at DESC LIMIT $1", limit)
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["details"] = json.loads(d["details"]) if isinstance(d["details"], str) else d["details"]
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif type(v).__name__ == "Decimal":
                d[k] = float(v)
        out.append(d)
    return out


spread_monitor = SpreadMonitor()
