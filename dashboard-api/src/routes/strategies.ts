import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();

const PERIOD_FILTER: Record<string, string> = {
  today: "INTERVAL '1 day'",
  '7d':  "INTERVAL '7 days'",
  '30d': "INTERVAL '30 days'",
  all:   "INTERVAL '100 years'",
};

router.get('/', async (_req: Request, res: Response) => {
  try {
    const query = `
      SELECT s.*, 
             (SELECT COUNT(*)::int FROM orders o WHERE o.strategy_id = s.id) as total_signals,
             (SELECT COUNT(*)::int FROM orders o WHERE o.strategy_id = s.id AND o.status = 'filled') as filled_orders,
             (SELECT AVG(total_pnl)::float FROM strategy_performance sp WHERE sp.strategy_id = s.id AND period_type = 'all_time') as avg_pnl,
             (SELECT win_rate::float FROM strategy_performance sp WHERE sp.strategy_id = s.id AND period_type = 'all_time') as win_rate
      FROM strategies s
    `;
    const { rows } = await getPool().query(query);
    res.json(rows);
  } catch (err) {
    console.error('Error fetching strategies:', err);
    res.status(500).json({ error: 'Database error fetching strategies' });
  }
});

router.get('/:id/performance', async (req: Request, res: Response) => {
  const { period = 'all_time', date } = req.query;
  const { id } = req.params;
  try {
    const query = `
      SELECT * FROM strategy_performance 
      WHERE strategy_id = $1 AND period_type = $2 
      ${date ? 'AND period_date = $3' : ''}
    `;
    const params = date ? [id, period, date] : [id, period];
    const { rows } = await getPool().query(query, params);
    if (rows.length === 0) return res.status(404).json({ message: "No performance data yet" });
    res.json(rows[0]);
  } catch (err) {
    console.error(`Error fetching performance for strategy ${id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/webhook-secret/rotate', async (req: Request, res: Response) => {
  try {
    const newSecret = require('crypto').randomBytes(32).toString('hex');
    await getPool().query('UPDATE strategies SET webhook_secret = $1 WHERE id = $2', [newSecret, req.params.id]);
    res.json({ message: 'Webhook secret rotated', strategy_id: req.params.id, new_secret_preview: newSecret.substring(0, 8) + '...' });
  } catch (err) {
    console.error('Error rotating webhook secret:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.get('/:id/webhook-calls', async (req: Request, res: Response) => {
  const { limit = 50, status_filter } = req.query;
  try {
    let query = 'SELECT * FROM strategy_webhook_calls WHERE strategy_id = $1';
    const params: any[] = [req.params.id];
    if (status_filter) {
      query += ' AND http_status = $2';
      params.push(status_filter);
    }
    query += ' ORDER BY received_at DESC LIMIT $' + (params.length + 1);
    params.push(limit);
    const { rows } = await getPool().query(query, params);
    res.json(rows);
  } catch (err) {
    console.error(`Error fetching webhook calls for ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/webhook-enabled', async (req: Request, res: Response) => {
  try {
    await getPool().query('UPDATE strategies SET webhook_enabled = $1 WHERE id = $2', [req.body.enabled, req.params.id]);
    res.json({ message: 'Status updated' });
  } catch (err) {
    console.error('Error updating webhook status:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/max-daily-signals', async (req: Request, res: Response) => {
  try {
    await getPool().query('UPDATE strategies SET max_daily_signals = $1 WHERE id = $2', [req.body.max_daily_signals, req.params.id]);
    res.json({ message: 'Limit updated' });
  } catch (err) {
    console.error('Error updating max daily signals:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/enable', async (req: Request, res: Response) => {
  try {
    await getPool().query('UPDATE strategies SET enabled = true WHERE id = $1', [req.params.id]);
    res.json({ message: 'Strategy enabled' });
  } catch (err) {
    console.error('Error enabling strategy:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/disable', async (req: Request, res: Response) => {
  try {
    await getPool().query('UPDATE strategies SET enabled = false WHERE id = $1', [req.params.id]);
    res.json({ message: 'Strategy disabled' });
  } catch (err) {
    console.error('Error disabling strategy:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.get('/comparison', async (req: Request, res: Response) => {
  const period = (req.query.period as string) || '7d';
  const interval = PERIOD_FILTER[period] || PERIOD_FILTER['7d'];
  try {
    const query = `
      SELECT 
        s.id as strategy_id, 
        s.name,
        COALESCE(SUM(st.trades_count), 0)::int as trades_count,
        COALESCE(AVG(st.win_rate), 0)::float as win_rate,
        COALESCE(SUM(st.pnl_total), 0)::float as pnl_total,
        COALESCE(AVG(st.max_drawdown), 0)::float as max_drawdown,
        (SELECT COUNT(*)::int FROM strategy_positions sp WHERE sp.strategy_id = s.id AND sp.status = 'open') as open_positions
      FROM strategies s
      LEFT JOIN strategy_stats st ON s.id = st.strategy_id AND st.period_date >= CURRENT_DATE - ${interval}
      GROUP BY s.id, s.name
    `;
    const { rows } = await getPool().query(query);
    res.json(rows);
  } catch (err) {
    console.error('Error fetching strategy comparison:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.get('/:id/stats', async (req: Request, res: Response) => {
  const period = (req.query.period as string) || '7d';
  const interval = PERIOD_FILTER[period] || PERIOD_FILTER['7d'];
  try {
    const query = `
      SELECT 
        strategy_id,
        SUM(trades_count)::int as trades_count,
        SUM(trades_won)::int as trades_won,
        AVG(win_rate)::float as win_rate,
        SUM(pnl_total)::float as pnl_total,
        AVG(pnl_total / NULLIF(trades_count, 0))::float as pnl_avg,
        MAX(max_drawdown)::float as max_drawdown
      FROM strategy_stats 
      WHERE strategy_id = $1 AND period_date >= CURRENT_DATE - ${interval}
      GROUP BY strategy_id
    `;
    const { rows } = await getPool().query(query, [req.params.id]);
    if (rows.length === 0) {
      return res.json({
        strategy_id: req.params.id,
        trades_count: 0,
        trades_won: 0,
        win_rate: 0,
        pnl_total: 0,
        pnl_avg: 0,
        max_drawdown: 0
      });
    }
    res.json(rows[0]);
  } catch (err) {
    console.error(`Error fetching stats for strategy ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.get('/:id/positions', async (req: Request, res: Response) => {
  try {
    const query = `
      SELECT 
        symbol,
        side,
        size::text as size,
        entry_price::text as "entryPx",
        current_price::text as "markPx",
        pnl_unrealized::text as "unrealizedPnl",
        exchange as platform
      FROM strategy_positions 
      WHERE strategy_id = $1 AND status = $2
    `;
    const { rows } = await getPool().query(query, [req.params.id, 'open']);
    res.json(rows);
  } catch (err) {
    console.error(`Error fetching positions for strategy ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});

export default router;
