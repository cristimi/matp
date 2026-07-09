import { Router, Request, Response } from 'express';

export const systemRouter = Router();

// Services with an HTTP /health endpoint, reachable only from inside the docker
// network (none of these publish a host port — see docker-compose.yml's port-isolation
// rule). dashboard-api fans out server-side so the browser never needs direct access.
const HTTP_SERVICES: { name: string; url: string }[] = [
  { name: 'dashboard-api',       url: 'http://localhost:8003/health' },
  { name: 'order-listener',      url: 'http://order-listener:8001/health' },
  { name: 'order-generator',     url: 'http://order-generator:8002/health' },
  { name: 'order-executor',      url: 'http://order-executor:8004/health' },
  { name: 'ai-signal-generator', url: 'http://ai-signal-generator:8005/health' },
  { name: 'strategy-tester',     url: 'http://strategy-tester:8006/health' },
  { name: 'notification-service', url: 'http://notification-service:8010/health' },
];

// Background workers with no HTTP surface at all (no FastAPI app, no port) —
// health can only be inferred from `docker compose ps`, not from this endpoint.
const WORKER_SERVICES = ['market-ingestion', 'signal-engine', 'social-listener'];

async function checkOne(name: string, url: string): Promise<{ name: string; ok: boolean; detail?: string }> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return { name, ok: res.ok };
  } catch (e: any) {
    return { name, ok: false, detail: e.message || String(e) };
  }
}

systemRouter.get('/health-grid', async (_req: Request, res: Response) => {
  const http = await Promise.all(HTTP_SERVICES.map(s => checkOne(s.name, s.url)));
  res.json({ http, workers: WORKER_SERVICES });
});

export default systemRouter;
