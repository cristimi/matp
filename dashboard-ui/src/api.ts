const BASE = import.meta.env.VITE_API_BASE || '/api/dashboard';

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE}${path}`;
  console.log(`API Fetch: ${url}`);
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    console.error(`API Error: ${res.status} ${res.statusText}`);
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || err.detail || res.statusText);
  }
  const data = await res.json();
  console.log(`API Success: ${url}`, data);
  return data;
}

export const api = {
  get: <T>(path: string) => req<T>(path),
  post: <T>(path: string, body?: unknown) =>
    req<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body: unknown) =>
    req<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    req<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T>(path: string) => req<T>(path, { method: 'DELETE' }),
};

export interface TradingPair {
  base: string;
  quote: string;
  label: string;
}

export interface Order {
  id: string;
  received_at: string;
  pair: TradingPair;
  side: 'buy' | 'sell';
  signal: string;
  size: string;
  platform: string;
  status: string;
  strategy_id?: string;
  exchange_order_id?: string;
  actual_fill_price?: string;
  pnl?: string;
  error_msg?: string;
  signal_source?: string;
  indicator_price?: string;
}

export interface Position {
  id: string;
  pair: TradingPair;
  side: string;
  size: string;
  entryPx: string;
  markPx: string;
  closePx?: string;
  closing_price?: string;
  unrealizedPnl: string;
  realizedPnl: string;
  liquidationPx?: string;
  platform: string;
  status: 'open' | 'closed' | 'stale';
}

export interface Stats {
  period: string;
  total_orders: number;
  filled: number;
  failed: number;
  win_count: number;
  loss_count: number;
  long_count: number;
  short_count: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl: number;
  unrealized_pnl: number;
  by_platform: Record<string, Stats>;
  by_strategy: Record<string, Stats>;
}

export interface Strategy {
  id: string;
  name: string;
  symbol: string;
  account_id: string;
  pair: TradingPair;
  interval: string;
  platform: string;
  enabled: boolean;
  type: 'internal' | 'tradingview';
  last_signal_time?: number;
  tags: string[];
  default_leverage: number;
  margin_mode: 'isolated' | 'cross';
  max_leverage: number;
  allow_quote_variants: boolean;
  allow_cross_charting: boolean;
  pnl_today: number;
  pnl_total: number;
  last_signal_at: string | null;
  win_rate: number;
  total_trades: number;
  capital_allocation: number;
  initial_allocation?: number;
  allocation_peak?: number;
  margin_per_trade: number;
  max_drawdown_pct: number;
  total_return?: number;
}

export interface StrategyStats {
  strategy_id: string;
  period: string;
  trades_count: number;
  trades_won: number;
  trades_lost: number;
  long_count: number;
  short_count: number;
  win_rate: number;
  pnl_total: number;
  pnl_avg: number;
  max_drawdown: number;
  unrealized_pnl: number;
  profit_factor: number | null;
}

export interface EquityCurvePoint {
  date: string;
  pnl: number;
  cumulative: number;
}

export interface StrategyComparison {
  strategy_id: string;
  name: string;
  trades_count: number;
  trades_won: number;
  trades_lost: number;
  win_rate: number;
  pnl_total: number;
  max_drawdown: number;
  open_positions: number;
  profit_factor: number | null;
}

export async function fetchStrategies(): Promise<Strategy[]> {
  return api.get<Strategy[]>('/strategies');
}

export async function fetchStrategyStats(id: string, period: string): Promise<StrategyStats> {
  return api.get<StrategyStats>(`/strategies/${id}/stats?period=${period}`);
}

export async function fetchEquityCurve(id: string, days: number): Promise<EquityCurvePoint[]> {
  return [];
}

export async function fetchStrategyPositions(id: string): Promise<Position[]> {
  return api.get<Position[]>(`/strategies/${id}/positions`);
}

export async function fetchStrategyComparison(period: string): Promise<StrategyComparison[]> {
  return api.get<StrategyComparison[]>(`/strategies/comparison?period=${period}`);
}

// ---- Strategy Tree (Phase 2) ----

export interface StrategyTreeItem {
  id: string;
  name: string;
  symbol: string;
  account_label: string;
  account_exchange: string;
  account_mode: string;
  enabled: boolean;
  stop_reason: string | null;
  capital_allocation: number;
  total_return: number;
  open_positions_count: number;
  open_pnl: number;
  strategy_source: string;
  ai_llm_model: string | null;
  ai_llm_provider: string | null;
  pending_orders: PendingOrder[];
  last_position_opened_at: string | null;
  last_activity_at: string | null;
}

export interface PendingOrder {
  id: string;
  symbol: string;
  side: string;
  price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  mark_price: number | null;
  received_at: string;
  updated_at: string;
}

export interface TreePosition {
  id: string;
  side: string;
  base_asset: string;
  quote_asset: string;
  size: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number | null;
  realized_pnl: number | null;
  liquidation_price: number | null;
  leverage: number;
  opened_at: string;
  closed_at: string | null;
  close_reason: string | null;
  closing_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  status: 'open' | 'closed';
  account_label: string;
  account_exchange: string;
  order_count: number;
  price_mode: 'tick' | 'sigfig' | null;
  price_tick: number | null;
  price_sigfigs: number | null;
  size_dp: number | null;
}

export interface TreeOrderKey {
  avg_fill: number | null;
  realized: number | null;
  fee: number | null;
}

export interface TreeOrder {
  id: string;
  time: string;
  type: string;
  fill: number | null;
  delta: number | null;
  status: string;
  key: TreeOrderKey;
}

export interface OrderDetail {
  origin: {
    signal_source: string | null;
    raw_webhook: Record<string, unknown> | null;
  };
  justification: {
    signal_metadata: Record<string, unknown> | null;
    indicator_price: number | null;
    ai_reasoning: string | null;
    ai_confidence: number | null;
  };
  execution: {
    requested_price: number | null;
    exchange_fee: number | null;
    exchange_order_id: string | null;
    placed_at: string | null;
    filled_at: string | null;
    actual_fill_price: number | null;
    events: unknown[];
  } | null;
}

export async function fetchStrategyTree(): Promise<StrategyTreeItem[]> {
  return api.get<StrategyTreeItem[]>('/strategies/tree');
}

export async function fetchTreePositions(
  strategyId: string,
  scope: 'open' | 'all',
): Promise<TreePosition[]> {
  return api.get<TreePosition[]>(`/strategies/${strategyId}/positions?scope=${scope}`);
}

export async function fetchPositionOrders(positionId: string): Promise<TreeOrder[]> {
  return api.get<TreeOrder[]>(`/positions/${positionId}/orders`);
}

export async function fetchOrderDetail(orderId: string): Promise<OrderDetail> {
  return api.get<OrderDetail>(`/orders/${orderId}/detail`);
}
