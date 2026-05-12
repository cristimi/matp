import { Router, Request, Response } from 'express';

const router = Router();
const GENERATOR_URL = process.env.GENERATOR_URL || 'http://order-generator:8002';

async function proxyToGenerator(path: string, method = 'GET', body?: unknown) {
  const opts: RequestInit = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${GENERATOR_URL}${path}`, opts);
  return { status: res.status, data: await res.json() };
}

router.get('/', async (_req: Request, res: Response) => {
  const { status, data } = await proxyToGenerator('/strategies');
  res.status(status).json(data);
});

router.post('/:id/enable', async (req: Request, res: Response) => {
  const { status, data } = await proxyToGenerator(`/strategies/${req.params.id}/enable`, 'POST');
  res.status(status).json(data);
});

router.post('/:id/disable', async (req: Request, res: Response) => {
  const { status, data } = await proxyToGenerator(`/strategies/${req.params.id}/disable`, 'POST');
  res.status(status).json(data);
});

router.get('/:id/config', async (req: Request, res: Response) => {
  const { status, data } = await proxyToGenerator(`/strategies/${req.params.id}/config`);
  res.status(status).json(data);
});

export default router;
