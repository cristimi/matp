"""
LLM health monitor — alerts when a strategy's cycles start dying at the LLM step.

A cycle that ends `llm_failed` produces no decision at all: `gate_passed` stays
false, no webhook fires, and nothing surfaces it. A strategy losing 30% of its
cycles looks identical from the outside to a strategy that simply chose to hold.
Over 2026-07-11..25 that was 821 cycles (~19%), with bnb-ai-scalper-edbb at 28.4%
— i.e. running at roughly a 72% duty cycle, unnoticed
(see .gemini/reports/2026-07-25_llm_failed_analysis.md).

Per strategy, over a rolling window, compute the `llm_failed` share and apply
hysteresis:
    ok       -> degraded   when share >= enter (default 20%)
    degraded -> ok         when share <  exit  (default 10%)

Transitions emit `llm.degraded` / `llm.recovered` onto the notifications:events
stream — the same producer contract as funding_monitor. State lives in a Redis
hash so restarts don't re-alert; the service's dedup window is the second guard.

The alert carries the dominant failure cause and model, because the fix differs
sharply by cause: quota means top up or repoint, config means the model can never
work, parse means the model can't hold the schema.
"""

import asyncio
import json
import logging
import time

from app.collector import get_redis
from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

STATE_KEY = "llm_health_monitor:state"
STREAM_KEY = "notifications:events"

# Ordered: first match wins, most specific first.
_CAUSES = (
    ("quota / rate limit",  ("ratelimiterror", "rate_limit", "resource_exhausted",
                             "429", "余额不足", "credit balance is too low")),
    ("billing",             ("402", "requires more credit", "prepayment credits")),
    ("missing credentials", ("missing credentials",)),
    ("config: model cannot serve this call",
                            ("does not support chat completions", "terms acceptance",
                             "did not match schema", "tool_use_failed")),
    ("timeout",             ("timeouterror", "timeout")),
    ("structured-output parse", ("parse failed", "parsing_error")),
)


def classify(error_text: str | None) -> str:
    low = (error_text or "").lower()
    for label, needles in _CAUSES:
        if any(n in low for n in needles):
            return label
    return "other"


class LLMHealthMonitor:
    def __init__(self):
        self._status: dict[str, dict] = {}
        self._last_run: float | None = None
        self._running = False

    def status(self) -> dict:
        return {
            "enabled": settings.llm_health_enabled,
            "window_h": settings.llm_health_window_h,
            "enter_pct": settings.llm_health_enter_pct,
            "exit_pct": settings.llm_health_exit_pct,
            "min_cycles": settings.llm_health_min_cycles,
            "last_run_epoch": self._last_run,
            "strategies": self._status,
        }

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        if not settings.llm_health_enabled:
            logger.info("LLM health monitor disabled via settings")
            return
        self._running = True
        while self._running:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("LLM health monitor cycle failed")
            await asyncio.sleep(settings.llm_health_interval_s)

    async def _cycle(self) -> None:
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT strategy_id,
                       count(*)                                                   AS cycles,
                       count(*) FILTER (WHERE gate_rejection_reason='llm_failed') AS failed,
                       mode() WITHIN GROUP (ORDER BY llm_model)
                           FILTER (WHERE gate_rejection_reason='llm_failed')      AS model,
                       (array_agg(reasoning ORDER BY triggered_at DESC)
                           FILTER (WHERE gate_rejection_reason='llm_failed'))[1]  AS last_error
                FROM ai_signal_log
                WHERE triggered_at > now() - ($1 || ' hours')::interval
                GROUP BY strategy_id
                """,
                str(settings.llm_health_window_h),
            )

        redis = get_redis()
        prev_state = await redis.hgetall(STATE_KEY) or {}
        self._status = {}

        for r in rows:
            sid, cycles, failed = r["strategy_id"], r["cycles"], r["failed"]
            if cycles < settings.llm_health_min_cycles:
                continue                      # too few cycles for the share to mean anything
            share = failed / cycles
            prev = prev_state.get(sid, "ok")
            state = prev

            if prev != "degraded" and share >= settings.llm_health_enter_pct:
                state = "degraded"
                await self._emit("llm.degraded", sid, share, cycles, failed,
                                 model=r["model"], cause=classify(r["last_error"]))
            elif prev == "degraded" and share < settings.llm_health_exit_pct:
                state = "ok"
                await self._emit("llm.recovered", sid, share, cycles, failed,
                                 model=r["model"])

            if state != prev:
                await redis.hset(STATE_KEY, sid, state)
            self._status[sid] = {
                "cycles": cycles, "failed": failed,
                "failed_pct": round(share * 100, 1), "state": state,
                "model": r["model"],
            }

        self._last_run = time.time()
        bad = [s for s, v in self._status.items() if v["state"] == "degraded"]
        logger.info("LLM health cycle: %d strategies checked, degraded=%s",
                    len(self._status), bad or "none")

    async def _emit(self, event: str, strategy_id: str, share: float,
                    cycles: int, failed: int, **extra) -> None:
        """xadd onto the notification stream. Never raises — an alert failure must
        not kill the monitor loop."""
        try:
            data = {
                "event": event,
                "strategy_id": strategy_id,
                "failed_pct": round(share * 100, 1),
                "cycles": cycles,
                "failed": failed,
                "window_h": settings.llm_health_window_h,
                "enter_pct": round(settings.llm_health_enter_pct * 100, 1),
                "exit_pct": round(settings.llm_health_exit_pct * 100, 1),
                **{k: v for k, v in extra.items() if v is not None},
            }
            await get_redis().xadd(STREAM_KEY, {"data": json.dumps(data)})
            logger.info("LLM health: emitted %s for %s (%.1f%% of %d cycles)",
                        event, strategy_id, share * 100, cycles)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM health: emit %s for %s failed: %s", event, strategy_id, exc)


llm_health_monitor = LLMHealthMonitor()
