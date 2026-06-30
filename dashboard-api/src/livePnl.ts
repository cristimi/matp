import { getPool } from './db';
import { getRedis } from './redis';

const EXECUTOR_URL = process.env.EXECUTOR_URL || 'http://order-executor:8004';
export const PNL_TICK_MS = parseInt(process.env.PNL_TICK_MS || '1000', 10);
const STALE_MS = PNL_TICK_MS * 4;

export const SNAPSHOT_KEY = 'pnl:live:snapshot';
export const SNAPSHOT_CHANNEL = 'pnl:live';

export interface PnlSnapshot {
  ts: number;
  strategies: Record<string, { open_pnl: number; position_ids: string[] }>;
  positions: Record<string, { mark_price: number; unrealized_pnl: number; liquidation_price: number | null }>;
}

let _lastSnapshot: PnlSnapshot | null = null;
export function getLastSnapshot(): PnlSnapshot | null { return _lastSnapshot; }
export function isSnapshotFresh(snap: PnlSnapshot): boolean {
  return Date.now() - snap.ts < STALE_MS;
}

async function tick(): Promise<void> {
  const pool = getPool();
  const redis = getRedis();

  // Query all OPEN positions with their strategy + account info in one shot
  const { rows } = await pool.query(`
    SELECT sp.id AS position_id, sp.strategy_id, s.account_id, sp.symbol, sp.side
    FROM strategy_positions sp
    JOIN strategies s ON s.id = sp.strategy_id
    WHERE sp.status = 'open' AND s.account_id IS NOT NULL
  `);

  const strategiesSnap: Record<string, { open_pnl: number; position_ids: string[] }> = {};
  const positionsSnap: Record<string, { mark_price: number; unrealized_pnl: number; liquidation_price: number | null }> = {};

  if (rows.length > 0) {
    // Pre-pass: collect all open position IDs per strategy before executor fanout
    for (const row of rows) {
      if (!strategiesSnap[row.strategy_id]) {
        strategiesSnap[row.strategy_id] = { open_pnl: 0, position_ids: [] };
      }
      strategiesSnap[row.strategy_id].position_ids.push(row.position_id);
    }

    // Fan out once per unique account — never per strategy or per position
    const accountIds = [...new Set(rows.map((r: any) => r.account_id as string))];
    const execMap = new Map<string, any>(); // key: `${accountId}:${symbol}:${side}`

    await Promise.all(accountIds.map(async (accountId) => {
      try {
        const resp = await fetch(`${EXECUTOR_URL}/accounts/${accountId}/positions`, {
          signal: AbortSignal.timeout(5000),
        });
        if (resp.ok) {
          const positions = await resp.json();
          if (Array.isArray(positions)) {
            positions.forEach((p: any) => {
              execMap.set(`${accountId}:${p.symbol}:${p.side}`, p);
            });
          }
        }
      } catch (e) {
        console.error(`[livePnl] executor failed for account ${accountId}:`, e);
      }
    }));

    console.log(`[livePnl] tick: ${rows.length} open position(s), ${accountIds.length} account(s) fanned out`);

    // Build per-position and per-strategy PnL; deduplicate executor keys per strategy
    const strategyKeysSeen = new Map<string, Set<string>>();
    for (const row of rows) {
      const execKey = `${row.account_id}:${row.symbol}:${row.side}`;
      const live = execMap.get(execKey);
      if (!live) continue;

      const markPrice = Number(live.mark_price || live.entry_price);
      const unrealizedPnl = Number(live.unrealized_pnl) || 0;
      const liquidationPrice = live.liquidation_price != null ? Number(live.liquidation_price) : null;
      positionsSnap[row.position_id] = { mark_price: markPrice, unrealized_pnl: unrealizedPnl, liquidation_price: liquidationPrice };

      if (!strategyKeysSeen.has(row.strategy_id)) {
        strategyKeysSeen.set(row.strategy_id, new Set());
      }
      // Count each executor position (account:symbol:side) only once per strategy
      const seen = strategyKeysSeen.get(row.strategy_id)!;
      if (!seen.has(execKey)) {
        seen.add(execKey);
        strategiesSnap[row.strategy_id].open_pnl += unrealizedPnl;
      }
    }
  }

  const snap: PnlSnapshot = { ts: Date.now(), strategies: strategiesSnap, positions: positionsSnap };
  const json = JSON.stringify(snap);
  await redis.set(SNAPSHOT_KEY, json);
  await redis.publish(SNAPSHOT_CHANNEL, json);
  _lastSnapshot = snap;
}

export async function startLivePnlTicker(): Promise<void> {
  console.log(`[livePnl] starting ticker, interval=${PNL_TICK_MS}ms`);
  try { await tick(); } catch (e) { console.error('[livePnl] initial tick failed:', e); }
  setInterval(() => tick().catch(e => console.error('[livePnl] tick failed:', e)), PNL_TICK_MS);
}
