import { WebSocketServer, WebSocket } from 'ws';
import { getSubscriber } from '../redis';
import { SNAPSHOT_CHANNEL, getLastSnapshot } from '../livePnl';

export function createPnlWebSocket(): WebSocketServer {
  const wss = new WebSocketServer({ noServer: true });
  const clients = new Set<WebSocket>();

  wss.on('connection', (ws) => {
    clients.add(ws);
    console.log(`PnL WS client connected. Total: ${clients.size}`);

    ws.on('close', () => {
      clients.delete(ws);
      console.log(`PnL WS client disconnected. Total: ${clients.size}`);
    });

    ws.send(JSON.stringify({ event: 'connected', message: 'MATP pnl feed' }));

    const snap = getLastSnapshot();
    if (snap) {
      ws.send(JSON.stringify({ event: 'pnl', ...snap }));
    }
  });

  const sub = getSubscriber();
  sub.subscribe(SNAPSHOT_CHANNEL, (message) => {
    const snap = JSON.parse(message);
    const envelope = JSON.stringify({ event: 'pnl', ...snap });
    const dead: WebSocket[] = [];
    for (const ws of clients) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(envelope);
      } else {
        dead.push(ws);
      }
    }
    dead.forEach(ws => clients.delete(ws));
  });

  console.log('PnL WebSocket server ready on /ws/pnl');
  return wss;
}
