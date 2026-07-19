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
    if event in ("spread.hot", "spread.cooled"):
        symbol = data.get("symbol")
        state = "hot" if event == "spread.hot" else "cooled"
        return f"spread:{symbol}:{state}"
    if event in ("spread.executed", "spread.closed"):
        pid = data.get("position_id")
        state = "executed" if event == "spread.executed" else "closed"
        return f"spread-pos:{pid}:{state}" if pid else None
    # spread.leg_failure / spread.margin_warning: never dedup — every one matters
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

    if event == "spread.hot":
        symbol = data.get("symbol", "?")
        ann = _fmt(data.get("trailing_ann"), "{:+.1%}")
        plan = data.get("plan")
        if plan:
            body = (
                f"HL-vs-Blofin funding spread {ann}/yr (7d trail). "
                f"ARMED: ${_fmt(plan.get('notional_usd'), '{:.0f}')}/leg "
                f"short {plan.get('short_venue')} / long {plan.get('long_venue')}, "
                f"~${_fmt(plan.get('est_daily_usd'), '{:.2f}')}/day, "
                f"breakeven {_fmt(plan.get('breakeven_days'), '{:.1f}')}d, "
                f"abort ±{_fmt((plan.get('details') or {}).get('abort_pct'), '{:.0%}')}. "
                "Confirm to execute."
            )
        else:
            body = (f"HL-vs-Blofin funding spread {ann}/yr crossed the enter threshold. "
                    "No plan armed (concurrency cap or thin book) — see plans endpoint.")
        return {
            "title": f"⚡ Spread hot: {symbol} {ann}/yr",
            "body": body,
            "tag": f"spread:{symbol}",
            "renotify": True,
            "data": {"symbol": symbol, "plan_id": (plan or {}).get("id")},
        }

    if event == "spread.cooled":
        symbol = data.get("symbol", "?")
        ann = _fmt(data.get("trailing_ann"), "{:+.1%}")
        expired = data.get("expired_plans")
        suffix = f" {expired} armed plan(s) expired." if expired else ""
        return {
            "title": f"🧊 Spread cooled: {symbol} {ann}/yr",
            "body": f"Cross-venue funding spread dropped below the exit threshold.{suffix}",
            "tag": f"spread:{symbol}",
            "renotify": True,
            "data": {"symbol": symbol},
        }

    if event == "spread.executed":
        symbol = data.get("symbol", "?")
        return {
            "title": f"\u2705 Spread executed: {symbol}",
            "body": (
                f"{_fmt(data.get('size'), '{:.6g}')} {symbol} both legs filled \u2014 "
                f"short {data.get('short_venue')} @ {_fmt(data.get('short_price'))} / "
                f"long {data.get('long_venue')} @ {_fmt(data.get('long_price'))}, "
                f"${_fmt(data.get('notional_usd'), '{:.0f}')}/leg. Collecting."
            ),
            "tag": f"spread-pos:{data.get('position_id')}",
            "renotify": True,
            "data": {"symbol": symbol, "position_id": data.get("position_id")},
        }

    if event == "spread.closed":
        symbol = data.get("symbol", "?")
        reason = data.get("reason", "?")
        pnl = data.get("pnl_realized")
        pnl_str = f"  \u2022  PnL {_fmt(pnl, '{:+.2f}')}" if pnl is not None else ""
        emoji = "\U0001f6d1" if reason == "abort" else "\U0001f3c1"
        return {
            "title": f"{emoji} Spread closed: {symbol} ({reason})",
            "body": f"Both legs closed.{pnl_str}",
            "tag": f"spread-pos:{data.get('position_id')}",
            "renotify": True,
            "data": {"symbol": symbol, "position_id": data.get("position_id")},
        }

    if event == "spread.leg_failure":
        symbol = data.get("symbol", "?")
        return {
            "title": f"\U0001f6a8 SPREAD LEG FAILURE: {symbol}",
            "body": data.get("detail", "One leg failed \u2014 verify both venues manually NOW."),
            "tag": f"spread-fail:{symbol}",
            "renotify": True,
            "data": {"symbol": symbol},
        }

    if event == "spread.margin_warning":
        symbol = data.get("symbol", "?")
        return {
            "title": f"\u26a0\ufe0f Spread margin warning: {symbol}",
            "body": data.get("detail", "A leg is approaching liquidation \u2014 add margin or close."),
            "tag": f"spread-margin:{symbol}",
            "renotify": True,
            "data": {"symbol": symbol, "position_id": data.get("position_id")},
        }

    return None
