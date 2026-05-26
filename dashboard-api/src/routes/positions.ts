import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();
const LISTENER_URL = process.env.LISTENER_URL || 'http://order-listener:8001';

router.get('/', async (req: Request, res: Response) => {
  try {
    const response = await fetch(`${LISTENER_URL}/positions`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    });

    if (!response.ok) {
      throw new Error(`Order listener failed: ${response.statusText}`);
    }

    const data = await response.json() as any[];
    const mappedData = data.map((pos: any) => ({
      ...pos,
      pair: { 
        base: pos.base_asset,
        quote: pos.quote_asset,
        label: pos.symbol || `${pos.base_asset}-${pos.quote_asset}`
      }
    }));
    res.json(mappedData);
  } catch (error: any) {
    res.status(500).json({ error: 'Failed to fetch positions', detail: error.message });
  }
});

router.post('/:symbol/close', async (req: Request, res: Response) => {
  // ... (keep as is for now, mapping will happen in order-listener)
  try {
  console.log(`DEBUG: Sending close request to: ${LISTENER_URL}/positions/${req.params.symbol}/close`);
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
