import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();
const EXECUTOR_URL = process.env.EXECUTOR_URL || 'http://order-executor:8004';
const LISTENER_URL = process.env.LISTENER_URL || 'http://order-listener:8001';

// GET /positions — aggregate strategy positions enriched with real-time executor data
router.get('/', async (_req: Request, res: Response) => {
  try {
    const pool = getPool();
    
    // 1. Fetch all strategy positions (open, stale, and last 20 closed)
    const posResult = await pool.query(
      `SELECT sp.*, s.name as strategy_name, s.type as strategy_type, s.strategy_source, s.account_id, ea.exchange as account_exchange, ea.label as account_label
       FROM strategy_positions sp
       JOIN strategies s ON sp.strategy_id = s.id
       LEFT JOIN exchange_accounts ea ON s.account_id = ea.id
       WHERE sp.status != 'closed' 
          OR (sp.status = 'closed' AND sp.closed_at > NOW() - INTERVAL '3 days')
       ORDER BY sp.opened_at DESC`
    );
    const dbPositions = posResult.rows;

    // 2. Fetch real-time positions from all active accounts via executor
    const accountsResult = await pool.query(
      `SELECT id FROM exchange_accounts WHERE is_active = true`
    );
    const activeAccounts = accountsResult.rows;

    const executorPositionsMap = new Map<string, any>();
    await Promise.all(activeAccounts.map(async (acc) => {
      try {
        const resp = await fetch(`${EXECUTOR_URL}/accounts/${acc.id}/positions`, {
          signal: AbortSignal.timeout(5000)
        });
        if (resp.ok) {
          const positions = await resp.json();
          if (Array.isArray(positions)) {
            positions.forEach(p => {
              // Positions use long/short; key must match strategy_positions.side
              const key = `${acc.id}:${p.symbol}:${p.side}`;
              executorPositionsMap.set(key, p);
            });
          }
        }
      } catch (e) {
        console.error(`Failed to fetch positions for account ${acc.id}:`, e);
      }
    }));

    // 3. Merge and enrich
    const enriched = await Promise.all(dbPositions.map(async dbPos => {
      const key = `${dbPos.account_id}:${dbPos.symbol}:${dbPos.side}`;
      const realPos = executorPositionsMap.get(key);

      // Exchange-confirmed size: live feed first, then reconciler snapshot when divergent
      const sizeExchange: number | null = realPos
        ? Number(realPos.size)
        : (dbPos.reconcile_divergent ? Number(dbPos.reconcile_exchange_size) : null);

      const enrichedPos: any = {
        id:                dbPos.id,
        symbol:            dbPos.symbol,
        side:              dbPos.side,
        status:            dbPos.status,
        leverage:          realPos ? (realPos.leverage ?? dbPos.leverage) : dbPos.leverage,
        margin_mode:       dbPos.margin_mode,
        strategy_name:     dbPos.strategy_name,
        account_id:        dbPos.account_id,
        account_label:     dbPos.account_label,
        account_exchange:  dbPos.account_exchange,
        opened_at:         dbPos.opened_at,
        closed_at:         dbPos.closed_at,
        entry_price:       Number(dbPos.entry_price) || (realPos ? Number(realPos.entry_price) : 0),
        mark_price:        realPos ? Number(realPos.mark_price || realPos.entry_price) : Number(dbPos.entry_price),
        close_price:       Number(dbPos.closing_price || 0),
        size:              Number(dbPos.size),
        size_exchange:     sizeExchange,
        size_divergent:    Boolean(dbPos.reconcile_divergent),
        margin:            0, // computed below from entry_price * size / leverage
        margin_exchange:   null as number | null,
        realized_pnl:      Number(dbPos.pnl_realized || 0),
        realized_pnl_fees: 0,
        unrealized_pnl:    realPos ? Number(realPos.unrealized_pnl) : Number(dbPos.pnl_unrealized),
        pnl_pct:           0,
        close_reason:      dbPos.close_reason || (dbPos.status === 'closed' ? 'Manual Close' : null),
        strategy_type:     dbPos.strategy_type,
        strategy_source:   dbPos.strategy_source,
        destination:       dbPos.account_exchange || 'exchange',
      };

      // Compute margin from position notional — no margin column in DB
      if (enrichedPos.entry_price > 0 && enrichedPos.size > 0) {
        enrichedPos.margin = (enrichedPos.entry_price * enrichedPos.size) / (enrichedPos.leverage || 1);
      }

      // Exchange-confirmed margin uses exchange size when available
      if (sizeExchange !== null && sizeExchange > 0 && enrichedPos.entry_price > 0) {
        enrichedPos.margin_exchange = (enrichedPos.entry_price * sizeExchange) / (enrichedPos.leverage || 1);
      }

      // P&L % — open uses unrealized, closed uses realized
      const pnlForPct = enrichedPos.status === 'closed'
        ? enrichedPos.realized_pnl
        : (enrichedPos.unrealized_pnl || 0);
      enrichedPos.pnl_pct = enrichedPos.margin > 0 ? (pnlForPct / enrichedPos.margin) * 100 : 0;

      return enrichedPos;
    }));

    res.json(enriched);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /positions/:id/close — close a position via listener (which attributes to strategy)
router.post('/:id/close', async (req: Request, res: Response) => {
  try {
    const size = req.body?.size ?? undefined;

    const listenerResp = await fetch(`${LISTENER_URL}/positions/${req.params.id}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(size !== undefined ? { size: Number(size) } : {}),
      signal: AbortSignal.timeout(35000),
    });

    if (!listenerResp.ok) {
      const errorData = await listenerResp.json() as any;
      return res.status(listenerResp.status).json({ error: errorData.detail || errorData.error || 'Listener failed to close position' });
    }

    const result = await listenerResp.json() as any;
    if (!result.success) {
      return res.status(502).json({ error: result.error || result.error_msg || 'Close failed' });
    }

    res.json({ success: true, result });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /positions/:id/refresh — refresh a stale position (re-check status)
router.post('/:id/refresh', async (req: Request, res: Response) => {
  // For now, refreshing just means the next GET /positions will re-fetch from exchange
  // We can also trigger an immediate check here if we want.
  res.json({ success: true, message: 'Refresh triggered' });
});

// GET /positions/:id/orders — L3 lazy order timeline for a position
// Returns entry order + all close orders ordered by time, with OEL fee when available.
router.get('/:id/orders', async (req: Request, res: Response) => {
  try {
    const { rows } = await getPool().query(`
      SELECT
        o.id,
        o.received_at AS time,
        CASE
          WHEN o.id = sp.opening_order_id                               THEN 'entry'
          WHEN o.signal_source IN ('stop_loss', 'sl', 'stop-loss')      THEN 'stop-loss'
          WHEN o.signal_source IN ('take_profit', 'tp', 'take-profit')   THEN 'take-profit'
          WHEN o.closes_position_id = sp.id
            AND sp.status = 'closed'
            AND sp.size > 0
            AND (
              SELECT COALESCE(SUM(o2.size), 0)
              FROM orders o2
              WHERE o2.closes_position_id = sp.id
                AND o2.received_at <= o.received_at
            ) >= sp.size * 0.99                                          THEN 'close'
          ELSE 'partial-close'
        END AS type,
        o.actual_fill_price AS fill,
        o.size AS delta,
        o.status,
        o.actual_fill_price AS avg_fill,
        o.pnl AS realized,
        oel.exchange_fee AS fee
      FROM strategy_positions sp, orders o
      LEFT JOIN order_execution_log oel
        ON oel.exchange_order_id = o.exchange_order_id
        AND o.exchange_order_id IS NOT NULL
      WHERE sp.id = $1
        AND (o.id = sp.opening_order_id OR o.closes_position_id = sp.id)
      ORDER BY o.received_at ASC
    `, [req.params.id]);

    if (rows.length === 0) {
      const check = await getPool().query(
        'SELECT id FROM strategy_positions WHERE id = $1', [req.params.id]
      );
      if (check.rowCount === 0) {
        return res.status(404).json({ error: `Position not found: ${req.params.id}` });
      }
    }

    res.json(rows.map(r => ({
      id:     r.id,
      time:   r.time,
      type:   r.type,
      fill:   r.fill    != null ? Number(r.fill)    : null,
      delta:  r.delta   != null ? Number(r.delta)   : null,
      status: r.status,
      key: {
        avg_fill: r.avg_fill  != null ? Number(r.avg_fill)  : null,
        realized: r.realized  != null ? Number(r.realized)  : null,
        fee:      r.fee       != null ? Number(r.fee)        : null,
      },
    })));
  } catch (err) {
    console.error(`Error fetching orders for position ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error fetching position orders' });
  }
});

export default router;
