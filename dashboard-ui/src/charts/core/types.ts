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
}

/** Response body of GET /positions/:id/candles and /orders/:id/candles. */
export interface ChartPayload {
  symbol:              string;
  exchange:            string;
  timeframe:           string | null;
  timeframe_requested: string | null;
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

export interface RiskRewardModel {
  direction: 'long' | 'short';

  /** Full stop → target span, from the bar the order was placed to the end. */
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
