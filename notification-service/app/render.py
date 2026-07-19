"""
Maps a notification event payload to a renderable {title, body, tag, ...} plus
the dedup_key used to avoid re-notifying for the same underlying occurrence.
"""


def _fmt(value, fmt="{:.4f}") -> str:
    if value is None:
        return "?"
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def compute_dedup_key(event: str, data: dict) -> str | None:
    if event in ("position.opened", "position.closed"):
        position_id = data.get("position_id")
        if not position_id:
            return None
        suffix = "opened" if event == "position.opened" else "closed"
        return f"position:{position_id}:{suffix}"
    if event in ("exchange.down", "exchange.up"):
        exchange = data.get("exchange")
        state = "down" if event == "exchange.down" else "up"
        return f"exchange:{exchange}:{state}"
    if event in ("service.down", "service.up"):
        service = data.get("service")
        state = "down" if event == "service.down" else "up"
        return f"service:{service}:{state}"
    if event in ("funding.hot", "funding.cooled"):
        symbol = data.get("symbol")
        state = "hot" if event == "funding.hot" else "cooled"
        return f"funding:{symbol}:{state}"
    return None


def render(event: str, data: dict) -> dict | None:
    """Return {title, body, tag, renotify, data} or None if the event type is unknown."""

    if event == "position.opened":
        position_id = data.get("position_id")
        symbol = data.get("symbol", "?")
        side = (data.get("side") or "?").upper()
        size = _fmt(data.get("size"), "{:.6g}")
        entry_price = _fmt(data.get("entry_price"))
        leverage = data.get("leverage")
        lev_suffix = f" {leverage}x" if leverage else ""
        emoji = "🟢" if side == "LONG" else "🔴"
        return {
            "title": f"{emoji} Opened {symbol} {side}{lev_suffix}",
            "body": f"{size} @ {entry_price}",
            "tag": f"position:{position_id}",
            "renotify": False,
            "data": {"position_id": position_id},
        }

    if event == "position.closed":
        position_id = data.get("position_id")
        symbol = data.get("symbol", "?")
        side = (data.get("side") or "?").upper()
        size = _fmt(data.get("size"), "{:.6g}")
        entry_price = _fmt(data.get("entry_price"))
        closing_price = _fmt(data.get("closing_price"))
        pnl = data.get("pnl_realized")
        pnl_str = _fmt(pnl, "{:+.2f}")
        close_reason = data.get("close_reason") or "manual"
        emoji = "🔴" if (pnl is not None and float(pnl) < 0) else "🟢"
        return {
            "title": f"{emoji} Closed {symbol} {side}",
            "body": (
                f"Entry {side} {size} @ {entry_price} → Exit @ {closing_price}"
                f"  •  PnL {pnl_str}  •  {close_reason}"
            ),
            "tag": f"position:{position_id}",
            "renotify": True,
            "data": {"position_id": position_id},
        }

    if event == "exchange.down":
        exchange = data.get("exchange", "?")
        return {
            "title": f"⚠️ {exchange} feed down",
            "body": "Market data ingestion heartbeat is stale.",
            "tag": f"exchange:{exchange}",
            "renotify": True,
            "data": {"exchange": exchange},
        }

    if event == "exchange.up":
        exchange = data.get("exchange", "?")
        return {
            "title": f"✅ {exchange} feed recovered",
            "body": "Market data ingestion heartbeat is fresh again.",
            "tag": f"exchange:{exchange}",
            "renotify": True,
            "data": {"exchange": exchange},
        }

    if event == "service.down":
        service = data.get("service", "?")
        return {
            "title": f"⚠️ {service} down",
            "body": "Health check is failing.",
            "tag": f"service:{service}",
            "renotify": True,
            "data": {"service": service},
        }

    if event == "service.up":
        service = data.get("service", "?")
        return {
            "title": f"✅ {service} recovered",
            "body": "Health check is passing again.",
            "tag": f"service:{service}",
            "renotify": True,
            "data": {"service": service},
        }

    if event == "funding.hot":
        symbol = data.get("symbol", "?")
        ann = _fmt(data.get("trailing_ann"), "{:.1%}")
        enter = _fmt(data.get("enter_ann"), "{:.0%}")
        plan = data.get("plan")
        if plan:
            body = (
                f"Signal {ann}/yr (Binance 3d). ARMED: ${_fmt(plan.get('notional_usd'), '{:.0f}')}/leg "
                f"short {plan.get('perp_symbol')} perp {plan.get('perp_leverage')}x + long {plan.get('spot_pair')}, "
                f"~${_fmt(plan.get('est_daily_funding_usd'), '{:.2f}')}/day at HL "
                f"{_fmt(plan.get('hl_funding_ann'), '{:.0%}')}/yr, "
                f"breakeven {_fmt(plan.get('breakeven_days'), '{:.1f}')}d. Confirm to execute."
            )
        else:
            body = (
                f"Trailing 3d funding annualizes above {enter}. "
                "No auto-plan (no liquid HL spot for this coin) — manual assessment only."
            )
        return {
            "title": f"🔥 Funding hot: {symbol} {ann}/yr",
            "body": body,
            "tag": f"funding:{symbol}",
            "renotify": True,
            "data": {"symbol": symbol, "venue": data.get("venue"),
                     "plan_id": (plan or {}).get("id")},
        }

    if event == "funding.cooled":
        symbol = data.get("symbol", "?")
        ann = _fmt(data.get("trailing_ann"), "{:.1%}")
        expired = data.get("expired_plans")
        suffix = f" {expired} armed plan(s) expired." if expired else ""
        return {
            "title": f"🧊 Funding cooled: {symbol} {ann}/yr",
            "body": f"Trailing 3d funding dropped below the exit threshold; harvest window closed.{suffix}",
            "tag": f"funding:{symbol}",
            "renotify": True,
            "data": {"symbol": symbol, "venue": data.get("venue")},
        }

    return None
