const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/tester';

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(opts?.headers ?? {}) },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Strategies ────────────────────────────────────────────────────────────────

export interface Strategy {
  id:                  string;
  name:                string;
  symbol:              string;
  interval:            string;
  enabled:             boolean;
  description:         string | null;
  source_matp_id:      string | null;
  // ai config (may be null if not loaded)
  llm_provider:        string | null;
  llm_model:           string | null;
  ai_config_imported:  boolean | null;   // from migrate/from-matp
  ai_config_defaulted: boolean;          // true when from-matp used schema defaults
  // latest run snapshot (from strategies list join)
  latest_run_id:          string | null;
  latest_run_status:      string | null;
  latest_run_timeframe:   string | null;
  latest_run_win_rate:    number | null;
  latest_run_total_pnl:   number | null;
  latest_run_total_pnl_pct:   number | null;
  latest_run_total_trades:    number | null;
  latest_run_date_from:   string | null;
  latest_run_date_to:     string | null;
  latest_run_completed_at: string | null;
  created_at:             string;
}

export const listStrategies = () =>
  req<Strategy[]>('/strategies');

export const getStrategy = (id: string) =>
  req<Strategy>(`/strategies/${id}`);

// ── Runs ─────────────────────────────────────────────────────────────────────

export interface BacktestRun {
  id:                 string;
  strategy_id:        string;
  status:             string;
  timeframe:          string;
  date_from:          string;
  date_to:            string;
  initial_balance:    number;
  slippage_pct:       number;
  fee_pct:            number;
  lookback_days:      number;
  candles_processed:  number | null;
  total_candles:      number | null;
  total_trades:       number | null;
  winning_trades:     number | null;
  losing_trades:      number | null;
  win_rate:           number | null;
  total_pnl:          number | null;
  total_pnl_pct:      number | null;
  profit_factor:      number | null;
  max_drawdown_pct:   number | null;
  long_count:         number | null;
  short_count:        number | null;
  avg_win:            number | null;
  avg_loss:           number | null;
  llm_provider:       string | null;
  llm_model:          string | null;
  llm_failures:       number | null;
  llm_failure_rate:   number | null;
  error_message:      string | null;
  started_at:         string | null;
  completed_at:       string | null;
  created_at:         string;
  queue_position?:    number;
}

export const getRun = (id: string) =>
  req<BacktestRun>(`/runs/${id}`);

export const listRunsForStrategy = (strategyId: string) =>
  req<BacktestRun[]>(`/strategies/${strategyId}/runs`);

export interface RunCreateBody {
  strategy_id:        string;
  date_from:          string;
  date_to:            string;
  timeframe:          string;
  initial_balance:    number;
  slippage_pct:       number;
  fee_pct:            number;
  lookback_days:      number;
  dry_signal:         boolean;
  llm_model_override?: string;
}

export const createRun = (body: RunCreateBody) =>
  req<{ run_id: string; status: string }>('/runs', {
    method: 'POST',
    body:   JSON.stringify(body),
  });

export const cancelRun = (id: string) =>
  req<unknown>(`/runs/${id}/cancel`, { method: 'POST' });

// ── Cost estimation ───────────────────────────────────────────────────────────

export interface EstimateRequest {
  strategy_id:   string;
  date_from:     string;
  date_to:       string;
  timeframe:     string;
  lookback_days: number;
}

export interface EstimateResponse {
  strategy_id:        string;
  provider:           string;
  model:              string;
  active_candles:     number;
  warmup_candles:     number;
  total_candles:      number;
  tokens_per_cycle:   { input: number; output: number };
  total_tokens:       { input: number; output: number };
  pricing:            { input_per_1m_usd: number; output_per_1m_usd: number };
  estimated_cost_usd: number;
  note:               string;
}

export const estimateCost = (body: EstimateRequest) =>
  req<EstimateResponse>('/estimate-cost', {
    method: 'POST',
    body:   JSON.stringify(body),
  });

// ── Results ───────────────────────────────────────────────────────────────────

export interface EquityCurvePoint {
  candle_ts:        string;
  realized_balance: number;
  mark_balance:     number;
  trade_pnl:        number | null;
  drawdown_pct:     number | null;
}

export interface EquityCurveResponse {
  run_id: string;
  count:  number;
  items:  EquityCurvePoint[];
}

export const getEquityCurve = (runId: string) =>
  req<EquityCurveResponse>(`/runs/${runId}/equity-curve`);

export interface Position {
  id:               string;
  symbol:           string;
  side:             'long' | 'short';
  entry_price:      number;
  closing_price:    number | null;
  size:             number;
  pnl_realized:     number | null;
  fee_open:         number | null;
  fee_close:        number | null;
  status:           string;
  close_reason:     string | null;
  opened_at:        string;
  closed_at:        string | null;
}

export interface PositionsResponse {
  run_id:  string;
  total:   number;
  limit:   number;
  offset:  number;
  items:   Position[];
}

export const getPositions = (runId: string, limit = 200, offset = 0) =>
  req<PositionsResponse>(`/runs/${runId}/positions?limit=${limit}&offset=${offset}`);

export interface Signal {
  id:                    string;
  triggered_at:          string;
  proposed_action:       string;
  confidence:            number | null;
  reasoning:             string | null;
  gate_passed:           boolean;
  gate_rejection_reason: string | null;
  context_tokens:        number | null;
}

export interface SignalsResponse {
  run_id:  string;
  total:   number;
  limit:   number;
  offset:  number;
  items:   Signal[];
}

export const getSignals = (runId: string, gatePassed?: boolean, limit = 200) => {
  const q = gatePassed !== undefined ? `&gate_passed=${gatePassed}` : '';
  return req<SignalsResponse>(`/runs/${runId}/signals?limit=${limit}${q}`);
};

// ── Migration ─────────────────────────────────────────────────────────────────

export interface ToMaTPRequest { account_id: string }
export interface ToMaTPResponse {
  tester_strategy_id:  string;
  public_strategy_id:  string;
  account_id:          string;
  enabled:             boolean;
  webhook_enabled:     boolean;
}

export const promoteToMaTP = (strategyId: string, body: ToMaTPRequest) =>
  req<ToMaTPResponse>(`/migrate/to-matp/${strategyId}`, {
    method: 'POST',
    body:   JSON.stringify(body),
  });
