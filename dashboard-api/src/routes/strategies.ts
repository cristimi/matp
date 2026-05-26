import { Router, Request, Response } from 'express';
import crypto from 'crypto';
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
             b.symbol as base_asset, 
             q.symbol as quote_asset,
             (SELECT COUNT(*)::int FROM orders o WHERE o.pair_id = s.pair_id) as total_signals,
             (SELECT COUNT(*)::int FROM orders o WHERE o.pair_id = s.pair_id AND o.status = 'filled') as filled_orders
      FROM strategies s
      LEFT JOIN trading_pairs tp ON s.pair_id = tp.id
      LEFT JOIN assets b ON tp.base_asset_id = b.id
      LEFT JOIN assets q ON tp.quote_asset_id = q.id
    `;
    const { rows } = await getPool().query(query);
    const strategies = rows.map(r => ({
      ...r,
      pair: { base: r.base_asset || '', quote: r.quote_asset || '', label: r.base_asset ? `${r.base_asset}/${r.quote_asset}` : r.symbol }
    }));
    res.json(strategies);
  } catch (err) {
    console.error('Error fetching strategies:', err);
    res.status(500).json({ error: 'Database error fetching strategies' });
  }
});

router.post('/', async (req: Request, res: Response) => {
  const { id, name, type, class: className, symbol, interval, platform = 'auto', config_yaml = '' } = req.body;
  if (!id || !name || !type || !className || !symbol || !interval) {
    return res.status(400).json({ error: 'Missing required fields (id, name, type, class, symbol, interval)' });
  }

  try {
    const webhookSecret = crypto.randomBytes(32).toString('hex');
    
    // Try to find pair_id
    const [base, quote] = symbol.includes('/') ? symbol.split('/') : symbol.includes('-') ? symbol.split('-') : [symbol, 'USDT'];
    const pairQuery = `
      SELECT tp.id FROM trading_pairs tp
      JOIN assets b ON tp.base_asset_id = b.id
      JOIN assets q ON tp.quote_asset_id = q.id
      WHERE b.symbol = $1 AND q.symbol = $2
    `;
    const pairRes = await getPool().query(pairQuery, [base, quote]);
    const pairId = pairRes.rows.length > 0 ? pairRes.rows[0].id : null;

    const query = `
      INSERT INTO strategies (
        id, name, type, "class", symbol, interval, platform, config_yaml, webhook_secret, pair_id
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
      RETURNING *
    `;
    const { rows } = await getPool().query(query, [
      id, name, type, className, symbol, interval, platform, config_yaml, webhookSecret, pairId
    ]);
    res.status(201).json(rows[0]);
  } catch (err: any) {
    console.error('Error creating strategy:', err);
    res.status(500).json({ error: 'Database error', detail: err.message });
  }
});

router.get('/:id', async (req: Request, res: Response) => {
  try {
    const query = `
      SELECT s.*, 
             b.symbol as base_asset, 
             q.symbol as quote_asset
      FROM strategies s
      LEFT JOIN trading_pairs tp ON s.pair_id = tp.id
      LEFT JOIN assets b ON tp.base_asset_id = b.id
      LEFT JOIN assets q ON tp.quote_asset_id = q.id
      WHERE s.id = $1
    `;
    const { rows } = await getPool().query(query, [req.params.id]);
    if (rows.length === 0) return res.status(404).json({ error: 'Strategy not found' });
    
    const strategy = {
      ...rows[0],
      pair: { base: rows[0].base_asset || '', quote: rows[0].quote_asset || '', label: rows[0].base_asset ? `${rows[0].base_asset}/${rows[0].quote_asset}` : rows[0].symbol }
    };
    res.json(strategy);
  } catch (err: any) {
    console.error('Error fetching strategy:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.put('/:id', async (req: Request, res: Response) => {
  const { id } = req.params;
  const updatableFields = [
    'name', 'type', 'class', 'symbol', 'interval', 'platform', 
    'config_yaml', 'enabled', 'webhook_enabled', 'max_position_size', 
    'max_leverage', 'max_daily_drawdown_percent', 'description'
  ];
  
  const updates: string[] = [];
  const params: any[] = [id];
  let idx = 2;

  for (const field of updatableFields) {
    if (req.body[field] !== undefined) {
      let dbField = field;
      if (field === 'class') dbField = '"class"';
      
      updates.push(`${dbField} = $${idx++}`);
      params.push(req.body[field]);
    }
  }

  if (updates.length === 0) {
    return res.status(400).json({ error: 'No fields to update' });
  }

  try {
    const query = `UPDATE strategies SET ${updates.join(', ')}, updated_at = NOW() WHERE id = $1 RETURNING *`;
    const { rows } = await getPool().query(query, params);
    if (rows.length === 0) return res.status(404).json({ error: 'Strategy not found' });
    res.json(rows[0]);
  } catch (err: any) {
    console.error('Error updating strategy:', err);
    res.status(500).json({ error: 'Database error', detail: err.message });
  }
});

router.delete('/:id', async (req: Request, res: Response) => {
  try {
    const { rows } = await getPool().query('DELETE FROM strategies WHERE id = $1 RETURNING *', [req.params.id]);
    if (rows.length === 0) return res.status(404).json({ error: 'Strategy not found' });
    res.json({ message: 'Strategy deleted', strategy: rows[0] });
  } catch (err: any) {
    console.error('Error deleting strategy:', err);
    res.status(500).json({ error: 'Database error', detail: err.message });
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
        sp.*,
        b.symbol as base_asset, 
        q.symbol as quote_asset
      FROM strategy_positions sp
      JOIN trading_pairs tp ON sp.pair_id = tp.id
      JOIN assets b ON tp.base_asset_id = b.id
      JOIN assets q ON tp.quote_asset_id = q.id
      WHERE sp.strategy_id = $1 AND sp.status = 'open'
    `;
    const { rows } = await getPool().query(query, [req.params.id]);
    const positions = rows.map(r => ({
      ...r,
      pair: { base: r.base_asset, quote: r.quote_asset, label: `${r.base_asset}/${r.quote_asset}` },
      entryPx: r.entry_price,
      markPx: r.current_price,
      unrealizedPnl: r.pnl_unrealized
    }));
    res.json(positions);
  } catch (err) {
    console.error(`Error fetching positions for strategy ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});

export default router;

