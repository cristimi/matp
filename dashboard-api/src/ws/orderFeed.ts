import http from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import { getSubscriber } from '../redis';

const CHANNELS = ['orders:received', 'orders:routed', 'orders:filled', 'orders:failed'];

export function initWebSocket(server: http.Server): void {
  const wss = new WebSocketServer({ server, path: '/ws/orders' });

  const clients = new Set<WebSocket>();

  wss.on('connection', (ws) => {
    clients.add(ws);
    console.log(`WS client connected. Total: ${clients.size}`);

    ws.on('close', () => {
      clients.delete(ws);
      console.log(`WS client disconnected. Total: ${clients.size}`);
    });

    ws.send(JSON.stringify({ event: 'connected', message: 'MATP order feed' }));
  });

  // Subscribe to all order channels and broadcast to WebSocket clients
  const sub = getSubscriber();
  for (const channel of CHANNELS) {
    sub.subscribe(channel, (message) => {
      const dead: WebSocket[] = [];
      for (const ws of clients) {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(message);
        } else {
          dead.push(ws);
        }
      }
      dead.forEach((ws) => clients.delete(ws));
    });
  }

  console.log(`WebSocket server ready. Subscribed to: ${CHANNELS.join(', ')}`);
}
