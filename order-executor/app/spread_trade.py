"""
Cross-venue spread-trade execution — phases 2-3 of the staged spread harvest
(docs/design/SPREAD_HARVEST.md).

Phase 2 primitive: execute_plan() turns an 'armed' spread_plans row into a live
two-leg position — short leg first, then long leg, with rollback (if the second
leg fails, the first is immediately flattened; if the rollback itself fails, a
critical notification demands manual action). Sizes are quantized to the
coarser venue's minimum step so both venues accept the same base size.

Phase 3: close_spread() unwinds both legs (reasons: cooled | abort | manual),
and watcher_loop() polls every open position: the ±25% ABORT band from the plan
(gate 4 of the research: mandatory — retains 86% of P&L while capping leg loss)
closes both legs automatically; a leg drifting within 10% of its liquidation
price emits a margin warning (the configured venues expose no margin-add API,
so top-up is operator action — notified, not silent).

Entries require explicit confirmation (the /spread/execute endpoint); exits are
automatic. That is the "armed + confirm" ladder: humans admit risk, machines
shed it.
"""

import asyncio
import json
import logging
import math
import os
import uuid
from decimal import Decimal

import redis.asyncio as aioredis

from app.database import get_pool
from app.models import OrderRequest
from app.registry import registry

logger = logging.getLogger(__name__)

WATCH_INTERVAL_S = 60
STREAM_KEY = "notifications:events"
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379"), decode_responses=True)
    return _redis


async def _emit(event: str, payload: dict) -> None:
    """Notify; never raises — a notification failure must not affect trading."""
    try:
        data = {"event": event, **payload}
        await _get_redis().xadd(STREAM_KEY, {"data": json.dumps(data, default=str)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("spread emit %s failed: %s", event, exc)


async def _resolve_accounts() -> dict[str, str]:
    """exchange -> active account_id. Raises if a venue has no active account."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, exchange FROM exchange_accounts WHERE is_active = true")
    out = {}
    for r in rows:
        out.setdefault(r["exchange"], r["id"])
    for venue in ("hyperliquid", "blofin"):
        if venue not in out:
            raise ValueError(f"no active {venue} account configured")
    return out


def _quantize(size: float, step: float) -> float:
    if step <= 0:
        return round(size, 6)
    return math.floor(size / step + 1e-9) * step


async def _place_leg(account_id: str, symbol: str, side: str, size: float,
                     leverage: int, signal: str):
    adapter = await registry.get(account_id)
    req = OrderRequest(
        order_id=str(uuid.uuid4()), account_id=account_id, symbol=symbol,
        side=side, signal=signal, order_type="market",
        size=Decimal(str(size)), leverage=leverage, margin_mode="isolated")
    last = None
    for attempt in range(2):
        result = await adapter.submit_order(req)
        if result.success:
            return result
        last = result
        logger.warning("spread leg %s %s %s attempt %d failed: %s",
                       account_id, side, symbol, attempt + 1, result.error_msg)
        await asyncio.sleep(1)
    return last


async def execute_plan(plan_id: str) -> dict:
    pool = get_pool()
    plan = await pool.fetchrow("SELECT * FROM spread_plans WHERE id = $1", uuid.UUID(plan_id))
    if plan is None:
        raise ValueError(f"plan not found: {plan_id}")
    if plan["status"] != "armed":
        raise ValueError(f"plan {plan_id} is '{plan['status']}', not armed")
    coin = plan["coin"]
    open_row = await pool.fetchrow(
        "SELECT id FROM spread_positions WHERE coin = $1 AND status = 'open'", coin)
    if open_row:
        raise ValueError(f"open spread position already exists for {coin}")

    accounts = await _resolve_accounts()
    short_acct = accounts[plan["short_venue"]]
    long_acct = accounts[plan["long_venue"]]
    symbol = f"{coin}-USDT"
    notional = float(plan["notional_usd"])
    leverage = int(plan["leg_leverage"])

    short_adapter = await registry.get(short_acct)
    long_adapter = await registry.get(long_acct)
    mark = await short_adapter.get_mark_price(symbol) or await long_adapter.get_mark_price(symbol)
    if not mark:
        raise ValueError(f"no mark price for {symbol} on either venue")
    step = max(await short_adapter.get_min_order_size(symbol) or 0.0,
               await long_adapter.get_min_order_size(symbol) or 0.0)
    size = _quantize(notional / mark, step)
    if size <= 0:
        raise ValueError(f"notional ${notional:.0f} below min size {step} {coin} @ {mark}")

    logger.info("spread execute %s: %s short-%s/long-%s size %s @ ~%s",
                plan_id, coin, plan["short_venue"], plan["long_venue"], size, mark)
    short_res = await _place_leg(short_acct, symbol, "sell", size, leverage, "spread_open")
    if short_res is None or not short_res.success:
        err = short_res.error_msg if short_res else "no result"
        await pool.execute(
            "UPDATE spread_plans SET status='failed', updated_at=now(), "
            "details = details || $2::jsonb WHERE id=$1",
            plan["id"], json.dumps({"fail": f"short leg: {err}"}))
        raise RuntimeError(f"short leg failed: {err}")

    long_res = await _place_leg(long_acct, symbol, "buy", size, leverage, "spread_open")
    if long_res is None or not long_res.success:
        err = long_res.error_msg if long_res else "no result"
        logger.error("spread %s long leg failed (%s) — rolling back short leg", coin, err)
        rolled = False
        for _ in range(3):
            try:
                rb = await short_adapter.close_position(symbol, "short")
                if rb.success:
                    rolled = True
                    break
            except Exception as exc:  # noqa: BLE001
                logger.error("rollback close error: %s", exc)
            await asyncio.sleep(2)
        await pool.execute(
            "UPDATE spread_plans SET status='failed', updated_at=now(), "
            "details = details || $2::jsonb WHERE id=$1",
            plan["id"], json.dumps({"fail": f"long leg: {err}", "rolled_back": rolled}))
        if not rolled:
            await _emit("spread.leg_failure", {
                "symbol": coin,
                "detail": f"long leg failed AND rollback failed — NAKED SHORT {size} {symbol} "
                          f"on {plan['short_venue']} ({short_acct}). Close manually NOW."})
            raise RuntimeError(f"long leg failed and ROLLBACK FAILED — naked short on "
                               f"{plan['short_venue']}: {err}")
        await _emit("spread.leg_failure", {
            "symbol": coin,
            "detail": f"long leg failed ({err}); short leg rolled back cleanly. No exposure."})
        raise RuntimeError(f"long leg failed (short rolled back): {err}")

    row = await pool.fetchrow(
        """
        INSERT INTO spread_positions
            (plan_id, coin, symbol, short_venue, long_venue, short_account_id,
             long_account_id, notional_usd, leg_leverage, size, entry_mark,
             abort_up_price, abort_down_price, short_entry_price, long_entry_price,
             short_order_id, long_order_id, details)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb)
        RETURNING id
        """,
        plan["id"], coin, symbol, plan["short_venue"], plan["long_venue"],
        short_acct, long_acct, notional, leverage, size, mark,
        plan["abort_up_price"], plan["abort_down_price"],
        short_res.actual_fill_price, long_res.actual_fill_price,
        short_res.exchange_order_id, long_res.exchange_order_id,
        json.dumps({"short_raw": short_res.raw_response, "long_raw": long_res.raw_response},
                   default=str))
    await pool.execute(
        "UPDATE spread_plans SET status='executed', updated_at=now() WHERE id=$1", plan["id"])
    await _emit("spread.executed", {
        "symbol": coin, "position_id": str(row["id"]), "size": size,
        "short_venue": plan["short_venue"], "long_venue": plan["long_venue"],
        "short_price": short_res.actual_fill_price, "long_price": long_res.actual_fill_price,
        "notional_usd": notional})
    logger.info("spread %s executed: position %s", coin, row["id"])
    return {"position_id": str(row["id"]), "coin": coin, "size": size,
            "short_fill": str(short_res.actual_fill_price),
            "long_fill": str(long_res.actual_fill_price)}


async def close_spread(position_id: str | None = None, coin: str | None = None,
                       reason: str = "manual") -> dict:
    pool = get_pool()
    if position_id:
        pos = await pool.fetchrow(
            "SELECT * FROM spread_positions WHERE id = $1 AND status = 'open'",
            uuid.UUID(position_id))
    elif coin:
        pos = await pool.fetchrow(
            "SELECT * FROM spread_positions WHERE coin = $1 AND status = 'open'", coin.upper())
    else:
        raise ValueError("position_id or coin required")
    if pos is None:
        return {"closed": False, "detail": "no open spread position found"}

    symbol = pos["symbol"]
    results = {}
    ok = True
    for leg, acct, side in (("short", pos["short_account_id"], "short"),
                            ("long", pos["long_account_id"], "long")):
        try:
            adapter = await registry.get(acct)
            res = await adapter.close_position(symbol, side)
            results[leg] = res
            if not res.success:
                ok = False
                logger.error("spread close %s: %s leg close failed: %s",
                             pos["id"], leg, res.error_msg)
        except Exception as exc:  # noqa: BLE001
            ok = False
            results[leg] = None
            logger.error("spread close %s: %s leg close error: %s", pos["id"], leg, exc)

    short_r, long_r = results.get("short"), results.get("long")
    pnl = None
    if short_r and short_r.success and long_r and long_r.success:
        parts = [r.realized_pnl for r in (short_r, long_r) if r.realized_pnl is not None]
        pnl = float(sum(parts)) if parts else None
    status = ("aborted" if reason == "abort" else "closed") if ok else "close_failed"
    await pool.execute(
        """
        UPDATE spread_positions SET
            status=$2, close_reason=$3,
            closed_at=CASE WHEN $7 THEN now() END,
            short_close_price=$4, long_close_price=$5, pnl_realized=$6, updated_at=now()
        WHERE id=$1
        """,
        pos["id"], status, reason,
        short_r.actual_fill_price if short_r and short_r.success else None,
        long_r.actual_fill_price if long_r and long_r.success else None,
        pnl, ok)
    if ok:
        await _emit("spread.closed", {
            "symbol": pos["coin"], "position_id": str(pos["id"]), "reason": reason,
            "pnl_realized": pnl})
    else:
        await _emit("spread.leg_failure", {
            "symbol": pos["coin"],
            "detail": f"close ({reason}) left a leg open on position {pos['id']} — "
                      "verify both venues manually NOW."})
    return {"closed": ok, "position_id": str(pos["id"]), "status": status, "pnl": pnl}


async def watcher_loop() -> None:
    """Abort band + margin proximity for every open spread position."""
    logger.info("Spread watcher started (interval %ds)", WATCH_INTERVAL_S)
    while True:
        try:
            await _watch_once()
        except Exception as exc:  # noqa: BLE001 — watcher must survive anything
            logger.error("Spread watcher cycle failed: %s", exc)
        await asyncio.sleep(WATCH_INTERVAL_S)


async def _watch_once() -> None:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM spread_positions WHERE status = 'open'")
    for pos in rows:
        symbol = pos["symbol"]
        mark = None
        for acct in (pos["short_account_id"], pos["long_account_id"]):
            try:
                mark = await (await registry.get(acct)).get_mark_price(symbol)
            except Exception:  # noqa: BLE001
                mark = None
            if mark:
                break
        if not mark:
            logger.warning("Spread watcher: no mark for %s — skipped this cycle", symbol)
            continue
        if mark >= float(pos["abort_up_price"]) or mark <= float(pos["abort_down_price"]):
            logger.warning("Spread watcher: ABORT %s mark %s outside [%s, %s]",
                           pos["coin"], mark, pos["abort_down_price"], pos["abort_up_price"])
            await close_spread(position_id=str(pos["id"]), reason="abort")
            continue
        # margin proximity: warn when a leg is within 10% of its liquidation price
        for acct, side in ((pos["short_account_id"], "short"),
                           (pos["long_account_id"], "long")):
            try:
                positions = await (await registry.get(acct)).get_open_positions()
            except Exception:  # noqa: BLE001
                continue
            for p in positions:
                # adapters return Position models or dicts depending on venue
                get = (lambda k, _p=p: _p.get(k)) if isinstance(p, dict) \
                    else (lambda k, _p=p: getattr(_p, k, None))
                if get("symbol") != symbol or get("side") != side:
                    continue
                liq = get("liquidation_price")
                if not liq:
                    continue
                liq = float(liq)
                if liq > 0 and abs(mark - liq) / mark < 0.10:
                    await _emit("spread.margin_warning", {
                        "symbol": pos["coin"], "position_id": str(pos["id"]),
                        "leg": side, "mark": mark, "liquidation_price": liq,
                        "detail": f"{side} leg within 10% of liquidation "
                                  f"(mark {mark}, liq {liq}) — add margin or close."})


async def list_positions(limit: int = 20) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM spread_positions ORDER BY opened_at DESC LIMIT $1", limit)
    out = []
    for r in rows:
        d = dict(r)
        for k, v in list(d.items()):
            if isinstance(v, uuid.UUID):
                d[k] = str(v)
            elif hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif isinstance(v, Decimal):
                d[k] = float(v)
        d["details"] = json.loads(d["details"]) if isinstance(d["details"], str) else d["details"]
        out.append(d)
    return out
