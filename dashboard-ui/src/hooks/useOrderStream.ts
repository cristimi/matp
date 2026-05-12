import { useEffect, useRef, useState, useCallback } from 'react';

export interface OrderEvent {
  event: string;
  order_id: string;
  status?: string;
  symbol?: string;
  platform?: string;
  timestamp: string;
}

const WS_URL = import.meta.env.VITE_WS_URL || '/ws/orders';

export function useOrderStream(onEvent?: (evt: OrderEvent) => void) {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<OrderEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = WS_URL.startsWith('/')
      ? `${protocol}//${window.location.host}${WS_URL}`
      : WS_URL;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);

    ws.onmessage = (e) => {
      try {
        const evt: OrderEvent = JSON.parse(e.data);
        if (evt.event === 'connected') return;
        setEvents((prev) => [evt, ...prev].slice(0, 100));
        onEvent?.(evt);
      } catch {}
    };

    ws.onclose = () => {
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }, [onEvent]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected, events };
}
