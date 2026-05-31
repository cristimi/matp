import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();

// ── GET /accounts ────────────────────────────────────────────────────
// List all exchange accounts.
// NEVER return the credentials column.
router.get('/', async (_req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `SELECT id, exchange, mode, label, is_active, created_at, updated_at
       FROM exchange_accounts
       ORDER BY exchange ASC, mode ASC, label ASC`
    );
    res.json(result.rows);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── POST /accounts ───────────────────────────────────────────────────
// Create a new exchange account.
// Credentials are stored as a single null byte placeholder.
// The user must update credentials via PUT after creation.
// This is intentional — the encryption key lives in order-executor only.
router.post('/', async (req: Request, res: Response) => {
  const { id, exchange, mode, label } = req.body;

  if (!id || !exchange || !mode || !label) {
    return res.status(400).json({
      error: 'Missing required fields',
      required: ['id', 'exchange', 'mode', 'label'],
    });
  }

  const validExchanges = ['blofin', 'hyperliquid'];
  if (!validExchanges.includes(exchange)) {
    return res.status(400).json({
      error: `Invalid exchange. Must be one of: ${validExchanges.join(', ')}`,
    });
  }

  const validModes = ['live', 'demo'];
  if (!validModes.includes(mode)) {
    return res.status(400).json({
      error: `Invalid mode. Must be one of: ${validModes.join(', ')}`,
    });
  }

  try {
    await getPool().query(
      `INSERT INTO exchange_accounts (id, exchange, mode, label, credentials)
       VALUES ($1, $2, $3, $4, $5)`,
      [id, exchange, mode, label, Buffer.from([0x00])]
    );
    res.status(201).json({
      id, exchange, mode, label,
      is_active: true,
      note: 'Account created. Credentials are placeholder — update via PUT /accounts/:id/credentials',
    });
  } catch (e: any) {
    if (e.code === '23505') {  // unique_violation
      return res.status(409).json({ error: `Account ID already exists: ${id}` });
    }
    res.status(500).json({ error: e.message });
  }
});

// ── PUT /accounts/:id ────────────────────────────────────────────────
// Update account label or active status.
// Does NOT update credentials — that is a separate endpoint.
router.put('/:id', async (req: Request, res: Response) => {
  const { label, is_active } = req.body;

  if (label === undefined && is_active === undefined) {
    return res.status(400).json({
      error: 'Provide at least one field to update: label, is_active',
    });
  }

  try {
    const result = await getPool().query(
      `UPDATE exchange_accounts
       SET label     = COALESCE($1, label),
           is_active = COALESCE($2, is_active),
           updated_at = NOW()
       WHERE id = $3
       RETURNING id, exchange, mode, label, is_active`,
      [label ?? null, is_active ?? null, req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Account not found: ${req.params.id}` });
    }
    res.json(result.rows[0]);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── DELETE /accounts/:id ─────────────────────────────────────────────
// Soft-delete: sets is_active = false.
// Does not remove the row — preserves FK references from orders/strategies.
router.delete('/:id', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `UPDATE exchange_accounts
       SET is_active = false, updated_at = NOW()
       WHERE id = $1
       RETURNING id`,
      [req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Account not found: ${req.params.id}` });
    }
    res.json({ deactivated: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── POST /accounts/:id/invalidate ────────────────────────────────────
// Evict the account's cached adapter instance from the executor registry.
// Call this after updating credentials so the executor reloads them.
router.post('/:id/invalidate', async (req: Request, res: Response) => {
  const executorUrl = process.env.EXECUTOR_URL || 'http://order-executor:8004';
  try {
    const response = await fetch(
      `${executorUrl}/accounts/${req.params.id}/invalidate`,
      { method: 'POST' }
    );
    if (!response.ok) {
      const text = await response.text();
      return res.status(502).json({
        error: `Executor returned ${response.status}`,
        detail: text,
      });
    }
    const data = await response.json();
    res.json(data);
  } catch (e: any) {
    res.status(502).json({
      error: 'Could not reach order-executor',
      detail: e.message,
    });
  }
});

export default router;
