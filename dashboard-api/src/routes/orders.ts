import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();

router.get('/', async (req: Request, res: Response) => {
  const {
    page = '1', limit = '50', symbol, platform, status,
    strategy_id, account_id, from, to,
  } = req.query as Record<string, string>;

  const pageNum = Math.max(parseInt(page), 1);
  const limitNum = Math.min(Math.max(parseInt(limit), 1), 200);
  const offset = (pageNum - 1) * limitNum;

  const filters: string[] = [];
  const params: unknown[] = [];
  let idx = 1;

  if (symbol) { filters.push("o.symbol = $" + idx++); params.push(symbol); }
  if (platform) { filters.push("o.platform = $" + idx++); params.push(platform); }
  if (status) { filters.push("o.status = $" + idx++); params.push(status); }
  else { filters.push("o.status != 'deleted'"); }
  if (strategy_id) { filters.push("o.strategy_id = $" + idx++); params.push(strategy_id); }
  if (account_id) { filters.push("o.account_id = $" + idx++); params.push(account_id); }
  if (from) { filters.push("o.received_at >= $" + idx++); params.push(new Date(from)); }
  if (to) { filters.push("o.received_at <= $" + idx++); params.push(new Date(to)); }

  const where = filters.length ? "WHERE " + filters.join(' AND ') : '';
  const pool = getPool();

  const [countRes, rowsRes] = await Promise.all([
    pool.query("SELECT COUNT(*) FROM orders o " + where, params),
    pool.query(
      "SELECT o.id, o.received_at, o.symbol, o.side, o.signal, o.size, o.platform, " +
              "o.status, o.strategy_id, o.account_id, o.exchange_order_id, o.pnl, o.error_msg, o.indicator_price, o.actual_fill_price, " +
              "ea.label AS account_label " +
       "FROM orders o " +
       "LEFT JOIN exchange_accounts ea ON ea.id = o.account_id " +
       where +
       " ORDER BY o.received_at DESC " +
       "LIMIT $" + idx + " OFFSET $" + (idx + 1),
      [...params, limitNum, offset],
    ),
  ]);

  res.json({
    total: parseInt(countRes.rows[0].count),
    page: pageNum,
    limit: limitNum,
    items: rowsRes.rows.map(row => ({
      ...row,
      pair: { label: row.symbol }
    })),
  });
});

router.get('/:id', async (req: Request, res: Response) => {
  const pool = getPool();
  const { rows } = await pool.query('SELECT * FROM orders WHERE id = $1', [req.params.id]);
  if (!rows.length) return res.status(404).json({ error: 'Order not found' });
  const row = rows[0];
  res.json({ ...row, pair: { label: row.symbol } });
});

router.post('/:id/retry', async (req: Request, res: Response) => {
  const listenerUrl = process.env.LISTENER_URL || 'http://order-listener:8001';
  const resp = await fetch(listenerUrl + '/orders/' + req.params.id + '/retry', { method: 'POST' });
  const data = await resp.json();
  res.status(resp.status).json(data);
});

// DELETE /orders/:id — soft delete (mark as deleted / remove log)
router.delete('/:id', async (req: Request, res: Response) => {
  try {
    const pool = getPool();
    await pool.query(
      `UPDATE orders SET status = 'deleted', updated_at = NOW() WHERE id = $1`,
      [req.params.id]
    );
    res.json({ deleted: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /orders/:id/cancel — cancel a pending order
router.post('/:id/cancel', async (req: Request, res: Response) => {
  try {
    const pool = getPool();
    await pool.query(
      `UPDATE orders SET status = 'cancelled', updated_at = NOW() WHERE id = $1`,
      [req.params.id]
    );
    res.json({ cancelled: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
