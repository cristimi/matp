import { Router, Request, Response } from 'express';
import { getPool } from '../db';
import { getRedis } from '../redis';
import { encryptConfigValue } from '../configSecret';

// ── Config Router ──────────────────────────────────────────────────────────────
export const configRouter = Router();

const LLM_PROVIDERS = ['anthropic', 'openai', 'gemini', 'groq', 'cerebras', 'zhipu', 'openrouter'] as const;
type LlmProvider = typeof LLM_PROVIDERS[number];

const AI_URL = process.env.AI_SIGNAL_GENERATOR_URL || 'http://ai-signal-generator:8005';

// LLM provider API keys, one llm_keys row per key (a provider can hold several),
// encrypted at rest. ai-signal-generator rotates between a provider's keys at
// runtime and hot-reloads on change (notifyKeyReload below); strategy-tester and
// social-listener take the top-priority key at their next restart.

// Fire-and-forget: tell ai-signal-generator to re-read llm_keys so edits apply
// without a restart. Failure is non-fatal — the service also reloads on restart.
function notifyKeyReload(): void {
  fetch(`${AI_URL}/internal/llm-keys/reload`, { method: 'POST' })
    .catch(err => console.warn('llm-keys: reload notify failed:', err?.message ?? err));
}

const KEY_COLUMNS = 'id, provider, label, enabled, priority, created_at, updated_at';

// Grouped by provider (every provider present, possibly empty). Never returns key material.
configRouter.get('/llm-keys', async (_req: Request, res: Response) => {
  const { rows } = await getPool().query(
    `SELECT ${KEY_COLUMNS} FROM llm_keys ORDER BY provider, priority, id`,
  );
  const result: Record<LlmProvider, any[]> = {} as any;
  for (const p of LLM_PROVIDERS) result[p] = [];
  for (const r of rows) {
    if (result[r.provider as LlmProvider]) result[r.provider as LlmProvider].push(r);
  }
  res.json(result);
});

// Runtime rotation state (active / cooldown / auth_failed) proxied from
// ai-signal-generator — in-memory there, so this is best-effort.
configRouter.get('/llm-keys/status', async (_req: Request, res: Response) => {
  try {
    // Generous timeout: this homelab host regularly sits under heavy load and
    // cross-container requests can take several seconds.
    const r = await fetch(`${AI_URL}/internal/llm-keys/status`, {
      signal: AbortSignal.timeout(8000),
    });
    res.json(await r.json());
  } catch {
    res.json({ providers: {} });
  }
});

configRouter.post('/llm-keys', async (req: Request, res: Response) => {
  const { provider, label, api_key } = req.body;
  if (!LLM_PROVIDERS.includes(provider)) {
    return res.status(400).json({ error: `provider must be one of: ${LLM_PROVIDERS.join(', ')}` });
  }
  if (!api_key || typeof api_key !== 'string' || api_key.trim().length === 0) {
    return res.status(400).json({ error: 'api_key is required' });
  }
  const encrypted = encryptConfigValue(api_key.trim());
  const { rows } = await getPool().query(
    `INSERT INTO llm_keys (provider, label, encrypted_key, priority)
     VALUES ($1, $2, $3,
             COALESCE((SELECT MAX(priority) + 1 FROM llm_keys WHERE provider = $1), 0))
     RETURNING ${KEY_COLUMNS}`,
    [provider, (typeof label === 'string' && label.trim()) || 'default', encrypted],
  );
  notifyKeyReload();
  res.status(201).json(rows[0]);
});

// Update label / enabled / priority, or replace the key material itself.
configRouter.patch('/llm-keys/:id', async (req: Request, res: Response) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id)) return res.status(400).json({ error: 'invalid id' });
  const { label, enabled, priority, api_key } = req.body;

  const sets: string[] = [];
  const vals: any[] = [];
  if (typeof label === 'string' && label.trim()) { vals.push(label.trim()); sets.push(`label = $${vals.length}`); }
  if (typeof enabled === 'boolean')              { vals.push(enabled);      sets.push(`enabled = $${vals.length}`); }
  if (Number.isInteger(priority))                { vals.push(priority);     sets.push(`priority = $${vals.length}`); }
  if (typeof api_key === 'string' && api_key.trim()) {
    vals.push(encryptConfigValue(api_key.trim()));
    sets.push(`encrypted_key = $${vals.length}`);
  }
  if (sets.length === 0) return res.status(400).json({ error: 'nothing to update' });

  vals.push(id);
  const { rows } = await getPool().query(
    `UPDATE llm_keys SET ${sets.join(', ')}, updated_at = now()
     WHERE id = $${vals.length} RETURNING ${KEY_COLUMNS}`,
    vals,
  );
  if (rows.length === 0) return res.status(404).json({ error: 'key not found' });
  notifyKeyReload();
  res.json(rows[0]);
});

configRouter.delete('/llm-keys/:id', async (req: Request, res: Response) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id)) return res.status(400).json({ error: 'invalid id' });
  const { rowCount } = await getPool().query('DELETE FROM llm_keys WHERE id = $1', [id]);
  if (rowCount === 0) return res.status(404).json({ error: 'key not found' });
  notifyKeyReload();
  res.json({ deleted: id });
});

configRouter.get('/', async (_req: Request, res: Response) => {
  const { rows } = await getPool().query('SELECT key, value, updated_at FROM config');
  const result: Record<string, { value: string; updated_at: Date }> = {};
  for (const r of rows) {
    // Mask sensitive values
    result[r.key] = {
      value: r.key.includes('key') || r.key.includes('secret') || r.key.includes('private')
        ? '***masked***'
        : r.value,
      updated_at: r.updated_at,
    };
  }
  res.json(result);
});

configRouter.put('/active_platform', async (req: Request, res: Response) => {
  const { platform } = req.body;
  const allowed = ['blofin', 'hyperliquid'];
  if (!allowed.includes(platform)) {
    return res.status(400).json({ error: `Platform must be one of: ${allowed.join(', ')}` });
  }
  const now = new Date();
  await getPool().query(
    `INSERT INTO config (key, value, updated_at) VALUES ('active_platform', $1, $2)
     ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = $2`,
    [platform, now],
  );
  // Invalidate Redis cache
  await getRedis().del('config:active_platform');
  res.json({ active_platform: platform, updated_at: now.toISOString() });
});

export default configRouter;
