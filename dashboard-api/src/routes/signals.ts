import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();

// GET /signals — paginated signal_log with joined execution log row
router.get('/', async (req: Request, res: Response) => {
  const {
    page = '1', limit = '50',
    strategy_id, outcome, from, to,
  } = req.query as Record<string, string>;

  const pageNum  = Math.max(parseInt(page), 1);
  const limitNum = Math.min(Math.max(parseInt(limit), 1), 200);
  const offset   = (pageNum - 1) * limitNum;

  const filters: string[] = [];
  const params: unknown[] = [];
  let idx = 1;

  if (strategy_id) { filters.push(`sl.strategy_id = $${idx++}`); params.push(strategy_id); }
  if (outcome)     { filters.push(`sl.outcome = $${idx++}`);      params.push(outcome); }
  if (from)        { filters.push(`sl.received_at >= $${idx++}`); params.push(new Date(from)); }
  if (to)          { filters.push(`sl.received_at <= $${idx++}`); params.push(new Date(to)); }

  const where = filters.length ? 'WHERE ' + filters.join(' AND ') : '';
  const pool  = getPool();

  const [countRes, rowsRes] = await Promise.all([
    pool.query(`SELECT COUNT(*) FROM signal_log sl ${where}`, params),
    pool.query(
      `SELECT
         sl.id, sl.received_at, sl.source_ip, sl.strategy_id,
         sl.http_status, sl.outcome, sl.error_detail, sl.raw_body, sl.duration_ms,
         oel.id           AS oel_id,
         oel.exchange,
         oel.exchange_order_id,
         oel.client_order_id,
         oel.symbol       AS oel_symbol,
         oel.side         AS oel_side,
         oel.order_type   AS oel_order_type,
         oel.requested_size,
         oel.status       AS oel_status,
         oel.error_message AS oel_error_message
       FROM signal_log sl
       LEFT JOIN order_execution_log oel ON oel.signal_log_id = sl.id
       ${where}
       ORDER BY sl.received_at DESC
       LIMIT $${idx} OFFSET $${idx + 1}`,
      [...params, limitNum, offset],
    ),
  ]);

  res.json({
    total: parseInt(countRes.rows[0].count),
    page:  pageNum,
    limit: limitNum,
    items: rowsRes.rows,
  });
});

// GET /signals/strategies — distinct strategy IDs that have signal_log entries
router.get('/strategies', async (_req: Request, res: Response) => {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT DISTINCT strategy_id FROM signal_log WHERE strategy_id IS NOT NULL ORDER BY strategy_id`
  );
  res.json(rows.map(r => r.strategy_id));
});

export default router;
