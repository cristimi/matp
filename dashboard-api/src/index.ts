import express from 'express';
import cors from 'cors';
import http from 'http';
import dotenv from 'dotenv';

import { initDb } from './db';
import { initRedis } from './redis';
import { initWebSocket } from './ws/orderFeed';
import ordersRouter from './routes/orders';
import statsRouter from './routes/stats';
import configRouter from './routes/config';
import strategiesRouter from './routes/strategies';
import positionsRouter from './routes/positions';
import accountsRouter from './routes/accounts';
import signalsRouter from './routes/signals';

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

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'dashboard-api' });
});

const server = http.createServer(app);

const PORT = parseInt(process.env.PORT || '8003', 10);

async function main() {
  await initDb();
  await initRedis();
  initWebSocket(server);
  server.listen(PORT, '0.0.0.0', () => {
    console.log(`Dashboard API listening on :${PORT}`);
  });
}

main().catch((err) => {
  console.error('Fatal startup error:', err);
  process.exit(1);
});
