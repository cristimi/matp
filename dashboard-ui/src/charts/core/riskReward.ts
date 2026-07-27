/**
 * Layer A — the risk/reward zones, computed in pure price/time space.
 *
 * Shaped like TradingView's Long/Short Position tool: a reward zone from entry
 * to target and a risk zone from entry to stop, rather than one box spanning
 * stop to target.
 *
 *   segments  one rung per price the order actually rested at. A resting limit
 *             order is amended in place, so a single entry price would be the
 *             latest one drawn back over bars it was never live for; the rungs
 *             put each level over its own span. Orders with no recorded history
 *             produce exactly one rung and look as they always did.
 *   outer     bounding box over every rung — culling, and the fill span.
 *   inner     the progress box: starts at the bar the order *filled*, height
 *             tracks entry → current price.
 *
 * No pixels, no DOM, no chart library. See types.ts for the contract.
 */
import type {
  Candle, ChartOverlay, RiskRewardModel, RiskRewardSegment, PriceTimeBox,
} from './types';

const LONG_WORDS  = ['long', 'buy', 'open_long', 'bid'];
const SHORT_WORDS = ['short', 'sell', 'open_short', 'ask'];

/** Snap a timestamp down to the open of the bar containing it. */
export function snapToBar(tsMs: number, barSeconds: number | null): number {
  if (!barSeconds || barSeconds <= 0) return tsMs;
  const barMs = barSeconds * 1000;
  return Math.floor(tsMs / barMs) * barMs;
}

/**
 * Snap a timestamp onto an actual bar of the series — the last bar at or before
 * it, clamped to the series ends.
 *
 * Arithmetic snapping alone is not enough: geometry_data is computed on the
 * strategy's cycle interval, which need not match the timeframe being charted,
 * so a raw geometry timestamp can land between two bars. A chart engine given a
 * time that is not on its scale either inserts a phantom slot (shifting every
 * bar) or fails to resolve a coordinate at all.
 */
export function snapToSeries(tsMs: number, candles: Candle[]): number {
  if (!candles.length) return tsMs;
  if (tsMs <= candles[0].time) return candles[0].time;
  if (tsMs >= candles[candles.length - 1].time) return candles[candles.length - 1].time;

  let lo = 0;
  let hi = candles.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (candles[mid].time <= tsMs) lo = mid;
    else hi = mid - 1;
  }
  return candles[lo].time;
}

function resolveDirection(
  side: string | null,
  entry: number,
  target: number | null,
  stop: number | null,
): 'long' | 'short' {
  const s = (side || '').toLowerCase();
  if (LONG_WORDS.some(w => s.includes(w)))  return 'long';
  if (SHORT_WORDS.some(w => s.includes(w))) return 'short';
  // No usable side word — infer from where the target (or stop) sits.
  if (target != null) return target >= entry ? 'long' : 'short';
  if (stop   != null) return stop   <= entry ? 'long' : 'short';
  return 'long';
}

/** Percentage of the entry→destination distance already travelled, clamped 0-100. */
function travelled(entry: number, destination: number, current: number): number | null {
  const span = destination - entry;
  if (!Number.isFinite(span) || span === 0) return null;
  const pct = ((current - entry) / span) * 100;
  return Math.max(0, Math.min(100, pct));
}

export interface RiskRewardInput {
  overlay:     ChartOverlay;
  candles:     Candle[];
  barSeconds:  number | null;
}

/**
 * Build the box model, or null when there is not enough to draw one.
 *
 * Requires an entry price plus at least one of stop / target — with neither there
 * is no risk-reward to show, only a bare entry line, which the adapter draws from
 * the overlay directly.
 */
export function computeRiskReward({
  overlay,
  candles,
  barSeconds,
}: RiskRewardInput): RiskRewardModel | null {
  const entry  = overlay.entry_price;
  const stop   = overlay.stop_price;
  const target = overlay.target_price;

  if (entry == null || !Number.isFinite(entry) || entry <= 0) return null;
  if (stop == null && target == null) return null;

  const lastCandle = candles.length ? candles[candles.length - 1] : null;

  // Current price: the close the chart itself ends on, so box and candles agree.
  // A closed position freezes at its close price instead of following the market.
  const current =
    overlay.closed_at != null && overlay.close_price != null
      ? overlay.close_price
      : (overlay.current_price ?? lastCandle?.close ?? entry);

  if (!Number.isFinite(current)) return null;

  const direction = resolveDirection(overlay.side, entry, target, stop);

  // ── Time bounds ────────────────────────────────────────────────────────────
  // Every edge lands on a real bar of the series (snapToSeries also clamps into
  // range, so an order older than the retained candles anchors at the left edge
  // instead of off-screen). With no candles at all, fall back to bar arithmetic.
  const firstCandleTime = candles.length ? candles[0].time : null;
  const lastCandleTime  = lastCandle ? lastCandle.time : null;

  const onGrid = (ts: number) =>
    candles.length ? snapToSeries(ts, candles) : snapToBar(ts, barSeconds);

  const placedRaw = overlay.placed_at ?? overlay.filled_at ?? firstCandleTime;
  if (placedRaw == null) return null;

  const placed = onGrid(placedRaw);
  const endRaw = overlay.closed_at ?? lastCandleTime ?? placed;
  const end    = Math.max(onGrid(endRaw), placed);

  // ── Segments: the staircase ────────────────────────────────────────────────
  // Each recorded price runs until the next one replaces it, so the levels sit
  // over the bars they were actually live for. Steps outside the charted window
  // collapse onto its edges (snapToSeries clamps), and the collapsed duplicates
  // are dropped so a long-lived order zoomed to its last hour draws one rung
  // rather than a dozen zero-width ones.
  const rawSteps = (overlay.steps ?? [])
    .filter(s => s.entry != null && Number.isFinite(s.entry) && Number.isFinite(s.at))
    .sort((a, b) => a.at - b.at);

  const stepped = rawSteps.length > 0;
  const reconstructed = rawSteps.some(s => s.source === 'backfill');

  let segments: RiskRewardSegment[];
  if (stepped) {
    const built: RiskRewardSegment[] = [];
    rawSteps.forEach((s, i) => {
      const from = Math.max(onGrid(s.at), placed);
      const to   = i + 1 < rawSteps.length
        ? Math.max(onGrid(rawSteps[i + 1].at), from)
        : end;
      built.push({
        from,
        to: Math.max(to, from),
        entry:  s.entry as number,
        stop:   s.stop   ?? null,
        target: s.target ?? null,
      });
    });
    // Keep only rungs with real width, plus always the last one so a fully
    // collapsed history still draws something.
    segments = built.filter((s, i) => s.to > s.from || i === built.length - 1);
    if (!segments.length) segments = [built[built.length - 1]];
  } else {
    segments = [{ from: placed, to: end, entry, stop, target }];
  }

  const levels = segments.flatMap(s =>
    [s.entry, s.stop, s.target].filter((v): v is number => v != null));

  const outer: PriceTimeBox = {
    from: segments[0].from,
    to:   segments[segments.length - 1].to,
    low:  Math.min(...levels),
    high: Math.max(...levels),
  };

  // ── Inner progress box ─────────────────────────────────────────────────────
  let inner: PriceTimeBox | null = null;
  if (overlay.filled_at != null) {
    const filled = Math.min(Math.max(onGrid(overlay.filled_at), placed), end);
    inner = {
      from: filled,
      to:   end,
      low:  Math.min(entry, current),
      high: Math.max(entry, current),
    };
  }

  // ── Derived percentages ────────────────────────────────────────────────────
  const riskPct   = stop   != null ? (Math.abs(entry - stop)   / entry) * 100 : null;
  const rewardPct = target != null ? (Math.abs(target - entry) / entry) * 100 : null;
  const riskReward =
    riskPct != null && rewardPct != null && riskPct > 0 ? rewardPct / riskPct : null;

  const progressPct   = target != null ? travelled(entry, target, current) : null;
  const towardStopPct = stop   != null ? travelled(entry, stop,   current) : null;

  const rawMovePct = ((current - entry) / entry) * 100;
  const pnlPct     = direction === 'long' ? rawMovePct : -rawMovePct;

  return {
    direction,
    segments,
    stepped,
    reconstructed,
    outer,
    inner,
    entryPrice:   entry,
    stopPrice:    stop,
    targetPrice:  target,
    currentPrice: current,
    riskPct,
    rewardPct,
    riskReward,
    progressPct,
    towardStopPct,
    pnlPct,
    inProfit: pnlPct > 0,
  };
}
