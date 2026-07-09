import express from 'express';
import cors from 'cors';
import http from 'http';
import dotenv from 'dotenv';

import { initDb } from './db';
import { initRedis } from './redis';
import { createOrderWebSocket } from './ws/orderFeed';
import { createPnlWebSocket } from './ws/pnlFeed';
import { startLivePnlTicker } from './livePnl';
import ordersRouter from './routes/orders';
import statsRouter from './routes/stats';
import configRouter from './routes/config';
import strategiesRouter from './routes/strategies';
import positionsRouter from './routes/positions';
import accountsRouter from './routes/accounts';
import signalsRouter from './routes/signals';
import aiRouter from './routes/ai';
import systemRouter from './routes/system';

dotenv.config();

const app = express();
app.use(cors());
app.use(express.json());

// Routes
app.use('/orders', ordersRouter);
app.use('/stats', statsRouter);
app.use('/config', configRouter);
app.use('/strategies', strategiesRouter);
app.use('/positions', positionsRouter);
app.use('/accounts', accountsRouter);
app.use('/signals',  signalsRouter);
app.use('/ai',       aiRouter);
app.use('/system',   systemRouter);

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'dashboard-api' });
});

const server = http.createServer(app);

const PORT = parseInt(process.env.PORT || '8003', 10);

async function main() {
  await initDb();
  await initRedis();
  const wssPnl = createPnlWebSocket();
  const wssOrders = createOrderWebSocket();

  // Single upgrade router — prevents double-handling which corrupts already-upgraded sockets
  server.on('upgrade', (req, socket, head) => {
    if (req.url === '/ws/pnl') {
      wssPnl.handleUpgrade(req, socket, head, (ws) => wssPnl.emit('connection', ws, req));
    } else if (req.url === '/ws/orders') {
      wssOrders.handleUpgrade(req, socket, head, (ws) => wssOrders.emit('connection', ws, req));
    } else {
      socket.destroy();
    }
  });

  startLivePnlTicker().catch(e => console.error('[livePnl] startup failed:', e));
  server.listen(PORT, '0.0.0.0', () => {
    console.log(`Dashboard API listening on :${PORT}`);
  });
}

main().catch((err) => {
  console.error('Fatal startup error:', err);
  process.exit(1);
});
