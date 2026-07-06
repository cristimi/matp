from typing import TypedDict, Optional, List
from datetime import datetime


class AgentState(TypedDict):
    # Identity
    strategy_id:        str
    strategy_config:    dict      # merged strategies + ai_strategy_config row
    risk_config:        dict      # ai_risk_config row

    # Trigger context
    trigger_reason:     str       # 'scheduled' | 'news_event' | 'volume_spike' | etc.
    cycle_interval:     str       # '4h' | '15m' | '5m' | 'immediate'
    triggered_at:       datetime

    # Position context (from MATP DB)
    position_open:      bool
    position_side:      Optional[str]      # 'long' | 'short'
    position_entry_price: Optional[float]
    position_size:      Optional[float]
    position_unrealized_pnl_pct: Optional[float]
    position_opened_at: Optional[datetime]
    original_reasoning: Optional[str]

    # Ingested data (Node 1 output)
    ohlcv_data:          Optional[dict]
    technical_indicators: Optional[dict]
    geometry_data:       Optional[dict]
    volume_profile:      Optional[dict]
    momentum_divergence: Optional[dict]
    volatility_regime:   Optional[dict]
    orderbook_data:      Optional[dict]
    sentiment_data:      Optional[dict]
    news_data:           Optional[dict]    # {'items': list, 'lookback_hours': int}
    market_context:      Optional[dict]
    data_fetch_errors:   List[str]

    # LLM output (Node 2 output)
    llm_signal:         Optional[dict]     # LLMSignalOutput as dict
    context_tokens:     Optional[int]

    # Gate result (Node 3 output)
    gate_passed:           bool
    gate_rejection_reason: Optional[str]
    resolved_size:         Optional[float]
    resolved_sl_price:     Optional[float]
    resolved_tp_price:     Optional[float]
    resolved_limit_price:     Optional[float]     # place_limit_* entry price / amend_order new price
    resolved_target_order_id: Optional[str]        # cancel_order / amend_order target

    # Open orders (Node 1 output, Phase 2)
    open_orders:           Optional[list]

    # Dispatch result (Node 4 output)
    webhook_fired:   bool
    webhook_status:  Optional[int]
    order_id:        Optional[str]
    signal_log_id:   Optional[int]
