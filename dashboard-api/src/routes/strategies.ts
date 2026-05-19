import { Router, Request, Response } from 'express';
import { Pool } from 'pg'; // Assuming pool is available, let's inject it via req or use a global if established.
// Since I don't see a shared DB connection here, I will use a placeholder or assume access to a pool.
// Actually, I see dashboard-api relies on proxyToGenerator in current strategies.ts.
// I need to update the generator to expose these, or implement them here if the API has direct DB access.
// Based on the architecture: "dashboard-api" is an Express app, it likely has DB access.

const router = Router();
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

router.get('/', async (_req: Request, res: Response) => {
  try {
    const query = `
      SELECT s.*, 
             (SELECT COUNT(*) FROM orders o WHERE o.strategy_id = s.id) as total_signals,
             (SELECT COUNT(*) FROM orders o WHERE o.strategy_id = s.id AND o.status = 'filled') as filled_orders,
             (SELECT AVG(total_pnl) FROM strategy_performance sp WHERE sp.strategy_id = s.id AND period_type = 'all_time') as avg_pnl,
             (SELECT win_rate FROM strategy_performance sp WHERE sp.strategy_id = s.id AND period_type = 'all_time') as win_rate
      FROM strategies s
    `;
    const { rows } = await pool.query(query);
    res.json(rows);
  } catch (err) {
    res.status(500).json({ error: 'Database error' });
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
    const { rows } = await pool.query(query, params);
    if (rows.length === 0) return res.status(404).json({ message: "No performance data yet" });
    res.json(rows[0]);
  } catch (err) {
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/webhook-secret/rotate', async (req: Request, res: Response) => {
  try {
    const newSecret = require('crypto').randomBytes(32).toString('hex');
    await pool.query('UPDATE strategies SET webhook_secret = $1 WHERE id = $2', [newSecret, req.params.id]);
    res.json({ message: 'Webhook secret rotated', strategy_id: req.params.id, new_secret_preview: newSecret.substring(0, 8) + '...' });
  } catch (err) {
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
    const { rows } = await pool.query(query, params);
    res.json(rows);
  } catch (err) {
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/webhook-enabled', async (req: Request, res: Response) => {
  try {
    await pool.query('UPDATE strategies SET webhook_enabled = $1 WHERE id = $2', [req.body.enabled, req.params.id]);
    res.json({ message: 'Status updated' });
  } catch (err) {
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/max-daily-signals', async (req: Request, res: Response) => {
  try {
    await pool.query('UPDATE strategies SET max_daily_signals = $1 WHERE id = $2', [req.body.max_daily_signals, req.params.id]);
    res.json({ message: 'Limit updated' });
  } catch (err) {
    res.status(500).json({ error: 'Database error' });
  }
});

export default router;
