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
              "o.signal_source, ea.label AS account_label, ea.exchange AS account_exchange " +
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

// GET /orders/:id/detail — L4 full order detail (lazy)
// OEL and signal_log joins are best-effort: null when exchange_order_id is absent
// (unfilled/failed orders) or when the exec-log row has no signal_log_id.
router.get('/:id/detail', async (req: Request, res: Response) => {
  try {
    const pool = getPool();
    const [orderRes, eventsRes] = await Promise.all([
      pool.query(`
        SELECT
          o.signal_source,
          o.raw_webhook,
          o.signal_metadata,
          o.indicator_price,
          oel.requested_price,
          oel.exchange_fee,
          oel.exchange_order_id AS oel_exchange_order_id,
          oel.placed_at,
          oel.filled_at,
          o.actual_fill_price,
          sl.ai_reasoning,
          sl.ai_confidence
        FROM orders o
        LEFT JOIN order_execution_log oel
          ON oel.exchange_order_id = o.exchange_order_id
          AND o.exchange_order_id IS NOT NULL
        LEFT JOIN signal_log sl
          ON sl.id = oel.signal_log_id
          AND oel.signal_log_id IS NOT NULL
        WHERE o.id = $1
      `, [req.params.id]),
      pool.query(`
        SELECT event_time, from_status, to_status, message
        FROM order_events
        WHERE order_id = $1
        ORDER BY event_time ASC
      `, [req.params.id]),
    ]);

    if (orderRes.rowCount === 0) {
      return res.status(404).json({ error: `Order not found: ${req.params.id}` });
    }

    const o = orderRes.rows[0];
    res.json({
      origin: {
        signal_source: o.signal_source,
        raw_webhook:   o.raw_webhook,
      },
      justification: {
        signal_metadata: o.signal_metadata,
        indicator_price: o.indicator_price != null ? Number(o.indicator_price) : null,
        ai_reasoning:    o.ai_reasoning    ?? null,
        ai_confidence:   o.ai_confidence   != null ? Number(o.ai_confidence)   : null,
      },
      execution: {
        requested_price:   o.requested_price        != null ? Number(o.requested_price)   : null,
        exchange_fee:      o.exchange_fee            != null ? Number(o.exchange_fee)      : null,
        exchange_order_id: o.oel_exchange_order_id  ?? null,
        placed_at:         o.placed_at              ?? null,
        filled_at:         o.filled_at              ?? null,
        actual_fill_price: o.actual_fill_price       != null ? Number(o.actual_fill_price) : null,
        events: eventsRes.rows.map(e => ({
          event_time:  e.event_time,
          from_status: e.from_status,
          to_status:   e.to_status,
          message:     e.message,
        })),
      },
    });
  } catch (err) {
    console.error(`Error fetching order detail ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error fetching order detail' });
  }
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
