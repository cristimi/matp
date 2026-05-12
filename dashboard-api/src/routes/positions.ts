import { Router, Request, Response } from 'express';

const router = Router();
const LISTENER_URL = process.env.LISTENER_URL || 'http://order-listener:8001';

router.get('/', async (_req: Request, res: Response) => {
  // Fetch positions from the listener which delegates to exchange adapters
  try {
    const resp = await fetch(`${LISTENER_URL}/positions`);
    const data = await resp.json();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch positions from listener', details: String(err) });
  }
});

router.post('/:symbol/close', async (req: Request, res: Response) => {
  try {
    const resp = await fetch(`${LISTENER_URL}/positions/${req.params.symbol}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    const data = await resp.json();
    res.status(resp.status).json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to close position', details: String(err) });
  }
});

export default router;
