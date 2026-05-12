import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();

const PERIOD_FILTER: Record<string, string> = {
  today: "received_at >= CURRENT_DATE",
  '7d':  "received_at >= NOW() - INTERVAL '7 days'",
  '30d': "received_at >= NOW() - INTERVAL '30 days'",
  all:   "1=1",
};

router.get('/', async (req: Request, res: Response) => {
  const period = (req.query.period as string) || 'today';
  const filter = PERIOD_FILTER[period] || PERIOD_FILTER.today;
  const pool = getPool();

  const { rows } = await pool.query(`
    SELECT
      COUNT(*)                                        AS total_orders,
      COUNT(*) FILTER (WHERE status = 'filled')       AS filled,
      COUNT(*) FILTER (WHERE status = 'route_failed') AS failed,
      COUNT(*) FILTER (WHERE pnl > 0)                 AS win_count,
      COUNT(*) FILTER (WHERE pnl < 0)                 AS loss_count,
      COALESCE(SUM(pnl), 0)                           AS total_pnl,
      COALESCE(AVG(pnl), 0)                           AS avg_pnl,
      platform,
      strategy_id
    FROM orders
    WHERE ${filter}
    GROUP BY GROUPING SETS ((), (platform), (strategy_id))
  `);

  // Aggregate
  const totals = rows.find((r) => !r.platform && !r.strategy_id) || {};
  const byPlatform: Record<string, unknown> = {};
  const byStrategy: Record<string, unknown> = {};

  for (const r of rows) {
    if (r.platform && !r.strategy_id) byPlatform[r.platform] = r;
    if (r.strategy_id && !r.platform) byStrategy[r.strategy_id] = r;
  }

  const filled = parseInt(totals.filled || '0');
  const wins = parseInt(totals.win_count || '0');
  const losses = parseInt(totals.loss_count || '0');
  const winRate = filled > 0 ? Math.round((wins / filled) * 100) : 0;

  res.json({
    period,
    total_orders: parseInt(totals.total_orders || '0'),
    filled,
    failed: parseInt(totals.failed || '0'),
    win_count: wins,
    loss_count: losses,
    win_rate: winRate,
    total_pnl: parseFloat(totals.total_pnl || '0'),
    avg_pnl: parseFloat(totals.avg_pnl || '0'),
    by_platform: byPlatform,
    by_strategy: byStrategy,
  });
});

export default router;
