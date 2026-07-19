"""
Funding-harvest planner — phase 1 of the staged automation
(docs/design/FUNDING_HARVEST.md).

When the funding monitor flips a supported coin hot, this module computes the
exact delta-neutral trade the operator would confirm: short HL perp at low
leverage + long HL Unit spot, equal notional. It probes the LIVE Hyperliquid
books to the target notional (spot Unit books are thin — measured slippage, not
assumed), prices both legs, and reports entry cost, daily funding income at
HL's own hourly rate (the income side; Binance trailing is only the signal),
and break-even days. Plans persist to funding_harvest_plans as 'armed'; the
regime cooling expires them. Nothing here places orders.
"""

import json
import logging

import httpx

from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

HL_INFO = "https://api.hyperliquid.xyz/info"

# Coins with HL Unit spot liquid enough to bother planning (design doc:
# AVAX/NEAR listed but thin — excluded until a probe proves otherwise).
SPOT_TOKEN = {"BTC": "UBTC", "ETH": "UETH", "SOL": "USOL", "DOGE": "UDOGE"}


async def _info(client: httpx.AsyncClient, payload: dict):
    resp = await client.post(HL_INFO, json=payload)
    resp.raise_for_status()
    return resp.json()


async def _resolve_spot_pair(client, token_name: str) -> tuple[str, str] | None:
    """Return (api_coin '@N', display 'UBTC/USDC') for token/USDC, or None."""
    meta = await _info(client, {"type": "spotMeta"})
    tok_idx = {t["name"]: t["index"] for t in meta.get("tokens", [])}
    base, usdc = tok_idx.get(token_name), tok_idx.get("USDC")
    if base is None or usdc is None:
        return None
    for pair in meta.get("universe", []):
        if pair.get("tokens") == [base, usdc]:
            return pair["name"], f"{token_name}/USDC"
    return None


def _walk_book(levels: list[dict], notional_usd: float) -> tuple[float, float] | None:
    """Average fill price and slippage (bps vs best) walking one book side to
    `notional_usd`. None if the visible book can't absorb it."""
    if not levels:
        return None
    best = float(levels[0]["px"])
    remaining, cost, qty = notional_usd, 0.0, 0.0
    for lvl in levels:
        px, sz = float(lvl["px"]), float(lvl["sz"])
        take_usd = min(remaining, px * sz)
        cost += take_usd
        qty += take_usd / px
        remaining -= take_usd
        if remaining <= 0:
            break
    if remaining > 0:
        return None
    avg = cost / qty
    return avg, abs(avg - best) / best * 1e4


async def _hl_perp_ctx(client, coin: str) -> tuple[float, float] | None:
    """(mark_price, hourly_funding_rate) for a perp coin."""
    meta, ctxs = await _info(client, {"type": "metaAndAssetCtxs"})
    for asset, ctx in zip(meta.get("universe", []), ctxs):
        if asset.get("name") == coin:
            return float(ctx["markPx"]), float(ctx["funding"])
    return None


async def build_plan(coin: str, trailing_ann: float, persist: bool = True) -> dict | None:
    """Compute (and optionally persist as 'armed') the paired trade for `coin`.
    Returns the plan dict, or None if the coin is unsupported or books are too
    thin. Never raises — the caller treats a failed plan as 'no plan'."""
    token = SPOT_TOKEN.get(coin)
    if token is None:
        return None
    try:
        capital = settings.funding_harvest_capital_usd
        leverage = settings.funding_harvest_perp_leverage
        # capital = spot notional + perp margin (N/lev)  =>  N = C / (1 + 1/lev)
        notional = capital / (1 + 1 / leverage)

        async with httpx.AsyncClient(timeout=20) as client:
            pair = await _resolve_spot_pair(client, token)
            if pair is None:
                logger.warning("Funding harvest: no HL spot pair for %s", token)
                return None
            api_coin, display = pair
            spot_book = await _info(client, {"type": "l2Book", "coin": api_coin})
            perp_book = await _info(client, {"type": "l2Book", "coin": coin})
            perp_ctx = await _hl_perp_ctx(client, coin)

        spot_walk = _walk_book(spot_book["levels"][1], notional)   # buy spot: asks
        perp_walk = _walk_book(perp_book["levels"][0], notional)   # sell perp: bids
        if spot_walk is None or perp_walk is None or perp_ctx is None:
            logger.warning("Funding harvest: %s book too thin for $%.0f notional",
                           coin, notional)
            return None
        spot_px, spot_slip = spot_walk
        perp_px, perp_slip = perp_walk
        perp_mark, hl_funding = perp_ctx

        fees_one_way = notional * (settings.hl_spot_taker_fee + settings.hl_perp_taker_fee)
        slip_cost = notional * (spot_slip + perp_slip) / 1e4
        entry_cost = fees_one_way + slip_cost
        roundtrip = 2 * fees_one_way + 2 * slip_cost      # assume similar exit
        hl_funding_ann = hl_funding * 24 * 365
        daily_funding = hl_funding * 24 * notional
        breakeven = roundtrip / daily_funding if daily_funding > 0 else None

        plan = {
            "coin": coin,
            "trailing_ann": round(trailing_ann, 4),
            "hl_funding_ann": round(hl_funding_ann, 4),
            "spot_pair": f"{display} ({api_coin})",
            "perp_symbol": coin,
            "capital_usd": capital,
            "notional_usd": round(notional, 2),
            "spot_qty": round(notional / spot_px, 8),
            "spot_price": spot_px,
            "perp_price": perp_px,
            "perp_leverage": leverage,
            "spot_slippage_bps": round(spot_slip, 2),
            "perp_slippage_bps": round(perp_slip, 2),
            "est_entry_cost_usd": round(entry_cost, 4),
            "est_roundtrip_usd": round(roundtrip, 4),
            "est_daily_funding_usd": round(daily_funding, 4),
            "breakeven_days": round(breakeven, 2) if breakeven is not None else None,
            "details": {"perp_mark": perp_mark, "hl_funding_hourly": hl_funding},
        }
        if persist:
            plan["id"] = await _persist(plan)
        logger.info(
            "Funding harvest: armed plan %s $%.0f/leg, ~$%.2f/day @HL %.1f%%/yr, "
            "breakeven %.1fd (spot slip %.1fbps)",
            coin, notional, daily_funding, hl_funding_ann * 100,
            breakeven if breakeven is not None else -1, spot_slip,
        )
        return plan
    except Exception as exc:  # noqa: BLE001 — a failed plan must not kill the monitor
        logger.error("Funding harvest: plan for %s failed: %s", coin, exc)
        return None


async def _persist(plan: dict) -> str:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # supersede any previous armed plan for this coin
            await conn.execute(
                "UPDATE funding_harvest_plans SET status='expired', updated_at=now() "
                "WHERE coin=$1 AND status='armed'", plan["coin"])
            row = await conn.fetchrow(
                """
                INSERT INTO funding_harvest_plans
                    (coin, trailing_ann, hl_funding_ann, spot_pair, perp_symbol,
                     capital_usd, notional_usd, spot_qty, spot_price, perp_price,
                     perp_leverage, spot_slippage_bps, perp_slippage_bps,
                     est_entry_cost_usd, est_roundtrip_usd, est_daily_funding_usd,
                     breakeven_days, details)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb)
                RETURNING id
                """,
                plan["coin"], plan["trailing_ann"], plan["hl_funding_ann"],
                plan["spot_pair"], plan["perp_symbol"], plan["capital_usd"],
                plan["notional_usd"], plan["spot_qty"], plan["spot_price"],
                plan["perp_price"], plan["perp_leverage"], plan["spot_slippage_bps"],
                plan["perp_slippage_bps"], plan["est_entry_cost_usd"],
                plan["est_roundtrip_usd"], plan["est_daily_funding_usd"],
                plan["breakeven_days"], json.dumps(plan["details"]),
            )
    return str(row["id"])


async def expire_plans(coin: str) -> int:
    """Regime cooled — expire armed plans for the coin. Returns count expired."""
    try:
        pool = get_pool()
        result = await pool.execute(
            "UPDATE funding_harvest_plans SET status='expired', updated_at=now() "
            "WHERE coin=$1 AND status='armed'", coin)
        n = int(result.split()[-1])
        if n:
            logger.info("Funding harvest: expired %d armed plan(s) for %s", n, coin)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.error("Funding harvest: expire for %s failed: %s", coin, exc)
        return 0


async def list_plans(limit: int = 20) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM funding_harvest_plans ORDER BY created_at DESC LIMIT $1", limit)
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
