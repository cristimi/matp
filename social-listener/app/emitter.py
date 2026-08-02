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
    # Both legs out. Only reachable on a hedge account holding a long AND a short
    # when a CLOSE post names no side — see _plan_close for why that reads as "all
    # out". Longs first is arbitrary; the two closes are independent.
    "close_all": ["close_long", "close_short"],
    # A partial close is the ordinary close signal carrying an explicit size and
    # no `target_position` — order-listener's close path reduces by that size and
    # clamps it to the size it believes is open, so we can never overshoot into a
    # full close by sending a stale number.
    "partial_close_long":  ["partial_close_long"],
    "partial_close_short": ["partial_close_short"],
    # A scale-in is the ordinary open signal carrying its own size. order-listener's
    # _apply_position_fill tops up the existing leg and blends the entry price, so
    # this grows the position instead of creating a second one.
    "add_long":  ["add_long"],
    "add_short": ["add_short"],
}

_PARTIAL = {"partial_close_long", "partial_close_short"}
_ADD = {"add_long", "add_short"}

# webhook step -> order side
_SIDE = {
    "open_long":   "buy",
    "close_short": "buy",
    "open_short":  "sell",
    "close_long":  "sell",
    "partial_close_long":  "sell",
    "partial_close_short": "buy",
    "add_long":  "buy",
    "add_short": "sell",
}

# webhook step -> the `signal` value order-listener's payload schema accepts.
# Everything else passes through unchanged.
_WEBHOOK_SIGNAL = {
    "partial_close_long":  "close_long",
    "partial_close_short": "close_short",
    "add_long":  "open_long",
    "add_short": "open_short",
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
    meta = {"source": settings.source_tag}
    if step in _PARTIAL or step in _ADD:
        meta["intent"] = step
    body = {
        "base_asset":      asset,
        "quote_asset":     settings.execution_quote_asset,
        "side":            _SIDE[step],
        "order_type":      "market",
        "size":            size,
        "signal":          _WEBHOOK_SIGNAL.get(step, step),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "token":           token,
        "signal_source":   "social_listener",
        "signal_metadata": meta,
    }
    if step in ("close_long", "close_short"):
        # State-sync close: order-listener looks up the open position and closes
        # it whole, so `size` is not what decides the closed quantity. A partial
        # close must NOT set this — it is precisely the size that decides there.
        body["target_position"] = "flat"
    return body


def standard_entry_size(strategy: dict, mark: float) -> float:
    """One standard entry in base asset — the unit an add is measured in."""
    return _size_for(strategy, mark)


async def emit(signal: str, asset: str, mark: float, strategy: dict,
               close_size: float | None = None,
               open_size: float | None = None) -> tuple[bool, str]:
    """Fire the webhook step(s) for one decided transition.

    `close_size` is the base-asset quantity for a partial close and is required for
    one — a partial with no size would fall back to entry sizing and could reduce
    far more than the trader asked for. `open_size` does the same for a scale-in,
    whose size is a fraction of a standard entry rather than a whole one.

    Returns (ok, detail). ok is True only when every step returned 2xx.
    """
    steps = _STEPS.get(signal)
    if not steps:
        return False, f"no webhook mapping for signal {signal!r}"

    if signal in _PARTIAL:
        if close_size is None or close_size <= 0:
            return False, "no close size — refusing to send an unsized partial close"
        size = round(float(close_size), 8)
    elif signal in _ADD:
        if open_size is None or open_size <= 0:
            return False, "no add size — refusing to send an unsized scale-in"
        size = round(float(open_size), 8)
    else:
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


async def adjust_levels(strategy: dict,
                        sl_price: float | None = None,
                        tp_price: float | None = None,
                        dry_run: bool = False,
                        side: str | None = None) -> tuple[bool, str]:
    """Set the stop and/or take-profit on one of the strategy's open positions.

    `side` ("LONG"/"SHORT") names the leg. Without it, order-listener picks the most
    recently opened position for the strategy — fine when there is only one, wrong
    the moment a long and a short are both open, because the stop would land on
    whichever happened to be newer.

    Uses order-listener's existing `/strategies/{id}/adjust-stops`, which resolves
    the position and hands off to order-executor's modify-stops — so, as everywhere
    else here, this service makes no exchange call and holds no credentials.

    ONE call carries both legs on purpose. modify-stops cancels every existing
    trigger and places only what it is handed, so moving the stop without re-sending
    the take-profit deletes the take-profit (and the reverse). Callers must always
    pass the levels they want to END UP with, not just the one that changed.

    Returns (ok, detail). ok requires every requested leg to be CONFIRMED resting:
    the endpoint's own contract is that `success` alone is not enough, because
    cancel-then-place is not atomic and a position can be left unprotected.
    """
    if sl_price is None and tp_price is None:
        return False, "no levels to set"

    url = f"{settings.listener_url}/strategies/{settings.execution_strategy_id}/adjust-stops"
    token = strategy["webhook_secret"]
    body: dict = {"token": token}
    if sl_price is not None:
        body["sl_price"] = float(sl_price)
    if tp_price is not None:
        body["tp_price"] = float(tp_price)
    if side is not None:
        body["side"] = side.lower()
    if dry_run:
        body["dry_run"] = True

    try:
        async with httpx.AsyncClient(timeout=settings.emit_timeout_seconds) as client:
            resp = await client.post(url, json=body, headers={"X-Webhook-Token": token})
    except Exception as e:  # noqa: BLE001
        return False, f"adjust-stops failed to send: {e}"

    if resp.status_code >= 300:
        return False, f"adjust-stops rejected {resp.status_code}: {resp.text[:300]}"

    data = resp.json()
    if data.get("simulated"):
        return True, (f"dry run: intended sl={data.get('intended_sl_price')} "
                      f"tp={data.get('intended_tp_price')}")

    legs_ok = data.get("success") is True
    if sl_price is not None and data.get("sl_ok") is not True:
        legs_ok = False
    if tp_price is not None and data.get("tp_ok") is not True:
        legs_ok = False
    if not legs_ok:
        return False, (f"levels not confirmed resting (success={data.get('success')}, "
                       f"sl_ok={data.get('sl_ok')}, tp_ok={data.get('tp_ok')}): "
                       f"{data.get('error') or data.get('error_msg')}")

    log.info("levels set %s sl=%s tp=%s -> %s", side or "-", sl_price, tp_price,
             resp.status_code)
    return True, f"{side or '-'} sl={sl_price} tp={tp_price} confirmed"
