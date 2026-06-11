from typing import TypedDict, Optional, List
from datetime import datetime


class AgentState(TypedDict):
    # Identity
    strategy_id:        str
    strategy_config:    dict      # merged strategies + ai_strategy_config row
    risk_config:        dict      # ai_risk_config row

    # Trigger context
    trigger_reason:     str
    cycle_interval:     str
    triggered_at:       datetime

    # Position context
    position_open:      bool
    position_side:      Optional[str]
    position_entry_price: Optional[float]
    position_size:      Optional[float]
    position_unrealized_pnl_pct: Optional[float]
    position_opened_at: Optional[datetime]
    original_reasoning: Optional[str]

    # Ingested data (populated by node_ingest_replay)
    ohlcv_data:           Optional[dict]
    technical_indicators: Optional[dict]
    sentiment_data:       Optional[dict]
    news_data:            Optional[dict]
    market_context:       Optional[dict]
    data_fetch_errors:    List[str]

    # LLM output (populated by node_analyze_sim)
    llm_signal:      Optional[dict]
    context_tokens:  Optional[int]

    # Gate result (populated by node_guard_sim)
    gate_passed:            bool
    gate_rejection_reason:  Optional[str]
    resolved_size:          Optional[float]
    resolved_sl_price:      Optional[float]
    resolved_tp_price:      Optional[float]

    # Dispatch result (populated by node_dispatch_sim)
    webhook_fired:   bool
    webhook_status:  Optional[int]
    order_id:        Optional[str]
    signal_log_id:   Optional[int]

    # ── Sim-specific ──────────────────────────────────────────────────────────
    simulated_now:        Optional[datetime]    # candle close ts — NOT wall-clock
    backtest_run_id:      Optional[str]         # UUID of current backtest run
    sim_balance:          Optional[float]       # replaces HTTP call to executor
    sim_pnl_today:        Optional[float]       # replaces DB pnl_today lookup
    replay_candle_window: Optional[List[dict]]  # candles fed to node_ingest_replay
    sim_action:           Optional[str]         # action engine should process next
