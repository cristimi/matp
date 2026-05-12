import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();

router.get('/', async (req: Request, res: Response) => {
  const {
    page = '1', limit = '50', symbol, platform, status,
    strategy_id, from, to,
  } = req.query as Record<string, string>;

  const pageNum = Math.max(parseInt(page), 1);
  const limitNum = Math.min(Math.max(parseInt(limit), 1), 200);
  const offset = (pageNum - 1) * limitNum;

  const filters: string[] = [];
  const params: unknown[] = [];
  let idx = 1;

  if (symbol) { filters.push(`symbol = $${idx++}`); params.push(symbol); }
  if (platform) { filters.push(`platform = $${idx++}`); params.push(platform); }
  if (status) { filters.push(`status = $${idx++}`); params.push(status); }
  if (strategy_id) { filters.push(`strategy_id = $${idx++}`); params.push(strategy_id); }
  if (from) { filters.push(`received_at >= $${idx++}`); params.push(new Date(from)); }
  if (to) { filters.push(`received_at <= $${idx++}`); params.push(new Date(to)); }

  const where = filters.length ? `WHERE ${filters.join(' AND ')}` : '';
  const pool = getPool();

  const [countRes, rowsRes] = await Promise.all([
    pool.query(`SELECT COUNT(*) FROM orders ${where}`, params),
    pool.query(
      `SELECT id, received_at, symbol, side, signal, size, platform,
              status, strategy_id, exchange_order_id, pnl, error_msg
       FROM orders ${where}
       ORDER BY received_at DESC
       LIMIT $${idx} OFFSET $${idx + 1}`,
      [...params, limitNum, offset],
    ),
  ]);

  res.json({
    total: parseInt(countRes.rows[0].count),
    page: pageNum,
    limit: limitNum,
    items: rowsRes.rows,
  });
});

router.get('/:id', async (req: Request, res: Response) => {
  const pool = getPool();
  const { rows } = await pool.query('SELECT * FROM orders WHERE id = $1', [req.params.id]);
  if (!rows.length) return res.status(404).json({ error: 'Order not found' });
  res.json(rows[0]);
});

router.post('/:id/retry', async (req: Request, res: Response) => {
  const listenerUrl = process.env.LISTENER_URL || 'http://order-listener:8001';
  const resp = await fetch(`${listenerUrl}/orders/${req.params.id}/retry`, { method: 'POST' });
  const data = await resp.json();
  res.status(resp.status).json(data);
});

export default router;
