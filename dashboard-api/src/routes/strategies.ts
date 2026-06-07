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
      SELECT
        s.*,
        ea.exchange        AS account_exchange,
        ea.mode            AS account_mode,
        ea.label           AS account_label,
        COALESCE((
          SELECT COUNT(*)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'open'
        ), 0)::int         AS open_positions_count,
        COALESCE((
          SELECT COUNT(*)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'closed'
        ), 0)::int         AS closed_positions_count,
        COALESCE((
          SELECT SUM(sp.pnl_realized)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'closed'
        ), 0)::numeric     AS realized_pnl
      FROM strategies s
      LEFT JOIN exchange_accounts ea ON ea.id = s.account_id
      WHERE COALESCE(s.is_deleted, false) = false
      ORDER BY s.created_at DESC
    `;
    const { rows } = await getPool().query(query);
    res.json(rows);
  } catch (err) {
    console.error('Error fetching strategies:', err);
    res.status(500).json({ error: 'Database error fetching strategies' });
  }
});

// POST /strategies — create a new strategy
router.post('/', async (req: Request, res: Response) => {
  const {
    name,
    symbol,
    account_id,
    interval         = '1h',
    description      = '',
    default_leverage           = 1,
    margin_mode                = 'isolated',
    max_position_size          = 1.0,
    max_leverage               = 10,
    max_daily_signals          = 500,
    max_daily_drawdown_percent = 20,
    capital_allocation_percent = 100,
    allow_quote_variants       = false,
    allow_cross_charting       = false,
  } = req.body;

  if (!name || !symbol || !account_id) {
    return res.status(400).json({
      error: 'Missing required fields',
      required: ['name', 'symbol', 'account_id'],
    });
  }

  // Validate account exists and is active
  try {
    const acct = await getPool().query(
      `SELECT id FROM exchange_accounts WHERE id = $1 AND is_active = true`,
      [account_id]
    );
    if (acct.rowCount === 0) {
      return res.status(400).json({
        error: `Account not found or inactive: ${account_id}`,
      });
    }
  } catch (e: any) {
    return res.status(500).json({ error: e.message });
  }

  // Generate ID from name
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 30);
  const suffix       = crypto.randomBytes(2).toString('hex');
  const id           = `${slug}-${suffix}`;
  const webhookSecret = crypto.randomBytes(16).toString('hex');

  // Normalise symbol: accept "BTC/USDT" or "BTCUSDT", store as "BTC-USDT"
  const normalisedSymbol = symbol
    .toUpperCase()
    .replace('/', '-');

  try {
    await getPool().query(
      `INSERT INTO strategies (
        id, name, symbol, account_id, interval, description,
        class, config_yaml,
        webhook_secret, webhook_enabled,
        default_leverage, margin_mode,
        max_position_size, max_leverage, max_daily_signals,
        max_daily_drawdown_percent, capital_allocation_percent,
        allow_quote_variants, allow_cross_charting,
        enabled
      ) VALUES (
        $1, $2, $3, $4, $5, $6,
        'webhook', '',
        $7, true,
        $8, $9, $10, $11, $12,
        $13, $14,
        $15, $16,
        true
      )`,
      [
        id, name, normalisedSymbol, account_id, interval, description,
        webhookSecret,
        default_leverage, margin_mode,
        max_position_size, max_leverage, max_daily_signals,
        max_daily_drawdown_percent, capital_allocation_percent,
        allow_quote_variants, allow_cross_charting,
      ]
    );

    // Return the created strategy with the webhook secret
    // (only returned here — not in GET endpoints)
    res.status(201).json({
      id,
      name,
      symbol:         normalisedSymbol,
      account_id,
      interval,
      enabled:        true,
      webhook_secret: webhookSecret,  // shown once on creation
      allow_quote_variants,
      allow_cross_charting,
      message: 'Strategy created. Save the webhook_secret — it will not be shown again.',
    });
  } catch (e: any) {
    if (e.code === '23505') {
      return res.status(409).json({ error: `Strategy ID conflict: ${id}` });
    }
    res.status(500).json({ error: e.message });
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

// GET /strategies/:id/webhook-info — returns webhook URL and secret
// Used by the edit page to display the TradingView configuration
router.get('/:id/webhook-info', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `SELECT id, name, symbol, webhook_secret, webhook_enabled
       FROM strategies WHERE id = $1`,
      [req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    const s = result.rows[0];

    // Determine host from request or env
    const host = process.env.PUBLIC_HOST
      || req.get('x-forwarded-host')
      || req.get('host')
      || 'localhost';
    const protocol = req.get('x-forwarded-proto') || 'http';
    const webhookUrl = `${protocol}://${host}/api/listener/webhook/${s.id}`;

    res.json({
      strategy_id:     s.id,
      strategy_name:   s.name,
      symbol:          s.symbol,
      webhook_url:     webhookUrl,
      webhook_secret:  s.webhook_secret,
      webhook_enabled: s.webhook_enabled,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /strategies/:id — single strategy with full config
router.get('/:id', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `SELECT
         s.*,
         ea.exchange  AS account_exchange,
         ea.mode      AS account_mode,
         ea.label     AS account_label,
         COALESCE((
           SELECT COUNT(*)
           FROM strategy_positions sp
           WHERE sp.strategy_id = s.id
           AND sp.status = 'open'
         ), 0)::int   AS open_positions_count
       FROM strategies s
       LEFT JOIN exchange_accounts ea ON ea.id = s.account_id
       WHERE s.id = $1`,
      [req.params.id]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    res.json(result.rows[0]);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// PUT /strategies/:id — update strategy fields including coupling flags
router.put('/:id', async (req: Request, res: Response) => {
  const {
    name,
    symbol,
    interval,
    enabled,
    webhook_enabled,
    default_leverage,
    margin_mode,
    allow_quote_variants,
    allow_cross_charting,
    max_position_size,
    max_leverage,
    max_daily_signals,
    max_daily_drawdown_percent,
  } = req.body;

  // Normalise symbol if provided
  const normalisedSymbol = symbol
    ? symbol.toUpperCase().replace('/', '-')
    : null;

  try {
    const result = await getPool().query(
      `UPDATE strategies SET
         name                       = COALESCE($1, name),
         symbol                     = COALESCE($2, symbol),
         interval                   = COALESCE($3, interval),
         enabled                    = COALESCE($4, enabled),
         webhook_enabled            = COALESCE($5, webhook_enabled),
         default_leverage           = COALESCE($6, default_leverage),
         margin_mode                = COALESCE($7, margin_mode),
         allow_quote_variants       = COALESCE($8, allow_quote_variants),
         allow_cross_charting       = COALESCE($9, allow_cross_charting),
         max_position_size          = COALESCE($10, max_position_size),
         max_leverage               = COALESCE($11, max_leverage),
         max_daily_signals          = COALESCE($12, max_daily_signals),
         max_daily_drawdown_percent = COALESCE($13, max_daily_drawdown_percent),
         updated_at                 = NOW()
       WHERE id = $14
       RETURNING id, name, symbol, interval, enabled, webhook_enabled,
                 default_leverage, margin_mode,
                 allow_quote_variants, allow_cross_charting, account_id`,
      [
        name ?? null,
        normalisedSymbol,
        interval ?? null,
        enabled ?? null,
        webhook_enabled ?? null,
        default_leverage ?? null,
        margin_mode ?? null,
        allow_quote_variants ?? null,
        allow_cross_charting ?? null,
        max_position_size ?? null,
        max_leverage ?? null,
        max_daily_signals ?? null,
        max_daily_drawdown_percent ?? null,
        req.params.id,
      ]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    res.json(result.rows[0]);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /strategies/:id/stop
// Disables the strategy. If it has open positions, caller must handle
// closing them first (checked by the UI before calling this).
router.post('/:id/stop', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `UPDATE strategies
       SET enabled = false, updated_at = NOW()
       WHERE id = $1
       RETURNING id, enabled`,
      [req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    res.json({ stopped: req.params.id, enabled: false });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /strategies/:id/start — re-enable a stopped strategy
router.post('/:id/start', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `UPDATE strategies
       SET enabled = true, updated_at = NOW()
       WHERE id = $1
       RETURNING id, enabled`,
      [req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    res.json({ started: req.params.id, enabled: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// DELETE /strategies/:id — soft delete (inactive + no open positions only)
router.delete('/:id', async (req: Request, res: Response) => {
  try {
    // Check strategy state
    const check = await getPool().query(
      `SELECT s.enabled,
              COALESCE((
                SELECT COUNT(*) FROM strategy_positions sp
                WHERE sp.strategy_id = s.id AND sp.status = 'open'
              ), 0)::int AS open_positions_count
       FROM strategies s WHERE s.id = $1`,
      [req.params.id]
    );
    if (check.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    const { enabled, open_positions_count } = check.rows[0];
    if (enabled) {
      return res.status(409).json({
        error: 'Cannot delete an active strategy. Stop it first.',
      });
    }
    if (open_positions_count > 0) {
      return res.status(409).json({
        error: `Cannot delete: strategy has ${open_positions_count} open position(s).`,
      });
    }
    await getPool().query(
      `UPDATE strategies
       SET is_deleted = true, updated_at = NOW()
       WHERE id = $1`,
      [req.params.id]
    );
    res.json({ deleted: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
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

// POST /strategies/:id/reset-daily — reset daily counters
router.post('/:id/reset-daily', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `UPDATE strategies
       SET signals_today = 0,
           pnl_today     = 0,
           enabled       = true,
           updated_at    = NOW()
       WHERE id = $1
       RETURNING id, signals_today, pnl_today, enabled`,
      [req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    res.json({
      reset:         req.params.id,
      signals_today: result.rows[0].signals_today,
      pnl_today:     result.rows[0].pnl_today,
      enabled:       result.rows[0].enabled,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
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
