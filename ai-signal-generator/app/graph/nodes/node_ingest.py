import logging

from app.data.geometry import detect_geometry
from app.data.indicators import compute_indicators
from app.data.macro import fetch_btc_dominance, fetch_macro
from app.data.news import fetch_news_digest
from app.data.ohlcv import fetch_ohlcv
from app.data.sentiment import fetch_fear_greed, fetch_funding_rate, fetch_open_interest
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


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

    # ── OHLCV + Indicators + Geometry ────────────────────────────────────
    ohlcv_data           = None
    technical_indicators = None
    geometry_data        = None

    if sc.get('use_technical') or sc.get('use_geometry'):
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

    return {
        **state,
        'ohlcv_data':           ohlcv_data,
        'technical_indicators': technical_indicators,
        'geometry_data':        geometry_data,
        'sentiment_data':       sentiment_data,
        'news_data':            news_data,
        'market_context':       market_context,
        'data_fetch_errors':    errors,
    }
