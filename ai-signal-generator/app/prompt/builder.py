"""
Prompt assembler for the AI signal generator.
Builds the full context string from AgentState data + a DB-loaded template.
No LLM calls — pure text assembly.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.prompt.templates import load_template

logger = logging.getLogger(__name__)

_SEP = '═' * 59


def _v(value: Any, default: str = 'N/A') -> str:
    if value is None:
        return default
    return str(value)


# ── Section renderers ──────────────────────────────────────────────────────────

def _render_header(state: dict) -> str:
    sc       = state['strategy_config']
    base     = sc.get('base_asset', 'BTC')
    quote    = sc.get('quote_asset', 'USDT')
    interval = state.get('cycle_interval', '?')
    ts       = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    trigger  = state.get('trigger_reason', 'scheduled')

    lines = [
        _SEP,
        f"MATP AI ANALYSIS — {base}-{quote} — {interval}",
        f"Generated: {ts} UTC",
        f"Analysis Trigger: {trigger}",
        _SEP,
    ]

    if state.get('position_open'):
        side      = _v(state.get('position_side'))
        entry     = _v(state.get('position_entry_price'))
        pnl_pct   = _v(state.get('position_unrealized_pnl_pct'))
        opened_at = state.get('position_opened_at')
        reasoning = state.get('original_reasoning')

        time_str = 'N/A'
        if opened_at is not None:
            try:
                if isinstance(opened_at, str):
                    opened_at = datetime.fromisoformat(opened_at)
                now = datetime.now(timezone.utc)
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                delta    = now - opened_at
                hours    = int(delta.total_seconds() // 3600)
                minutes  = int((delta.total_seconds() % 3600) // 60)
                time_str = f"{hours}h {minutes}m"
            except Exception:
                pass

        lines += [
            '',
            '⚠️  ACTIVE POSITION — EXIT EVALUATION MODE',
            f'Direction:     {side}',
            f'Entry Price:   {entry}',
            f'Current P&L:   {pnl_pct}%',
            f'Time Open:     {time_str}',
        ]
        if reasoning:
            lines.append(f'Original Thesis: "{reasoning}"')

    return '\n'.join(lines)


def _render_technical(state: dict) -> str:
    ohlcv    = state.get('ohlcv_data') or {}
    ind      = state.get('technical_indicators') or {}
    interval = state.get('cycle_interval', '?')

    lines = [f"TECHNICAL INDICATORS ({interval} timeframe):"]

    if ohlcv:
        lines += [
            f"Current Price:    {_v(ohlcv.get('current_price'))}",
            f"24h Change:       {_v(ohlcv.get('price_change_24h_pct'))}%",
            f"7d Change:        {_v(ohlcv.get('price_change_7d_pct'))}%",
            '',
        ]

    if 'rsi_14' in ind:
        lines.append(
            f"RSI(14):          {ind['rsi_14']} — {ind.get('rsi_interpretation', '')}"
        )
    if 'macd_hist' in ind:
        lines.append(
            f"MACD:             hist {ind['macd_hist']}, "
            f"signal cross {ind.get('macd_signal_bars', 0)} bars ago"
        )
    if 'ema_cross_status' in ind:
        cross = ind.get('ema_cross_status', '')
        ema50  = ind.get('ema_50')
        ema200 = ind.get('ema_200')
        suffix = f" (EMA50={ema50} / EMA200={ema200})" if ema50 and ema200 else ''
        lines.append(f"EMA 50/200:       {cross}{suffix}")
    if 'bb_interpretation' in ind:
        lines.append(f"BB:               {ind['bb_interpretation']}")
    if 'vwap_deviation_pct' in ind:
        lines.append(
            f"VWAP:             price {ind['vwap_deviation_pct']}% "
            f"{ind.get('vwap_direction', '')} VWAP"
        )
    if 'atr_14' in ind:
        lines.append(
            f"ATR(14):          {ind['atr_14']} ({ind.get('atr_pct_of_price', 'N/A')}% of price)"
        )
    if 'volume_vs_avg_pct' in ind:
        vol_pct = ind['volume_vs_avg_pct']
        direction = 'above' if vol_pct >= 0 else 'below'
        lines.append(
            f"Volume (vs 20MA):  {abs(vol_pct)}% {direction} average"
        )

    if 'support_1' in ind or 'resistance_1' in ind:
        lines += ['', 'Key Levels:']
        if 'support_1' in ind:
            lines.append(f"  Nearest Support:    {ind['support_1']}")
        if 'resistance_1' in ind:
            lines.append(f"  Nearest Resistance: {ind['resistance_1']}")

    return '\n'.join(lines)


def _render_open_orders(state: dict) -> str:
    sc     = state['strategy_config']
    orders = state.get('open_orders')

    if not sc.get('use_geometry') or orders is None:
        return ''

    lines = ['OPEN ORDERS (this strategy\'s resting limit orders):']
    if not orders:
        lines.append('None — no resting orders.')
    else:
        for o in orders:
            lines.append(
                f"  order_id={_v(o.get('order_id'))}  side={_v(o.get('side'))}  "
                f"price={_v(o.get('price'))}  size={_v(o.get('size'))}  status={_v(o.get('status'))}"
            )
        lines.append(
            'Use the order_id above as target_order_id for cancel_order/amend_order. '
            'Do not place a new limit on a side that already has a resting order.'
        )

    return '\n'.join(lines)


def _render_orderbook(state: dict) -> str:
    sc = state['strategy_config']
    ob = state.get('orderbook_data') or {}

    # Honest absence — same precedent as _render_geometry / _render_volume_profile.
    if not sc.get('use_orderbook') or not ob:
        return ''

    def _usd(v) -> str:
        return f"${v:,.0f}" if v is not None else 'N/A'

    def _wall(w) -> str:
        if not w:
            return 'none within band'
        return f"{_usd(w.get('size_usd'))} @ {_v(w.get('price'))}"

    imb = ob.get('depth_imbalance_ratio')
    if imb is None:
        imb_str = 'N/A'
    elif imb > 1.2:
        imb_str = f"{imb} (bids heavier)"
    elif imb < 0.8:
        imb_str = f"{imb} (asks heavier)"
    else:
        imb_str = f"{imb} (balanced)"

    lines = [
        'ORDER BOOK:',
        f"Bid Depth (±1% / ±2%):  {_usd(ob.get('bid_depth_1pct_usd'))} / {_usd(ob.get('bid_depth_2pct_usd'))}",
        f"Ask Depth (±1% / ±2%):  {_usd(ob.get('ask_depth_1pct_usd'))} / {_usd(ob.get('ask_depth_2pct_usd'))}",
        f"Depth Imbalance (1% bid/ask): {imb_str}",
        f"Largest Bid Wall:       {_wall(ob.get('largest_bid_wall'))}",
        f"Largest Ask Wall:       {_wall(ob.get('largest_ask_wall'))}",
        'Note: snapshot at analysis time — resting walls can be pulled; treat as corroboration only.',
    ]
    return '\n'.join(lines)


def _render_cvd(state: dict) -> str:
    sc = state['strategy_config']
    cd = state.get('cvd_data') or {}

    # Honest absence — same precedent as _render_geometry / _render_orderbook.
    if not sc.get('use_cvd') or not cd:
        return ''

    def _usd(v) -> str:
        if v is None:
            return 'not covered by snapshot'
        return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"

    lines = ['ORDER FLOW (CVD):']
    for key, label in (('cvd_1h', 'CVD (1h window):'), ('cvd_4h', 'CVD (4h window):')):
        if key in cd:
            lines.append(f"{label:<22}{_usd(cd[key])}")
    lines += [
        f"{'CVD (full snapshot):':<22}{_usd(cd.get('cvd_window_usd'))}",
        f"{'CVD Trend:':<22}{_v(cd.get('cvd_trend'))}",
        f"{'CVD/Price Divergence:':<22}{_v(cd.get('cvd_divergence'))}",
        f"Coverage:             {_v(cd.get('trades_count'))} trades spanning "
        f"{_v(cd.get('coverage_minutes'))} min (single snapshot, one API call — "
        'short coverage is a data limit, not low activity).',
    ]
    return '\n'.join(lines)


def _render_funding_history(sd: dict) -> list[str]:
    """
    Funding-history lines rendered inside the SENTIMENT section, under the
    Funding Rate line — the deliberate exception to one-renderer-one-section
    (a split funding read across two sections would be worse for the LLM).
    Returns [] when the data is absent — honest absence.
    """
    fh = sd.get('funding_history')
    if not fh:
        return []
    streak = fh.get('funding_streak')
    direction = fh.get('streak_direction', '?')
    return [
        f"Funding Percentile:   {_v(fh.get('funding_percentile'))} (vs trailing 30d window)",
        f"Funding Streak:       {_v(streak)} consecutive {direction} settlements",
    ]


def _render_sentiment(state: dict) -> str:
    sc = state['strategy_config']
    sd = state.get('sentiment_data') or {}

    body: list[str] = []

    if sc.get('use_fear_greed'):
        fg = sd.get('fear_greed')
        if fg:
            body.append(f"Fear & Greed Index:   {fg['value']} ({fg['label']})")

    if sc.get('use_funding_rate'):
        fr = sd.get('funding_rate')
        if fr:
            body.append(f"Funding Rate:         {fr['rate']}% ({fr['interpretation']})")

    if sc.get('use_funding_history'):
        body += _render_funding_history(sd)

    if sc.get('use_open_interest'):
        oi = sd.get('open_interest')
        if oi:
            oi_usd    = float(oi.get('open_interest_usd') or 0)
            oi_b      = oi_usd / 1_000_000_000
            ch        = oi.get('change_24h_pct', 0)
            ls_ratio  = oi.get('long_short_ratio')
            ls_interp = oi.get('ls_interpretation', 'data unavailable')
            venues    = oi.get('venues')
            label     = f"Open Interest ({'+'.join(venues)}):" if venues else 'Open Interest:'
            label     = f"{label:<22}"
            if not label.endswith(' '):
                label += ' '
            own_usd   = oi.get('own_venue_usd')
            suffix    = f"  [own venue: ${own_usd / 1_000_000_000:.2f}B]" if own_usd else ''
            body.append(f"{label}${oi_b:.2f}B ({ch}% 24h){suffix}")
            body.append(f"Long/Short Ratio:     {_v(ls_ratio)} ({ls_interp})")

    if not body:
        return ''
    return 'SENTIMENT:\n' + '\n'.join(body)


def _render_news(state: dict) -> str:
    nd = state.get('news_data')
    sc = state['strategy_config']
    lookback = sc.get('lookback_days', 1) * 24

    if isinstance(nd, dict):
        items    = nd.get('items', [])
        lookback = nd.get('lookback_hours', lookback)
    elif isinstance(nd, list):
        items = nd
    else:
        items = []

    lines = [f"NEWS DIGEST (last {lookback} hours):"]
    if items:
        for item in items[:10]:
            sev      = item.get('severity', 'medium').upper()
            headline = item.get('headline', '')
            lines.append(f"[{sev}] {headline}")
    else:
        lines.append("No significant news in the lookback window.")

    return '\n'.join(lines)


def _render_liquidations(state: dict) -> str:
    sc = state['strategy_config']
    ld = state.get('liquidation_data') or {}

    # Honest absence — currently always absent: no configured exchange exposes
    # liquidation data (see app/data/liquidations.py docstring / ROADMAP).
    if not sc.get('use_liquidations') or not ld:
        return ''

    def _usd(v) -> str:
        return f"${v:,.0f}" if v is not None else 'N/A'

    lines = [
        'LIQUIDATIONS:',
        f"Long Liqs (4h):       {_usd(ld.get('liq_long_volume_4h'))}",
        f"Short Liqs (4h):      {_usd(ld.get('liq_short_volume_4h'))}",
    ]
    clusters = ld.get('liq_clusters') or []
    if clusters:
        lines.append('Clusters near price:')
        for c in clusters:
            lines.append(f"  {_usd(c.get('volume_usd'))} @ {_v(c.get('price'))}")
    return '\n'.join(lines)


def _render_calendar(state: dict) -> str:
    sc = state['strategy_config']
    cd = state.get('calendar_data')

    # Honest absence: None = data missing (no key / provider error) → ''.
    # An empty events list is different — data present, genuinely quiet window —
    # and must say so: templates gate entries on this, so silence has to be
    # distinguishable from missing data.
    if not sc.get('use_economic_calendar') or cd is None:
        return ''

    horizon = cd.get('horizon_hours', 48)
    lines = [f"SCHEDULED EVENTS (next {horizon}h):"]
    events = cd.get('events') or []
    if not events:
        lines.append('No high-impact events in the window.')
    else:
        for ev in events:
            impact = (ev.get('impact') or '?').upper()
            lines.append(
                f"[{impact}] {_v(ev.get('event_name'))} — in {_v(ev.get('time_until_hours'))}h"
            )
    return '\n'.join(lines)


def _render_macro(state: dict) -> str:
    sc = state['strategy_config']
    mc = state.get('market_context') or {}

    lines: list[str] = []

    if sc.get('use_btc_dominance'):
        bd = mc.get('btc_dominance')
        if bd:
            lines.append(
                f"BTC Dominance:        {bd.get('btc_dominance', 'N/A')}% "
                f"({bd.get('btc_dom_trend', 'N/A')})"
            )

    if sc.get('use_macro'):
        m = mc.get('macro')
        if m:
            if 'dxy' in m:
                lines.append(f"DXY:                  {m['dxy']} ({m.get('dxy_trend', 'N/A')})")
            if 'us10y' in m:
                lines.append(f"US10Y:                {m['us10y']}% ({m.get('us10y_trend', 'N/A')})")

    return '\n'.join(lines)


def _render_mtf_structure(state: dict) -> str:
    sc  = state['strategy_config']
    mtf = state.get('mtf_structure') or []

    # Honest absence — same precedent as _render_geometry / _render_volume_profile.
    if not sc.get('use_mtf_structure') or not mtf:
        return ''

    lines = ['MULTI-TIMEFRAME STRUCTURE:']
    for entry in mtf:
        lines.append(
            f"{entry.get('tf', '?'):>4}: {_v(entry.get('trend_direction')):<9} — "
            f"{_v(entry.get('ema_posture'))}; swings {_v(entry.get('swing_structure'))}"
        )
    return '\n'.join(lines)


def _render_volatility_regime(state: dict) -> str:
    sc = state['strategy_config']
    vr = state.get('volatility_regime') or {}

    # Honest absence — same precedent as _render_geometry / _render_volume_profile.
    if not sc.get('use_volatility_regime') or not vr:
        return ''

    squeeze = vr.get('squeeze_flag')
    squeeze_str = (
        'YES — Bollinger width compressed (bottom of window), expansion likely'
        if squeeze else 'no'
    )

    lines = [
        'VOLATILITY REGIME:',
        f"ATR(14) Percentile:   {_v(vr.get('atr_percentile'))} (vs trailing window)",
        f"BB Width Percentile:  {_v(vr.get('bb_width_percentile'))}",
        f"Squeeze:              {squeeze_str}",
    ]
    return '\n'.join(lines)


def _render_momentum_divergence(state: dict) -> str:
    sc = state['strategy_config']
    md = state.get('momentum_divergence') or {}

    # Honest absence — same precedent as _render_geometry / _render_volume_profile.
    if not sc.get('use_momentum_divergence') or not md:
        return ''

    def _line(kind, bars_since) -> str:
        if kind in (None, 'none'):
            return 'none detected'
        return f"{kind} (swing confirmed {_v(bars_since)} bars ago)"

    lines = [
        'MOMENTUM DIVERGENCE:',
        f"RSI Divergence:       {_line(md.get('rsi_divergence'), md.get('rsi_divergence_bars_since'))}",
        f"MACD Divergence:      {_line(md.get('macd_divergence'), md.get('macd_divergence_bars_since'))}",
    ]
    return '\n'.join(lines)


def _render_volume_profile(state: dict) -> str:
    sc = state['strategy_config']
    vp = state.get('volume_profile') or {}

    # Honest absence: when the toggled-on source failed or degraded to None,
    # the DATA WARNINGS path already tells the LLM — emit nothing rather than
    # fabricating neutral values (same precedent as _render_geometry).
    if not sc.get('use_volume_profile') or not vp:
        return ''

    def _levels(levels: list) -> str:
        return ', '.join(str(p) for p in levels) if levels else 'none detected'

    lines = [
        'VOLUME PROFILE (lookback window):',
        f"POC (Point of Control): {_v(vp.get('poc_price'))}",
        f"Value Area High:        {_v(vp.get('value_area_high'))}",
        f"Value Area Low:         {_v(vp.get('value_area_low'))}",
        f"HVN Levels:             {_levels(vp.get('hvn_levels') or [])}",
        f"LVN Levels:             {_levels(vp.get('lvn_levels') or [])}",
    ]
    return '\n'.join(lines)


def _render_geometry(state: dict) -> str:
    sc = state['strategy_config']
    gd = state.get('geometry_data') or {}

    if not sc.get('use_geometry') or not gd:
        return ''

    shape        = gd.get('shape', '')
    fit_quality  = gd.get('fit_quality')
    unclassified = shape == 'no_pattern'
    reliable     = fit_quality == 'strong'

    # A no_pattern shape with a strong trendline fit means the boundaries are real
    # but don't match any named pattern — surface it labeled as unclassified rather
    # than silently dropping a strong fit. A genuinely noisy fit (no_pattern + weak,
    # e.g. too few swings) is now surfaced too, honestly labeled as unreliable,
    # instead of being dropped — dropping it read to the LLM as "geometry data is
    # missing" rather than "geometry was checked and found no reliable structure".
    if unclassified:
        label = (
            'Unclassified Structure (no named pattern, but a strong trendline fit)'
            if reliable else
            'No Reliable Pattern (weak trendline fit)'
        )
    else:
        label = shape.replace('_', ' ').title()

    conv = gd.get('convergence_pct_per_bar', 0.0)

    # position_in_range_pct is computed off the fitted boundaries, so when the fit
    # itself isn't strong the position readout inherits that noise — flag it rather
    # than presenting it as a dependable 0-100 locator.
    position_suffix = (
        '%  (0=at lower boundary, 100=at upper)' if reliable else
        "%  (UNRELIABLE — fit_quality is not 'strong'; boundary may be noisy)"
    )

    lines = [
        'GEOMETRIC PATTERN:',
        f"Detected Shape:       {label}",
        f"Fit Quality:          {_v(fit_quality)}",
        f"Upper Boundary:       {_v(gd.get('upper_boundary'))}",
        f"Lower Boundary:       {_v(gd.get('lower_boundary'))}",
        f"Upper Touches:        {_v(gd.get('upper_touches'))}",
        f"Lower Touches:        {_v(gd.get('lower_touches'))}",
        f"Position in Range:    {_v(gd.get('position_in_range_pct'))}{position_suffix}",
        f"Pattern Age:          {_v(gd.get('pattern_age_bars'))} bars",
    ]

    if conv > 0:
        lines.append(f"Convergence Rate:     +{conv}% of price per bar (boundaries closing in)")
    elif conv < 0:
        lines.append(f"Divergence Rate:      {conv}% of price per bar (boundaries widening)")
    else:
        lines.append("Convergence Rate:     0 (parallel boundaries)")

    return '\n'.join(lines)


def _render_portfolio(state: dict) -> str:
    lines = [
        'PORTFOLIO CONTEXT:',
        'Account Balance:      (resolved at execution time)',
    ]
    if not state.get('position_open'):
        lines.append("Last Signal:          N/A")

    return '\n'.join(lines)


def _render_task(state: dict) -> str:
    position_open = state.get('position_open', False)
    position_side = state.get('position_side') or 'long'

    lines = [_SEP, 'YOUR TASK:']

    if position_open:
        close_action = f"close_{position_side}"
        lines += [
            f"Evaluate whether the original thesis for this {position_side} position is still valid.",
            "Consider all new data since the position was opened.",
            'If the thesis is intact: output "hold" or "adjust_stops" with updated levels.',
            'If the thesis is weakening: output "partial_close".',
            f'If the thesis is invalidated or a new risk is present: output "{close_action}".',
            'If the position is showing strong continuation: output "increase" (only if within size limits).',
        ]
    else:
        lines += [
            "Identify whether current market conditions present a high-conviction trade setup.",
            'If a setup exists: output "open_long" or "open_short" with full parameters.',
            'If conditions are unclear or insufficient confluence: output "hold".',
        ]

    lines += [
        '',
        'CONFIDENCE SCALE:',
        '0.50-0.65: Speculative — below threshold, will be rejected',
        '0.65-0.75: Moderate — meets minimum threshold',
        '0.75-0.85: High conviction — clear confluence',
        '0.85-0.95: Exceptional setup — multiple independent signals aligned',
        'Never output confidence above 0.95.',
        '',
        'OUTPUT: Structured JSON only. reasoning field must cite specific indicator values.',
        _SEP,
    ]

    return '\n'.join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

async def build_prompt(state: dict, db_pool) -> str:
    """
    Assemble the full LLM prompt from AgentState and a DB-loaded template.
    No LLM calls. Returns the complete prompt string.
    """
    sc          = state['strategy_config']
    template_id = sc.get('template_id', 'trend_following')
    template    = await load_template(template_id, db_pool)

    sections: list[str] = []

    # 1. Header — always included; contains position warning if position_open
    sections.append(_render_header(state))

    # 2. Technical — only if toggled on and OHLCV data is available
    if sc.get('use_technical') and state.get('ohlcv_data'):
        sections.append(_render_technical(state))

    # 2.1. Multi-timeframe structure — immediately after Technical
    if sc.get('use_mtf_structure'):
        mtf = _render_mtf_structure(state)
        if mtf:
            sections.append(mtf)

    # 2.2. Volatility regime — right after Technical
    if sc.get('use_volatility_regime'):
        vr = _render_volatility_regime(state)
        if vr:
            sections.append(vr)

    # 2.3. Momentum divergence
    if sc.get('use_momentum_divergence'):
        md = _render_momentum_divergence(state)
        if md:
            sections.append(md)

    # 2.4. Volume profile — just before Geometry, so boundary/HVN confluence reads adjacently
    if sc.get('use_volume_profile'):
        vp = _render_volume_profile(state)
        if vp:
            sections.append(vp)

    # 2.5. Geometry — only if toggled on and geometry data is available
    if sc.get('use_geometry'):
        g = _render_geometry(state)
        if g:
            sections.append(g)

    # 2.6. Open orders — only if toggled on (geometry gates the range-working actions)
    if sc.get('use_geometry'):
        oo = _render_open_orders(state)
        if oo:
            sections.append(oo)

    # 2.7. Order book — only if toggled on and snapshot is available
    if sc.get('use_orderbook'):
        ob = _render_orderbook(state)
        if ob:
            sections.append(ob)

    # 2.8. Order flow (CVD) — only if toggled on and snapshot is available
    if sc.get('use_cvd'):
        cv = _render_cvd(state)
        if cv:
            sections.append(cv)

    # 2.9. Liquidations — only if toggled on and data is available
    # (currently never: no configured exchange exposes it — see ROADMAP)
    if sc.get('use_liquidations'):
        lq = _render_liquidations(state)
        if lq:
            sections.append(lq)

    # 3. Sentiment — only if at least one sentiment source is toggled on
    # (funding_history counts: it renders inside this section, so without it
    # in the gate the toggle would be dead on a strategy with the trio off)
    if (sc.get('use_fear_greed') or sc.get('use_funding_rate')
            or sc.get('use_open_interest') or sc.get('use_funding_history')):
        s = _render_sentiment(state)
        if s:
            sections.append(s)

    # 4. News — only if toggled on and digest is available
    if sc.get('use_news') and state.get('news_data'):
        sections.append(_render_news(state))

    # 4.5. Scheduled events — right after News (past news, then future events)
    if sc.get('use_economic_calendar'):
        cal = _render_calendar(state)
        if cal:
            sections.append(cal)

    # 5. Macro — only if at least one macro source is toggled on
    if sc.get('use_btc_dominance') or sc.get('use_macro'):
        m = _render_macro(state)
        if m:
            sections.append(m)

    # 6. Portfolio — always included
    sections.append(_render_portfolio(state))

    # 7. Data warnings — inserted between portfolio and instructions if errors occurred
    errors = state.get('data_fetch_errors') or []
    if errors:
        warn_lines = ['DATA WARNINGS:'] + [f'  - {e}' for e in errors]
        sections.append('\n'.join(warn_lines))

    # 8. Strategy instructions — from DB template, always included
    if template:
        inst = ['STRATEGY INSTRUCTIONS:', template['system_prompt']]
        custom = sc.get('custom_instructions')
        if custom:
            inst += ['', 'ADDITIONAL RULES:', custom]
        sections.append('\n'.join(inst))

    # 9. Task section — always included
    sections.append(_render_task(state))

    return '\n\n'.join(filter(None, sections))


def get_estimated_tokens(prompt: str) -> int:
    return len(prompt) // 4
