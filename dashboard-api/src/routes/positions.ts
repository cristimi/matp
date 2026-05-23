import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();
const LISTENER_URL = process.env.LISTENER_URL || 'http://order-listener:8001';

router.get('/', async (req: Request, res: Response) => {
  try {
    const pool = getPool();
    const result = await pool.query(`
      SELECT 
        sp.*, 
        b.symbol as base_asset, 
        q.symbol as quote_asset
      FROM strategy_positions sp
      JOIN trading_pairs tp ON sp.pair_id = tp.id
      JOIN assets b ON tp.base_asset_id = b.id
      JOIN assets q ON tp.quote_asset_id = q.id
      WHERE sp.status = 'open'
    `);
    
    const positions = result.rows.map(row => ({
      id: row.id,
      pair: {
        base: row.base_asset,
        quote: row.quote_asset,
        label: `${row.base_asset}/${row.quote_asset}`
      },
      side: row.side,
      size: row.size,
      entryPx: row.entry_price,
      markPx: row.current_price,
      closing_price: row.closing_price,
      unrealizedPnl: row.pnl_unrealized,
      realizedPnl: row.pnl_realized,
      platform: row.exchange,
      status: row.status
    }));

    res.json(positions);
  } catch (error: any) {
    res.status(500).json({ error: 'Failed to fetch positions', detail: error.message });
  }
});

router.post('/:symbol/close', async (req: Request, res: Response) => {
  // ... (keep as is for now, mapping will happen in order-listener)
  try {
    const response = await fetch(`${LISTENER_URL}/positions/${req.params.symbol}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (error) {
    res.status(500).json({ error: 'Failed to close position' });
  }
});

export default router;
