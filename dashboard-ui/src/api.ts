const BASE = import.meta.env.VITE_API_BASE || '/api/dashboard';

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || err.detail || res.statusText);
  }
  return res.json();
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
  last_signal_time?: number;
}

export interface Position {
  symbol: string;
  side: string;
  size: string;
  entryPx: string;
  markPx: string;
  unrealizedPnl: string;
  liquidationPx?: string;
  platform: string;
}
