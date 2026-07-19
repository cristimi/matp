"""
Funding-regime monitor — arms the delta-neutral funding-harvest trade.

Backed by the edge research on feat/edge-research (research/README.md,
.gemini/reports/edge-research-phase2-walkforward.md): the funding premium is
real but episodic — always-on harvesting nets ~0, but 2021-style regimes paid
+25% of notional in a year, delta-neutral. So we do not trade it automatically;
we watch for the regime and notify.

Every cycle, for each universe coin: fetch the last TRAIL Binance 8h funding
settlements and annualize the trailing mean (3 days of settlements, so a hot
reading IS the "sustained for days" condition). Hysteresis per coin:
  cool -> hot   when trailing annualized > enter (default 40%/yr)
  hot  -> cool  when trailing annualized < exit  (default 20%/yr)
Transitions emit `funding.hot` / `funding.cooled` onto the notifications:events
stream (same producer contract as order-listener's emit_notification); the
notification-service renders and pushes them. State lives in a Redis hash so
restarts don't re-alert; the service's 24h dedup window is the second guard.

Binance is the reference venue for the signal (deep history, 8h cadence, and
the research thresholds were calibrated on it). Execution venue analysis
(Hyperliquid hourly funding) stays a research open thread.
"""

import asyncio
import logging
import time

import httpx

from app.collector import get_redis
from app.config import settings

logger = logging.getLogger(__name__)

TRAIL = 9                      # settlements: 3 days at 8h cadence
ANN_FACTOR = 3 * 365           # 8h rate -> annualized
STATE_KEY = "funding_monitor:state"
STREAM_KEY = "notifications:events"
FAPI = "https://fapi.binance.com/fapi/v1/fundingRate"


class FundingMonitor:
    def __init__(self):
        self._status: dict[str, dict] = {}
        self._last_run: float | None = None
        self._running = False

    def status(self) -> dict:
        return {
            "enabled": settings.funding_monitor_enabled,
            "enter_ann": settings.funding_monitor_enter_ann,
            "exit_ann": settings.funding_monitor_exit_ann,
            "last_run_epoch": self._last_run,
            "coins": self._status,
        }

    async def run(self) -> None:
        if not settings.funding_monitor_enabled:
            logger.info("Funding monitor disabled via settings")
            return
        self._running = True
        logger.info(
            "Funding monitor started: universe=%s enter>%.0f%%/yr exit<%.0f%%/yr every %ds",
            settings.funding_monitor_symbols,
            settings.funding_monitor_enter_ann * 100,
            settings.funding_monitor_exit_ann * 100,
            settings.funding_monitor_interval_s,
        )
        while self._running:
            try:
                await self._check_all()
            except Exception as exc:  # noqa: BLE001 — monitor must survive any cycle error
                logger.error("Funding monitor cycle failed: %s", exc)
            await asyncio.sleep(settings.funding_monitor_interval_s)

    def stop(self) -> None:
        self._running = False

    async def _check_all(self) -> None:
        coins = [c.strip().upper() for c in settings.funding_monitor_symbols.split(",") if c.strip()]
        redis = get_redis()
        async with httpx.AsyncClient(timeout=20) as client:
            for coin in coins:
                sym = f"{coin}USDT"
                try:
                    resp = await client.get(FAPI, params={"symbol": sym, "limit": TRAIL})
                    resp.raise_for_status()
                    rates = [float(r["fundingRate"]) for r in resp.json()]
                except Exception as exc:  # noqa: BLE001 — one venue/coin failing is non-fatal
                    logger.warning("Funding monitor: fetch failed for %s: %s", sym, exc)
                    continue
                if len(rates) < TRAIL:
                    continue
                trail_ann = sum(rates[-TRAIL:]) / TRAIL * ANN_FACTOR
                prev = await redis.hget(STATE_KEY, coin) or "cool"
                state = prev
                if prev != "hot" and trail_ann > settings.funding_monitor_enter_ann:
                    state = "hot"
                    await self._emit("funding.hot", coin, trail_ann)
                elif prev == "hot" and trail_ann < settings.funding_monitor_exit_ann:
                    state = "cool"
                    await self._emit("funding.cooled", coin, trail_ann)
                if state != prev:
                    await redis.hset(STATE_KEY, coin, state)
                self._status[coin] = {
                    "trailing_ann_pct": round(trail_ann * 100, 2),
                    "state": state,
                }
        self._last_run = time.time()
        hot = [c for c, s in self._status.items() if s["state"] == "hot"]
        logger.info(
            "Funding monitor cycle: %d coins checked, hot=%s",
            len(self._status), hot or "none",
        )

    async def _emit(self, event: str, coin: str, trail_ann: float) -> None:
        """xadd onto the notification stream. Never raises — an alert failure
        must not kill the monitor loop."""
        try:
            import json
            data = {
                "event": event,
                "symbol": coin,
                "venue": "binance",
                "trailing_ann": round(trail_ann, 4),
                "enter_ann": settings.funding_monitor_enter_ann,
                "exit_ann": settings.funding_monitor_exit_ann,
            }
            await get_redis().xadd(STREAM_KEY, {"data": json.dumps(data)})
            logger.info("Funding monitor: emitted %s for %s (trailing %.1f%%/yr)",
                        event, coin, trail_ann * 100)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Funding monitor: emit %s for %s failed: %s", event, coin, exc)


funding_monitor = FundingMonitor()
