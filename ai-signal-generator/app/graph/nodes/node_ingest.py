import asyncio
import logging

import httpx

from app.config import settings
from app.data.cvd import fetch_cvd
from app.data.divergence import detect_momentum_divergence
from app.data.econ_calendar import fetch_economic_calendar
from app.data.geometry import detect_geometry
from app.data.funding import fetch_funding_history
from app.data.indicators import compute_indicators
from app.data.liquidations import fetch_liquidations
from app.data.macro import fetch_btc_dominance, fetch_macro
from app.data.mtf import fetch_mtf_structure
from app.data.news import fetch_news_digest
from app.data.ohlcv import fetch_ohlcv
from app.data.orderbook import fetch_orderbook_depth
from app.data.sentiment import (
    fetch_fear_greed,
    fetch_funding_rate,
    fetch_open_interest_aggregate,
)
from app.data.volatility import compute_volatility_regime
from app.data.volume_profile import compute_volume_profile
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


async def _fetch_open_orders(strategy_id: str) -> list:
    """
    Fetch resting orders from the listener (never the executor directly — keeps
    the AI isolated from exchange specifics per the exchange-isolation rule).
    """
    url = f"{settings.matp_listener_url.rstrip('/')}/strategies/{strategy_id}/orders"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"unexpected /orders response: {data}")
    return data


async def node_ingest(state: AgentState) -> AgentState:
    sc     = state['strategy_config']
    errors = list(state.get('data_fetch_errors') or [])

    exchange_id = sc['exchange_id']

    base_asset   = sc.get('base_asset', 'BTC')
    quote_asset  = sc.get('quote_asset', 'USDT')
    ccxt_symbol  = f"{base_asset}/{quote_asset}"
    lookback_days = int(sc.get('lookback_days') or 90)
    interval      = state.get('cycle_interval', '4h')
    enabled_inds  = sc.get('indicators') or ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP']

    need_ohlcv = (sc.get('use_technical') or sc.get('use_geometry')
                  or sc.get('use_volume_profile') or sc.get('use_momentum_divergence')
                  or sc.get('use_volatility_regime'))
    need_open_orders = sc.get('use_geometry') or sc.get('use_limit_orders')

    # ── Fan out every independent external fetch concurrently ────────────
    # These used to be awaited one at a time — 15+ sequential HTTP round trips
    # that could add up to minutes even though the LLM call and dispatch are
    # each well under 10s. None of these depend on each other's *results*
    # (fetch_mtf_structure does its own internal OHLCV fetches, independent
    # of the primary one below), so start them all now and await each at its
    # original call site below — total wall time becomes ~max(latencies)
    # instead of sum(latencies), with every existing per-source try/except
    # and error message left untouched.
    ohlcv_task           = asyncio.create_task(fetch_ohlcv(exchange_id, ccxt_symbol, interval, lookback_days)) if need_ohlcv else None
    mtf_task             = asyncio.create_task(fetch_mtf_structure(exchange_id, ccxt_symbol)) if sc.get('use_mtf_structure') else None
    fear_greed_task      = asyncio.create_task(fetch_fear_greed()) if sc.get('use_fear_greed') else None
    funding_rate_task    = asyncio.create_task(fetch_funding_rate(exchange_id, ccxt_symbol)) if sc.get('use_funding_rate') else None
    open_interest_task   = asyncio.create_task(fetch_open_interest_aggregate(exchange_id, ccxt_symbol)) if sc.get('use_open_interest') else None
    funding_history_task = asyncio.create_task(fetch_funding_history(exchange_id, ccxt_symbol)) if sc.get('use_funding_history') else None
    news_task            = asyncio.create_task(fetch_news_digest(lookback_hours=24)) if sc.get('use_news') else None
    calendar_task        = asyncio.create_task(fetch_economic_calendar()) if sc.get('use_economic_calendar') else None
    btc_dominance_task   = asyncio.create_task(fetch_btc_dominance()) if sc.get('use_btc_dominance') else None
    macro_task           = asyncio.create_task(fetch_macro()) if sc.get('use_macro') else None
    open_orders_task     = asyncio.create_task(_fetch_open_orders(state['strategy_id'])) if need_open_orders else None
    orderbook_task       = asyncio.create_task(fetch_orderbook_depth(exchange_id, ccxt_symbol)) if sc.get('use_orderbook') else None
    cvd_task             = asyncio.create_task(fetch_cvd(exchange_id, ccxt_symbol)) if sc.get('use_cvd') else None
    liquidations_task    = asyncio.create_task(fetch_liquidations(exchange_id, ccxt_symbol)) if sc.get('use_liquidations') else None

    # ── OHLCV + Indicators + Geometry + local-compute fields ─────────────
    ohlcv_data           = None
    technical_indicators = None
    geometry_data        = None
    volume_profile       = None
    momentum_divergence  = None
    volatility_regime    = None

    # Any candle-derived source needs the OHLCV fetch, not just technical/geometry —
    # otherwise its toggle is a dead switch on strategies without those two.
    if need_ohlcv:
        try:
            ohlcv_data = await ohlcv_task
        except Exception as exc:
            errors.append(f"ohlcv:{exc}")
            logger.warning("OHLCV fetch failed: %s", exc)

        # Indicators/geometry must only see closed candles — the last raw
        # candle can still be filling in even after Phase 1's candle-close
        # buffer, and a pattern/level computed on a mutable candle would
        # shift or vanish between cycles as it fills in.
        closed_candles = ohlcv_data.get('closed_candles') if ohlcv_data else None
        if closed_candles:
            if sc.get('use_technical'):
                try:
                    technical_indicators = compute_indicators(closed_candles, enabled_inds)
                except Exception as exc:
                    errors.append(f"indicators:{exc}")
                    logger.warning("Indicator computation failed: %s", exc)

            if sc.get('use_geometry'):
                try:
                    geometry_data = detect_geometry(closed_candles) or None
                except Exception as exc:
                    errors.append(f"geometry:{exc}")
                    logger.warning("Geometry detection failed: %s", exc)

            if sc.get('use_volume_profile'):
                try:
                    volume_profile = compute_volume_profile(closed_candles)
                except Exception as exc:
                    errors.append(f"volume_profile:{exc}")
                    logger.warning("Volume profile computation failed: %s", exc)

            if sc.get('use_momentum_divergence'):
                try:
                    momentum_divergence = detect_momentum_divergence(closed_candles)
                except Exception as exc:
                    errors.append(f"momentum_divergence:{exc}")
                    logger.warning("Momentum divergence detection failed: %s", exc)

            if sc.get('use_volatility_regime'):
                try:
                    volatility_regime = compute_volatility_regime(closed_candles)
                except Exception as exc:
                    errors.append(f"volatility_regime:{exc}")
                    logger.warning("Volatility regime computation failed: %s", exc)

    # ── Multi-timeframe structure (own OHLCV fetches, fixed TFs) ─────────
    mtf_structure = None
    if sc.get('use_mtf_structure'):
        try:
            mtf_structure = await mtf_task
        except Exception as exc:
            errors.append(f"mtf_structure:{exc}")
            logger.warning("MTF structure fetch failed: %s", exc)

    # ── Sentiment ────────────────────────────────────────────────────────
    fear_greed    = None
    funding_rate  = None
    open_interest = None

    if sc.get('use_fear_greed'):
        try:
            fear_greed = await fear_greed_task
        except Exception as exc:
            errors.append(f"fear_greed:{exc}")

    if sc.get('use_funding_rate'):
        try:
            funding_rate = await funding_rate_task
        except Exception as exc:
            errors.append(f"funding_rate:{exc}")

    if sc.get('use_open_interest'):
        try:
            open_interest = await open_interest_task
        except Exception as exc:
            errors.append(f"open_interest:{exc}")

    # Sentiment-class data — grouped with the trio above, renders inside
    # the SENTIMENT section rather than as its own top-level section.
    funding_history = None
    if sc.get('use_funding_history'):
        try:
            funding_history = await funding_history_task
        except Exception as exc:
            errors.append(f"funding_history:{exc}")

    sentiment_data = {
        'fear_greed':      fear_greed,
        'funding_rate':    funding_rate,
        'open_interest':   open_interest,
        'funding_history': funding_history,
    }

    # ── News ─────────────────────────────────────────────────────────────
    news_data = None
    if sc.get('use_news'):
        try:
            news_data = await news_task
        except Exception as exc:
            errors.append(f"news:{exc}")

    # ── Economic calendar (scheduled events; dormant without FINNHUB key) ─
    calendar_data = None
    if sc.get('use_economic_calendar'):
        try:
            calendar_data = await calendar_task
        except Exception as exc:
            errors.append(f"economic_calendar:{exc}")
            logger.warning("Economic calendar fetch failed: %s", exc)

    # ── Macro ────────────────────────────────────────────────────────────
    btc_dominance = None
    macro_data    = None

    if sc.get('use_btc_dominance'):
        try:
            btc_dominance = await btc_dominance_task
        except Exception as exc:
            errors.append(f"btc_dominance:{exc}")

    if sc.get('use_macro'):
        try:
            macro_data = await macro_task
        except Exception as exc:
            errors.append(f"macro:{exc}")

    market_context = {'btc_dominance': btc_dominance, 'macro': macro_data}

    # ── Open orders (resting limits) — feeds the range-working actions ────
    # use_geometry keeps its historical coupling (geometric_range predates the
    # flag); use_limit_orders grants the same capability without geometry.
    open_orders = None
    if need_open_orders:
        try:
            open_orders = await open_orders_task
        except Exception as exc:
            errors.append(f"open_orders:{exc}")
            logger.warning("Open orders fetch failed: %s", exc)
            open_orders = []

    # ── Order book depth (snapshot at cycle time) ────────────────────────
    orderbook_data = None
    if sc.get('use_orderbook'):
        try:
            orderbook_data = await orderbook_task
        except Exception as exc:
            errors.append(f"orderbook:{exc}")
            logger.warning("Orderbook fetch failed: %s", exc)

    # ── Order flow (CVD from a public-trades snapshot) ───────────────────
    cvd_data = None
    if sc.get('use_cvd'):
        try:
            cvd_data = await cvd_task
        except Exception as exc:
            errors.append(f"cvd:{exc}")
            logger.warning("CVD fetch failed: %s", exc)

    # ── Liquidations (documented no-op on current exchanges — see fetcher) ─
    liquidation_data = None
    if sc.get('use_liquidations'):
        try:
            liquidation_data = await liquidations_task
        except Exception as exc:
            errors.append(f"liquidations:{exc}")
            logger.warning("Liquidations fetch failed: %s", exc)

    return {
        **state,
        'ohlcv_data':           ohlcv_data,
        'technical_indicators': technical_indicators,
        'geometry_data':        geometry_data,
        'volume_profile':       volume_profile,
        'momentum_divergence':  momentum_divergence,
        'volatility_regime':    volatility_regime,
        'mtf_structure':        mtf_structure,
        'open_orders':          open_orders,
        'orderbook_data':       orderbook_data,
        'cvd_data':             cvd_data,
        'sentiment_data':       sentiment_data,
        'news_data':            news_data,
        'calendar_data':        calendar_data,
        'liquidation_data':     liquidation_data,
        'market_context':       market_context,
        'data_fetch_errors':    errors,
    }
