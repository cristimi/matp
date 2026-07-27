/**
 * Layer A — engine-agnostic chart core.
 *
 * Nothing in this folder may import a charting library. These types and the pure
 * functions beside them describe *what* to draw in price/time space; translating
 * that into a specific engine's API is Layer B's job (src/charts/adapters/…).
 *
 * All timestamps are epoch milliseconds, matching the dashboard-api payload and
 * geometry_data's anchor_ts / first_swing_ts.
 */

export interface Candle {
  time:   number;   // bar open time, epoch ms
  open:   number;
  high:   number;
  low:    number;
  close:  number;
  volume: number;
}

/** The chart-replay half of ai_signal_log.geometry_data (see geometry.py). */
export interface GeometryData {
  shape:                   string;
  fit_quality:             string;
  upper_boundary:          number;
  lower_boundary:          number;
  upper_touches:           number;
  lower_touches:           number;
  convergence_pct_per_bar: number;
  pattern_age_bars:        number;
  position_in_range_pct:   number;

  // Optional: rows written before the Phase 0 geometry change carry none of
  // these, so every consumer must tolerate them being absent.
  upper_slope?:    number | null;
  lower_slope?:    number | null;
  anchor_ts?:      number | null;
  bar_seconds?:    number | null;
  first_swing_ts?: number | null;
  swing_highs?:    Array<[number, number]>;
  swing_lows?:     Array<[number, number]>;
}

/**
 * One price the order actually rested at, and from when.
 *
 * A resting limit order is amended in place, so the overlay's single
 * `entry_price` is only ever the LATEST one. Drawing it from `placed_at`
 * backdates today's level over yesterday's bars — a buy limit then appears to
 * have sat under the market for hours without filling. These steps carry the
 * real walk. `source: 'backfill'` means the two ends are real but the steps
 * between them were never recorded.
 */
export interface OverlayStep {
  at:     number;
  entry:  number | null;
  stop:   number | null;
  target: number | null;
  source: string;
}

export interface ChartOverlay {
  side:          string | null;
  status:        string | null;
  placed_at:     number | null;
  filled_at:     number | null;
  entry_price:   number | null;
  stop_price:    number | null;
  target_price:  number | null;
  current_price: number | null;
  closed_at:     number | null;
  close_price:   number | null;
  /** Oldest first. Absent or empty ⇒ draw one box, as before. */
  steps?:        OverlayStep[];
}

/** Response body of GET /positions/:id/candles and /orders/:id/candles. */
export interface ChartPayload {
  symbol:              string;
  exchange:            string;
  timeframe:           string | null;
  timeframe_requested: string | null;
  /** Ladder rungs that actually have candles — what the picker may offer. */
  available_timeframes: string[];
  bar_seconds:         number | null;
  candles:             Candle[];
  geometry:            GeometryData | null;
  geometry_at:         number | null;
  overlay:             ChartOverlay;
  note?:               string;
}

// ── Geometric output of the core (pure price/time, no pixels) ─────────────────

/** An axis-aligned rectangle in (time, price) space. */
export interface PriceTimeBox {
  from: number;   // epoch ms
  to:   number;   // epoch ms
  low:  number;
  high: number;
}

/**
 * One rung of the staircase: the levels the order held over one span of time.
 *
 * Drawn TradingView-style — a reward zone from entry to target and a risk zone
 * from entry to stop, rather than one box spanning stop to target.
 */
export interface RiskRewardSegment {
  from:   number;
  to:     number;
  entry:  number;
  stop:   number | null;
  target: number | null;
}

export interface RiskRewardModel {
  direction: 'long' | 'short';

  /**
   * The levels over time. One entry when the order was never amended (or has no
   * recorded history), one per recorded price otherwise. Always non-empty.
   */
  segments: RiskRewardSegment[];
  /** True when the segments came from recorded history rather than one snapshot. */
  stepped: boolean;
  /**
   * True when some segment was reconstructed by the migration rather than
   * recorded live — the ends are real, the walk between them is not.
   */
  reconstructed: boolean;

  /** Bounding box over every segment. Used for culling and for the fill span. */
  outer: PriceTimeBox;
  /** Entry → current price, from the bar the order filled. Null while unfilled. */
  inner: PriceTimeBox | null;

  entryPrice:   number;
  stopPrice:    number | null;
  targetPrice:  number | null;
  currentPrice: number;

  /** |entry − stop| as % of entry. Null without a stop. */
  riskPct:     number | null;
  /** |target − entry| as % of entry. Null without a target. */
  rewardPct:   number | null;
  /** rewardPct / riskPct. Null when either leg is missing or risk is zero. */
  riskReward:  number | null;

  /** 0-100, how far price has travelled from entry toward target. */
  progressPct: number | null;
  /** 0-100, how far price has travelled from entry toward stop. */
  towardStopPct: number | null;

  /** Signed move from entry in the position's favour, as % of entry. */
  pnlPct:   number;
  inProfit: boolean;
}

/** A boundary line to draw, as two or more points in (time, price). */
export interface GeometryLine {
  id:     'upper' | 'lower';
  points: Array<{ time: number; price: number }>;
}

export interface GeometryModel {
  lines:  GeometryLine[];
  swings: Array<{ id: 'high' | 'low'; time: number; price: number }>;
  shape:       string;
  fitQuality:  string;
}

// ── Adapter contract (implemented in Layer B) ────────────────────────────────

export interface ChartMountOptions {
  candles:      Candle[];
  riskReward:   RiskRewardModel | null;
  geometry:     GeometryModel | null;
  /** Decimal places for price labels; caller derives it from the symbol. */
  priceDecimals: number;
  /** Chart height in CSS pixels. */
  height:       number;
}

/** Opaque handle to a mounted chart. Only the adapter knows what is inside. */
export interface ChartHandle {
  /** Re-render with new data without tearing the chart down. */
  update(options: ChartMountOptions): void;
  /** Re-measure after the container resizes. */
  resize(): void;
  /** Release every resource. Must be safe to call twice. */
  destroy(): void;
}

export interface ChartAdapter {
  /** Human-readable engine name, for diagnostics. */
  readonly name: string;
  mount(container: HTMLElement, options: ChartMountOptions): ChartHandle;
}
