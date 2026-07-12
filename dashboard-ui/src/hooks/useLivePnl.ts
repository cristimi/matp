import { useEffect, useRef, useState, useCallback } from 'react';

export interface PnlSnapshot {
  ts: number;
  strategies: Record<string, { open_pnl: number; position_ids: string[] }>;
  positions: Record<string, { mark_price: number; unrealized_pnl: number; liquidation_price: number | null }>;
  pending_orders: Record<string, { mark_price: number | null }>;
}

const WS_PNL_URL = (import.meta.env.VITE_WS_PNL_URL as string | undefined) || '/ws/pnl';

export function useLivePnl(): PnlSnapshot | null {
  const [snapshot, setSnapshot] = useState<PnlSnapshot | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = WS_PNL_URL.startsWith('/')
      ? `${protocol}//${window.location.host}${WS_PNL_URL}`
      : WS_PNL_URL;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.event === 'pnl') {
          setSnapshot({ ts: msg.ts, strategies: msg.strategies, positions: msg.positions, pending_orders: msg.pending_orders ?? {} });
        }
      } catch {}
    };

    ws.onclose = () => {
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return snapshot;
}
