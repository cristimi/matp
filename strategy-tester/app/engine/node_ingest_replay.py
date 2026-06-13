from app._vendored.indicators import compute_indicators
from app.graph.state import AgentState

_TF_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '8h': 28800, '1d': 86400,
}


def _tf_seconds(timeframe: str) -> int:
    return _TF_SECONDS.get(timeframe, 3600)


def _pct_change(candles: list, candles_back: int) -> float:
    if len(candles) <= candles_back:
        return 0.0
    cur  = candles[-1]['close']
    past = candles[-candles_back - 1]['close']
    return round((cur - past) / past * 100, 2) if past else 0.0


async def node_ingest_replay(state: AgentState) -> AgentState:
    sc            = state['strategy_config']
    candle_window = state.get('replay_candle_window') or []
    enabled_inds  = sc.get('indicators') or ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP']
    timeframe     = state.get('cycle_interval', '1h')

    ohlcv_data           = None
    technical_indicators = None

    if sc.get('use_technical') and candle_window:
        current = candle_window[-1]
        candles_per_day = max(1, 86400 // _tf_seconds(timeframe))
        ohlcv_data = {
            'symbol':               f"{sc.get('base_asset', '')}/{sc.get('quote_asset', '')}",
            'timeframe':            timeframe,
            'candles':              candle_window,
            'current_price':        current['close'],
            'price_change_24h_pct': _pct_change(candle_window, candles_per_day),
            'price_change_7d_pct':  _pct_change(candle_window, candles_per_day * 7),
        }
        technical_indicators = compute_indicators(candle_window, enabled_inds)

    return {
        **state,
        'ohlcv_data':           ohlcv_data,
        'technical_indicators': technical_indicators,
        'sentiment_data':       {'fear_greed': None, 'funding_rate': None, 'open_interest': None},
        'news_data':            None,
        'market_context':       {'btc_dominance': None, 'macro': None},
        'data_fetch_errors':    [],
    }
