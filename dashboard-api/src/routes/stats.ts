import { Router, Request, Response } from 'express';
import { pool } from '../db';

const router = Router();

type Period = 'today' | '7d' | '30d' | 'all';

function periodFilter(period: Period): string {
  switch (period) {
    case 'today': return `received_at >= CURRENT_DATE`;
    case '7d':    return `received_at >= NOW() - INTERVAL '7 days'`;
    case '30d':   return `received_at >= NOW() - INTERVAL '30 days'`;
    case 'all':   return `1=1`;
    default:      return `1=1`;
  }
}

// GET /stats?period=today|7d|30d|all
router.get('/', async (req: Request, res: Response) => {
  const period = (req.query.period as Period) || 'today';
  const filter = periodFilter(period);

  try {
    // ── Global aggregates ──────────────────────────────────────────
    const globalResult = await pool.query(`
      SELECT
        COUNT(*)                                                        AS total_orders,
        COUNT(*) FILTER (WHERE status = 'filled')                       AS filled,
        COUNT(*) FILTER (WHERE status IN (
          'route_failed','lag_failed','rejected'))                       AS failed,
        COUNT(*) FILTER (WHERE pnl > 0)                                 AS win_count,
        COUNT(*) FILTER (WHERE pnl < 0)                                 AS loss_count,
        COUNT(*) FILTER (WHERE status = 'filled' AND side = 'buy')      AS long_count,
        COUNT(*) FILTER (WHERE status = 'filled' AND side = 'sell')     AS short_count,
        COALESCE(SUM(pnl), 0)                                           AS total_pnl,
        COALESCE(AVG(pnl) FILTER (WHERE pnl IS NOT NULL), 0)            AS avg_pnl,
        (SELECT COALESCE(SUM(pnl_unrealized), 0)
         FROM strategy_positions WHERE status = 'open')                 AS unrealized_pnl
      FROM orders
      WHERE ${filter}
    `);

    const g = globalResult.rows[0];
    const total   = parseInt(g.total_orders);
    const wins    = parseInt(g.win_count);
    const losses  = parseInt(g.loss_count);
    const decided = wins + losses;
    const win_rate = decided > 0
      ? parseFloat(((wins / decided) * 100).toFixed(2))
      : 0;

    // ── Per-account breakdown ──────────────────────────────────────
    const byAccountResult = await pool.query(`
      SELECT
        o.account_id,
        ea.label                                           AS label,
        ea.exchange                                        AS exchange,
        ea.mode                                            AS mode,
        COUNT(*)                                           AS total_orders,
        COUNT(*) FILTER (WHERE o.status = 'filled')       AS filled,
        COUNT(*) FILTER (WHERE o.status IN (
          'route_failed','lag_failed','rejected'))         AS failed,
        COUNT(*) FILTER (WHERE o.pnl > 0)                 AS win_count,
        COUNT(*) FILTER (WHERE o.pnl < 0)                 AS loss_count,
        COALESCE(SUM(o.pnl), 0)                           AS total_pnl,
        COALESCE(AVG(o.pnl) FILTER (
          WHERE o.pnl IS NOT NULL), 0)                    AS avg_pnl
      FROM orders o
      LEFT JOIN exchange_accounts ea ON ea.id = o.account_id
      WHERE ${filter}
        AND o.account_id IS NOT NULL
      GROUP BY o.account_id, ea.label, ea.exchange, ea.mode
      ORDER BY total_pnl DESC
    `);

    const by_account: Record<string, any> = {};
    for (const row of byAccountResult.rows) {
      const w = parseInt(row.win_count);
      const l = parseInt(row.loss_count);
      const d = w + l;
      by_account[row.account_id] = {
        label:        row.label,
        exchange:     row.exchange,
        mode:         row.mode,
        total_orders: parseInt(row.total_orders),
        filled:       parseInt(row.filled),
        failed:       parseInt(row.failed),
        win_count:    w,
        loss_count:   l,
        win_rate:     d > 0 ? parseFloat(((w / d) * 100).toFixed(2)) : 0,
        total_pnl:    parseFloat(row.total_pnl),
        avg_pnl:      parseFloat(row.avg_pnl),
      };
    }

    // ── Per-strategy breakdown ─────────────────────────────────────
    const byStrategyResult = await pool.query(`
      SELECT
        o.strategy_id,
        s.name                                             AS name,
        COUNT(*)                                           AS total_orders,
        COUNT(*) FILTER (WHERE o.status = 'filled')       AS filled,
        COUNT(*) FILTER (WHERE o.status IN (
          'route_failed','lag_failed','rejected'))         AS failed,
        COUNT(*) FILTER (WHERE o.pnl > 0)                 AS win_count,
        COUNT(*) FILTER (WHERE o.pnl < 0)                 AS loss_count,
        COALESCE(SUM(o.pnl), 0)                           AS total_pnl,
        COALESCE(AVG(o.pnl) FILTER (
          WHERE o.pnl IS NOT NULL), 0)                    AS avg_pnl
      FROM orders o
      LEFT JOIN strategies s ON s.id = o.strategy_id
      WHERE ${filter}
      GROUP BY o.strategy_id, s.name
      ORDER BY total_pnl DESC
    `);

    const by_strategy: Record<string, any> = {};
    for (const row of byStrategyResult.rows) {
      const w = parseInt(row.win_count);
      const l = parseInt(row.loss_count);
      const d = w + l;
      by_strategy[row.strategy_id] = {
        name:         row.name,
        total_orders: parseInt(row.total_orders),
        filled:       parseInt(row.filled),
        failed:       parseInt(row.failed),
        win_count:    w,
        loss_count:   l,
        win_rate:     d > 0 ? parseFloat(((w / d) * 100).toFixed(2)) : 0,
        total_pnl:    parseFloat(row.total_pnl),
        avg_pnl:      parseFloat(row.avg_pnl),
      };
    }

    res.json({
      period,
      total_orders:   total,
      filled:         parseInt(g.filled),
      failed:         parseInt(g.failed),
      win_count:      wins,
      loss_count:     losses,
      long_count:     parseInt(g.long_count),
      short_count:    parseInt(g.short_count),
      win_rate,
      total_pnl:      parseFloat(g.total_pnl),
      avg_pnl:        parseFloat(g.avg_pnl),
      unrealized_pnl: parseFloat(g.unrealized_pnl),
      by_account,
      by_strategy,
    });

  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
