import { Router, Request, Response } from 'express';

const router = Router();
const LISTENER_URL = process.env.LISTENER_URL || 'http://order-listener:8001';

router.get('/', async (req: Request, res: Response) => {
  try {
    const response = await fetch(`${LISTENER_URL}/positions`);
    const data = await response.json();
    res.json(data);
  } catch (error) {
    res.status(500).json({ error: 'Failed to fetch positions' });
  }
});

router.post('/:symbol/close', async (req: Request, res: Response) => {
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
