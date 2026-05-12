import { Router, Request, Response } from 'express';
import { getPool } from '../db';
import { getRedis } from '../redis';

// ── Config Router ──────────────────────────────────────────────────────────────
export const configRouter = Router();

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
