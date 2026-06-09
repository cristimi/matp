import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();
const EXECUTOR_URL = process.env.EXECUTOR_URL || 'http://order-executor:8004';

// GET /positions — aggregate strategy positions enriched with real-time executor data
router.get('/', async (_req: Request, res: Response) => {
  try {
    const pool = getPool();
    
    // 1. Fetch all strategy positions (open, stale, and last 20 closed)
    const posResult = await pool.query(
      `SELECT sp.*, s.name as strategy_name, s.type as strategy_type, s.account_id, ea.exchange as account_exchange, ea.label as account_label
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
    const enriched = dbPositions.map(dbPos => {
      const key = `${dbPos.account_id}:${dbPos.symbol}:${dbPos.side}`;
      const realPos = executorPositionsMap.get(key);

      const enrichedPos = {
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
        mark_price:        realPos ? Number(realPos.mark_price || realPos.entry_price) : Number(dbPos.current_price),
        close_price:       Number(dbPos.closing_price || dbPos.close_price || 0),
        size:              Number(dbPos.size),
        margin:            0, // computed below from entry_price * size / leverage
        realized_pnl:      Number(dbPos.pnl_realized || 0),
        realized_pnl_fees: 0,
        unrealized_pnl:    realPos ? Number(realPos.unrealized_pnl) : Number(dbPos.pnl_unrealized),
        pnl_pct:           0,
        close_reason:      dbPos.close_reason || (dbPos.status === 'closed' ? 'Manual Close' : null),
        strategy_type:     dbPos.strategy_type,
        destination:       dbPos.account_exchange || 'exchange',
      };

      // Compute margin from position notional — no margin column in DB
      if (enrichedPos.entry_price > 0 && enrichedPos.size > 0) {
        enrichedPos.margin = (enrichedPos.entry_price * enrichedPos.size) / (enrichedPos.leverage || 1);
      }

      // P&L % — open uses unrealized, closed uses realized
      const pnlForPct = enrichedPos.status === 'closed'
        ? enrichedPos.realized_pnl
        : (enrichedPos.unrealized_pnl || 0);
      enrichedPos.pnl_pct = enrichedPos.margin > 0 ? (pnlForPct / enrichedPos.margin) * 100 : 0;

      // Determine 'stale' status
      if (enrichedPos.status === 'open' && !realPos) {
        enrichedPos.status = 'stale';
      }

      return enrichedPos;
    });

    res.json(enriched);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /positions/:id/close — close a position via executor
router.post('/:id/close', async (req: Request, res: Response) => {
  try {
    const pool = getPool();
    // 1. Fetch position details
    const posResult = await pool.query(
      `SELECT sp.id, sp.symbol, sp.side, sp.margin_mode, s.account_id, sp.status
       FROM strategy_positions sp
       JOIN strategies s ON sp.strategy_id = s.id
       WHERE sp.id = $1`,
      [req.params.id]
    );

    if (posResult.rowCount === 0) {
      return res.status(404).json({ error: 'Position not found' });
    }

    const pos = posResult.rows[0];
    if (pos.status === 'closed') {
      return res.status(400).json({ error: 'Position already closed' });
    }

    // 2. Call executor to close on exchange
    const execResp = await fetch(`${EXECUTOR_URL}/accounts/${pos.account_id}/positions/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: pos.symbol, side: pos.side, margin_mode: pos.margin_mode })
    });

    if (!execResp.ok) {
      const errorData = await execResp.json() as any;
      return res.status(execResp.status).json({ error: errorData.detail || 'Executor failed to close position' });
    }

    const result = await execResp.json() as any;

    // Guard: if the exchange itself rejected the close, do NOT mark position closed in DB
    if (!result.success) {
      return res.status(502).json({ error: result.error_msg || 'Exchange rejected close order — position remains open' });
    }

    // 3. Update DB status to closed
    await pool.query(
      `UPDATE strategy_positions
       SET status = 'closed',
           closed_at = NOW(),
           closing_price = $1,
           pnl_realized = $2
       WHERE id = $3`,
      [result.actual_fill_price, result.realized_pnl, pos.id]
    );

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

export default router;
