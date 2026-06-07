import { Router, Request, Response } from 'express';
import { pool, getPool } from '../db';

const router = Router();
const EXECUTOR_URL = process.env.EXECUTOR_URL || 'http://order-executor:8004';

// ── GET /accounts ────────────────────────────────────────────────────
// List all exchange accounts.
// NEVER return the credentials column.
router.get('/', async (_req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `SELECT id, exchange, mode, label, is_active, created_at, updated_at
       FROM exchange_accounts
       WHERE is_active = true
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
  try {
    const response = await fetch(
      `${EXECUTOR_URL}/accounts/${req.params.id}/invalidate`,
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

// GET /accounts/:id/balance — fetch live balance from executor
router.get('/:id/balance', async (req: Request, res: Response) => {
  try {
    const response = await fetch(
      `${EXECUTOR_URL}/accounts/${req.params.id}/balance`,
      { signal: AbortSignal.timeout(15000) }
    );
    if (!response.ok) {
      return res.status(502).json({
        error: `Executor returned ${response.status}`,
        total_balance: 0, available_balance: 0,
        used_margin: 0, currency: 'USDT',
      });
    }
    const data = await response.json();
    res.json(data);
  } catch (e: any) {
    // Return zeroed balance on error rather than 500
    // so the UI can still render with a "unavailable" state
    res.json({
      total_balance: 0, available_balance: 0,
      used_margin: 0, currency: 'USDT',
      error: e.message,
    });
  }
});

// GET /accounts/:id/meta — fetch safe account metadata from executor
router.get('/:id/meta', async (req: Request, res: Response) => {
  try {
    const response = await fetch(
      `${EXECUTOR_URL}/accounts/${req.params.id}/meta`,
      { signal: AbortSignal.timeout(10000) }
    );
    if (!response.ok) {
      return res.status(502).json({ error: `Executor returned ${response.status}` });
    }
    const data = await response.json();
    res.json(data);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /accounts/:id/credentials — update encrypted credentials
router.post('/:id/credentials', async (req: Request, res: Response) => {
  const { credentials_json } = req.body;

  if (!credentials_json) {
    return res.status(400).json({ error: 'credentials_json is required' });
  }

  try {
    // Step 0: fetch account exchange + mode for validation
    const acctResult = await getPool().query(
      `SELECT exchange, mode FROM exchange_accounts WHERE id = $1`,
      [req.params.id]
    );
    if (acctResult.rowCount === 0) {
      return res.status(404).json({ error: `Account not found: ${req.params.id}` });
    }
    const { exchange, mode } = acctResult.rows[0];

    // Step 0b: validate credentials against the exchange before storing
    const validateResp = await fetch(`${EXECUTOR_URL}/credentials/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exchange, mode, credentials_json }),
      signal: AbortSignal.timeout(15000),
    });
    if (!validateResp.ok) {
      return res.status(502).json({ error: 'Executor validation endpoint unreachable' });
    }
    const validateResult = await validateResp.json() as { valid: boolean; error?: string; detail?: string };
    if (!validateResult.valid) {
      return res.status(400).json({ error: validateResult.error || 'Credential validation failed' });
    }

    // HL duplicate check: ensure no existing active account uses the same API wallet
    // TODO(blofin-dedup): add equivalent check for Blofin using api_key comparison
    if (exchange === 'hyperliquid' && (validateResult as any).wallet) {
      const derivedWallet: string = (validateResult as any).wallet;
      const existingHL = await getPool().query(
        `SELECT id FROM exchange_accounts WHERE exchange = 'hyperliquid' AND is_active = true AND id != $1`,
        [req.params.id]
      );
      for (const existing of existingHL.rows) {
        try {
          const metaResp = await fetch(`${EXECUTOR_URL}/accounts/${existing.id}/meta`,
            { signal: AbortSignal.timeout(5000) });
          if (metaResp.ok) {
            const meta = await metaResp.json() as any;
            if (meta.wallet_address?.toLowerCase() === derivedWallet.toLowerCase()) {
              return res.status(409).json({
                error: `API wallet ${derivedWallet.slice(0, 10)}… is already registered on account "${existing.id}"`,
              });
            }
          }
        } catch { /* non-fatal: skip unresponsive account */ }
      }
    }

    // Strip validation-only fields (api_wallet is derived from private_key; not needed in storage)
    const creds = JSON.parse(credentials_json);
    delete creds.api_wallet;
    const clean_json = JSON.stringify(creds);

    // Step 1: encrypt via executor (MASTER_KEY stays in executor)
    const encryptResp = await fetch(
      `${EXECUTOR_URL}/credentials/encrypt`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials_json: clean_json }),
        signal: AbortSignal.timeout(10000),
      }
    );
    if (!encryptResp.ok) {
      const err = await encryptResp.text();
      return res.status(502).json({
        error: 'Executor encryption failed',
        detail: err,
      });
    }
    const { encrypted_b64 } = await encryptResp.json() as { encrypted_b64: string };

    // Step 2: decode base64 → buffer → store as bytea
    const credBuffer = Buffer.from(encrypted_b64, 'base64');
    await pool.query(
      `UPDATE exchange_accounts
       SET credentials = $1, updated_at = NOW()
       WHERE id = $2`,
      [credBuffer, req.params.id]
    );

    // Step 3: invalidate cached adapter instance in executor
    try {
      await fetch(
        `${EXECUTOR_URL}/accounts/${req.params.id}/invalidate`,
        { method: 'POST', signal: AbortSignal.timeout(5000) }
      );
    } catch {
      // Non-fatal: invalidation is best-effort
    }

    res.json({
      updated:  req.params.id,
      message:  'Credentials updated and adapter cache invalidated',
      detail:   validateResult.detail,
    });

  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
