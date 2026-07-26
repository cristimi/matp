"""Turn a decided position change into real orders, via order-listener's webhook.

The listener never touches an exchange. It POSTs the same contract order-listener
already accepts from TradingView and the AI engine, so sizing (the
`margin_per_trade` clamp), the guaranteed stop-loss injection, leverage, margin
mode and every exchange call stay owned by order-listener / order-executor. This
module holds no credentials and knows no venue.

A FLIP is deliberately two calls — close to flat, then open the other way —
because that is what the webhook contract expresses and what the backtest priced
(`backtest_replay`: "a flip is two fills").

Emission is fail-closed: main.py only advances `social_position_state` when every
step succeeded, so the recorded stance never claims a position the exchange does
not hold.
"""
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# signal -> ordered webhook steps needed to reach the target state
_STEPS = {
    "open_long":     ["open_long"],
    "open_short":    ["open_short"],
    "close_long":    ["close_long"],
    "close_short":   ["close_short"],
    "flip_to_long":  ["close_short", "open_long"],
    "flip_to_short": ["close_long", "open_short"],
}

# webhook step -> order side
_SIDE = {
    "open_long":   "buy",
    "close_short": "buy",
    "open_short":  "sell",
    "close_long":  "sell",
}


def _size_for(strategy: dict, mark: float) -> float:
    """Quantity for one entry: margin × leverage / price.

    order-listener re-derives this and clamps anything larger, so it stays the
    single source of truth — this only avoids sending a deliberately wrong number.
    """
    margin = float(strategy.get("margin_per_trade") or 0)
    lev = int(strategy.get("default_leverage") or 1)
    return round((margin * lev) / mark, 8)


def _payload(step: str, asset: str, size: float, token: str) -> dict:
    body = {
        "base_asset":      asset,
        "quote_asset":     settings.execution_quote_asset,
        "side":            _SIDE[step],
        "order_type":      "market",
        "size":            size,
        "signal":          step,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "token":           token,
        "signal_source":   "social_listener",
        "signal_metadata": {"source": settings.source_tag},
    }
    if step in ("close_long", "close_short"):
        # State-sync close: order-listener looks up the open position and closes
        # it whole, so `size` is not what decides the closed quantity.
        body["target_position"] = "flat"
    return body


async def emit(signal: str, asset: str, mark: float, strategy: dict) -> tuple[bool, str]:
    """Fire the webhook step(s) for one decided transition.

    Returns (ok, detail). ok is True only when every step returned 2xx.
    """
    steps = _STEPS.get(signal)
    if not steps:
        return False, f"no webhook mapping for signal {signal!r}"
    if mark is None or mark <= 0:
        return False, "no mark price — refusing to send an unsized order"

    size = _size_for(strategy, mark)
    if size <= 0:
        return False, f"computed size {size} is not tradeable"

    url = f"{settings.listener_url}/webhook/{settings.execution_strategy_id}"
    token = strategy["webhook_secret"]
    done: list[str] = []

    async with httpx.AsyncClient(timeout=settings.emit_timeout_seconds) as client:
        for step in steps:
            try:
                resp = await client.post(
                    url,
                    json=_payload(step, asset, size, token),
                    headers={"X-Webhook-Token": token},
                )
            except Exception as e:  # noqa: BLE001
                return False, f"step {step} failed to send after {done}: {e}"

            if resp.status_code >= 300:
                return False, (f"step {step} rejected {resp.status_code}: "
                               f"{resp.text[:300]} (completed: {done})")
            done.append(f"{step}->{resp.json().get('order_id', '?')}")
            log.info("emitted %s for %s size=%s -> %s", step, asset, size, resp.status_code)

    return True, "; ".join(done)
