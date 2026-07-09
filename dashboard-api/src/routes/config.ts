import { Router, Request, Response } from 'express';
import { getPool } from '../db';
import { getRedis } from '../redis';
import { encryptConfigValue } from '../configSecret';

// ── Config Router ──────────────────────────────────────────────────────────────
export const configRouter = Router();

const LLM_PROVIDERS = ['anthropic', 'openai', 'gemini'] as const;
type LlmProvider = typeof LLM_PROVIDERS[number];

// LLM provider API keys, encrypted at rest (config.key = 'llm_key_<provider>').
// Consumed by ai-signal-generator / strategy-tester / social-listener at their own
// startup — a key saved here takes effect on that service's next restart, same as
// editing .env did before this existed.
configRouter.get('/llm-keys', async (_req: Request, res: Response) => {
  const { rows } = await getPool().query(
    `SELECT key, updated_at FROM config WHERE key = ANY($1)`,
    [LLM_PROVIDERS.map(p => `llm_key_${p}`)],
  );
  const byKey = new Map(rows.map(r => [r.key, r.updated_at]));
  const result: Record<LlmProvider, { configured: boolean; updated_at: Date | null }> =
    {} as any;
  for (const p of LLM_PROVIDERS) {
    const updatedAt = byKey.get(`llm_key_${p}`) ?? null;
    result[p] = { configured: updatedAt !== null, updated_at: updatedAt };
  }
  res.json(result);
});

configRouter.put('/llm-keys/:provider', async (req: Request, res: Response) => {
  const provider = req.params.provider as LlmProvider;
  if (!LLM_PROVIDERS.includes(provider)) {
    return res.status(400).json({ error: `Provider must be one of: ${LLM_PROVIDERS.join(', ')}` });
  }
  const { api_key } = req.body;
  if (!api_key || typeof api_key !== 'string' || api_key.trim().length === 0) {
    return res.status(400).json({ error: 'api_key is required' });
  }
  const encrypted = encryptConfigValue(api_key.trim());
  const now = new Date();
  await getPool().query(
    `INSERT INTO config (key, value, updated_at) VALUES ($1, $2, $3)
     ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = $3`,
    [`llm_key_${provider}`, encrypted, now],
  );
  res.json({ provider, configured: true, updated_at: now.toISOString() });
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
