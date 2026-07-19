import { Router, Request, Response } from 'express';
import { getPool } from '../db';

// Cross-venue spread harvest (docs/design/SPREAD_HARVEST.md phases 2-3).
// Plans/positions are read straight from the DB; execute/close proxy to the
// order-executor, which owns the adapters and the two-leg rollback logic.

const router = Router();
const EXECUTOR_URL = process.env.EXECUTOR_URL || 'http://order-executor:8004';
const AI_URL = process.env.AI_SIGNAL_GENERATOR_URL || 'http://ai-signal-generator:8005';

// GET /spread/monitor — live per-coin trailing spreads from the monitor
router.get('/monitor', async (_req: Request, res: Response) => {
  try {
    const resp = await fetch(`${AI_URL}/internal/spread-monitor/status`, {
      signal: AbortSignal.timeout(10_000),
    });
    res.status(resp.status).json(await resp.json());
  } catch (e: any) {
    res.status(502).json({ error: e.message });
  }
});

// GET /spread/funding-monitor — live per-coin trailing 3d Binance funding
router.get('/funding-monitor', async (_req: Request, res: Response) => {
  try {
    const resp = await fetch(`${AI_URL}/internal/funding-monitor/status`, {
      signal: AbortSignal.timeout(10_000),
    });
    res.status(resp.status).json(await resp.json());
  } catch (e: any) {
    res.status(502).json({ error: e.message });
  }
});

// GET /spread/funding-plans — single-venue funding-harvest plans (execution
// for these is not built yet — surfaced as informational cards)
router.get('/funding-plans', async (_req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      'SELECT * FROM funding_harvest_plans ORDER BY created_at DESC LIMIT 50');
    res.json({ plans: result.rows });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /spread/plans — recent plans, newest first
router.get('/plans', async (_req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      'SELECT * FROM spread_plans ORDER BY created_at DESC LIMIT 50');
    res.json({ plans: result.rows });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /spread/positions — recent two-leg positions, newest first
router.get('/positions', async (_req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      'SELECT * FROM spread_positions ORDER BY opened_at DESC LIMIT 50');
    res.json({ positions: result.rows });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /spread/plans/:id/execute — the operator "confirm" of armed+confirm
router.post('/plans/:id/execute', async (req: Request, res: Response) => {
  try {
    const resp = await fetch(`${EXECUTOR_URL}/spread/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: req.params.id }),
      signal: AbortSignal.timeout(60_000),
    });
    const data = await resp.json();
    res.status(resp.status).json(data);
  } catch (e: any) {
    res.status(502).json({ error: e.message });
  }
});

// POST /spread/positions/:id/close — manual unwind of both legs
router.post('/positions/:id/close', async (req: Request, res: Response) => {
  try {
    const resp = await fetch(`${EXECUTOR_URL}/spread/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ position_id: req.params.id, reason: 'manual' }),
      signal: AbortSignal.timeout(60_000),
    });
    const data = await resp.json();
    res.status(resp.status).json(data);
  } catch (e: any) {
    res.status(502).json({ error: e.message });
  }
});

export default router;
