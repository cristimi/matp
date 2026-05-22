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
};

// Types
export interface Order {
  id: string;
  received_at: string;
  symbol: string;
  side: 'buy' | 'sell';
  signal: string;
  size: string;
  platform: string;
  status: string;
  strategy_id?: string;
  exchange_order_id?: string;
  pnl?: string;
  error_msg?: string;
  signal_source?: string;
  indicator_price?: string;
}

export interface Stats {
  period: string;
  total_orders: number;
  filled: number;
  failed: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl: number;
  by_platform: Record<string, Stats>;
  by_strategy: Record<string, Stats>;
}

export interface Strategy {
  id: string;
  name: string;
  symbol: string;
  interval: string;
  platform: string;
  enabled: boolean;
  type: 'internal' | 'tradingview';
  last_signal_time?: number;
  tags: string[];
  max_position_size: number;
  max_leverage: number;
  max_daily_drawdown_percent: number;
  pnl_today: number;
  pnl_total: number;
  win_count: number;
  loss_count: number;
  last_signal_at: string | null;
  win_rate: number;
  total_trades: number;
}

export interface StrategyStats {
  strategy_id: string;
  period: string;
  trades_count: number;
  trades_won: number;
  win_rate: number;
  pnl_total: number;
  pnl_avg: number;
  max_drawdown: number;
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
  win_rate: number;
  pnl_total: number;
  max_drawdown: number;
  open_positions: number;
}

export async function fetchStrategies(): Promise<Strategy[]> {
  return api.get<Strategy[]>('/strategies');
}

export async function fetchStrategyStats(id: string, period: string): Promise<StrategyStats> {
  return api.get<StrategyStats>(`/strategies/${id}/stats?period=${period}`);
}

export async function fetchEquityCurve(id: string, days: number): Promise<EquityCurvePoint[]> {
  // TODO: endpoint not yet implemented in dashboard-api
  return [];
}

export async function fetchStrategyPositions(id: string): Promise<Position[]> {
  return api.get<Position[]>(`/strategies/${id}/positions`);
}

export async function fetchStrategyComparison(period: string): Promise<StrategyComparison[]> {
  return api.get<StrategyComparison[]>(`/strategies/comparison?period=${period}`);
}

export interface Position {
  id: string;
  symbol: string;
  side: string;
  size: string;
  entryPx: string;
  markPx: string;
  closePx?: string;
  unrealizedPnl: string;
  liquidationPx?: string;
  platform: string;
  status: 'open' | 'closed';
}
