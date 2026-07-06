import logging

import httpx

from app.config import settings
from app.data.divergence import detect_momentum_divergence
from app.data.geometry import detect_geometry
from app.data.indicators import compute_indicators
from app.data.macro import fetch_btc_dominance, fetch_macro
from app.data.news import fetch_news_digest
from app.data.ohlcv import fetch_ohlcv
from app.data.sentiment import fetch_fear_greed, fetch_funding_rate, fetch_open_interest
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

    # ── OHLCV + Indicators + Geometry + local-compute fields ─────────────
    ohlcv_data           = None
    technical_indicators = None
    geometry_data        = None
    volume_profile       = None
    momentum_divergence  = None
    volatility_regime    = None

    # Any candle-derived source needs the OHLCV fetch, not just technical/geometry —
    # otherwise its toggle is a dead switch on strategies without those two.
    if (sc.get('use_technical') or sc.get('use_geometry')
            or sc.get('use_volume_profile') or sc.get('use_momentum_divergence')
            or sc.get('use_volatility_regime')):
        try:
            ohlcv_data = await fetch_ohlcv(exchange_id, ccxt_symbol, interval, lookback_days)
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

    # ── Sentiment ────────────────────────────────────────────────────────
    fear_greed    = None
    funding_rate  = None
    open_interest = None

    if sc.get('use_fear_greed'):
        try:
            fear_greed = await fetch_fear_greed()
        except Exception as exc:
            errors.append(f"fear_greed:{exc}")

    if sc.get('use_funding_rate'):
        try:
            funding_rate = await fetch_funding_rate(exchange_id, ccxt_symbol)
        except Exception as exc:
            errors.append(f"funding_rate:{exc}")

    if sc.get('use_open_interest'):
        try:
            open_interest = await fetch_open_interest(exchange_id, ccxt_symbol)
        except Exception as exc:
            errors.append(f"open_interest:{exc}")

    sentiment_data = {
        'fear_greed':    fear_greed,
        'funding_rate':  funding_rate,
        'open_interest': open_interest,
    }

    # ── News ─────────────────────────────────────────────────────────────
    news_data = None
    if sc.get('use_news'):
        try:
            news_data = await fetch_news_digest(lookback_hours=24)
        except Exception as exc:
            errors.append(f"news:{exc}")

    # ── Macro ────────────────────────────────────────────────────────────
    btc_dominance = None
    macro_data    = None

    if sc.get('use_btc_dominance'):
        try:
            btc_dominance = await fetch_btc_dominance()
        except Exception as exc:
            errors.append(f"btc_dominance:{exc}")

    if sc.get('use_macro'):
        try:
            macro_data = await fetch_macro()
        except Exception as exc:
            errors.append(f"macro:{exc}")

    market_context = {'btc_dominance': btc_dominance, 'macro': macro_data}

    # ── Open orders (resting limits) — feeds the range-working actions ────
    open_orders = None
    if sc.get('use_geometry'):
        try:
            open_orders = await _fetch_open_orders(state['strategy_id'])
        except Exception as exc:
            errors.append(f"open_orders:{exc}")
            logger.warning("Open orders fetch failed: %s", exc)
            open_orders = []

    return {
        **state,
        'ohlcv_data':           ohlcv_data,
        'technical_indicators': technical_indicators,
        'geometry_data':        geometry_data,
        'volume_profile':       volume_profile,
        'momentum_divergence':  momentum_divergence,
        'volatility_regime':    volatility_regime,
        'open_orders':          open_orders,
        'sentiment_data':       sentiment_data,
        'news_data':            news_data,
        'market_context':       market_context,
        'data_fetch_errors':    errors,
    }
