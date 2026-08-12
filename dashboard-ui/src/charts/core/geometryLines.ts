/**
 * Layer A — turn ai_signal_log.geometry_data into drawable boundary lines.
 *
 * geometry.py fits each boundary as `price = slope * barIndex + intercept`, where
 * barIndex counts from anchor_ts, and reports the value at the final bar as
 * upper_boundary / lower_boundary. Projecting back to any time t is therefore:
 *
 *   price(t) = boundary + slope * (barsBetween(anchorEndTime, t))
 *
 * The line is drawn from first_swing_ts (the oldest swing in the fit) to the end
 * of the candle series. Before Phase 0 shipped the slope fields, geometry_data
 * carried only the two end-point values — those older rows degrade to flat lines,
 * which is why the slope is treated as optional here rather than required.
 */
import type { Candle, GeometryData, GeometryModel, GeometryLine } from './types';
import { snapToSeries } from './riskReward';

/** Bars between two timestamps, using the geometry's own bar duration. */
function barsBetween(fromMs: number, toMs: number, barSeconds: number): number {
  return (toMs - fromMs) / (barSeconds * 1000);
}

export interface GeometryLinesInput {
  geometry:   GeometryData | null;
  candles:    Candle[];
  /** Falls back to this when geometry_data has no bar_seconds of its own. */
  barSeconds: number | null;
  /**
   * When the read was taken (payload `geometry_at`). The boundary values belong to
   * the last bar of the window the fit analysed, which ends here — so this is where
   * they are pinned before the slope projects them across the chart. Assuming the
   * newest bar on screen instead silently slides an older read's whole range: a
   * 2026-08-02 fit drawn over 2026-08-12 bars was carried 1648 points away from
   * where it was measured and ended up below every candle.
   */
  geometryAt?: number | null;
}

export function computeGeometryModel({
  geometry,
  candles,
  barSeconds,
  geometryAt,
}: GeometryLinesInput): GeometryModel | null {
  if (!geometry || !candles.length) return null;

  // "No pattern" is a verdict: the fit found no range. The read still carries the
  // two regression boundaries it computed on the way to that verdict, and drawing
  // them presents a non-finding as a finding — ai-btc-6f8c showed an inverted
  // "range" whose upper line started below its lower one. Trust the verdict.
  if (geometry.shape === 'no_pattern') return null;

  const { upper_boundary, lower_boundary } = geometry;
  if (!Number.isFinite(upper_boundary) || !Number.isFinite(lower_boundary)) return null;
  // An empty read reports both boundaries as 0 — nothing to draw.
  if (upper_boundary === 0 && lower_boundary === 0) return null;

  const bars = geometry.bar_seconds ?? barSeconds;
  if (!bars || bars <= 0) return null;

  const seriesStart = candles[0].time;
  const seriesEnd   = candles[candles.length - 1].time;

  // The fit's own end. anchor_ts + pattern span is not recorded, so the boundary
  // values are treated as belonging to the bar the read was taken on. The series end
  // is only the fallback for a payload with no geometry_at; for a fresh read the two
  // are the same bar anyway.
  const boundaryAt = geometryAt ?? seriesEnd;

  // geometry_data is computed on the strategy's cycle interval, which need not be
  // the timeframe being charted, so its timestamps are snapped onto real bars of
  // this series. A two-point line also needs two *distinct* times: if the snap
  // collapses the start onto the last bar, fall back to the series start.
  const startRaw  = geometry.first_swing_ts ?? geometry.anchor_ts ?? seriesStart;
  const snapped   = snapToSeries(startRaw, candles);
  const start     = snapped < seriesEnd ? snapped : seriesStart;

  if (start >= seriesEnd) return null;   // single-bar series — nothing to draw

  // Both endpoints are projected from where the boundary was measured, not just the
  // start: a read taken before the newest bar has to keep sloping across the bars
  // that came after it. Pinning the end to the raw boundary value while sloping the
  // start away from it would bend the line to a value the fit never reported.
  // Identical to the old behaviour whenever boundaryAt is the last bar.
  const build = (
    id: 'upper' | 'lower',
    boundaryValue: number,
    slope: number | null | undefined,
  ): GeometryLine => {
    const s     = slope ?? 0;
    const at    = (t: number) => boundaryValue + s * barsBetween(boundaryAt, t, bars);
    return {
      id,
      points: [
        { time: start,     price: at(start) },
        { time: seriesEnd, price: at(seriesEnd) },
      ],
    };
  };

  const lines: GeometryLine[] = [
    build('upper', upper_boundary, geometry.upper_slope),
    build('lower', lower_boundary, geometry.lower_slope),
  ];

  // Swings outside the charted window are dropped; the survivors are snapped onto
  // real bars for the same reason the line endpoints are.
  const inWindow = (ts: number) => ts >= seriesStart && ts <= seriesEnd;

  const swings = [
    ...(geometry.swing_highs || [])
      .filter(([ts]) => inWindow(ts))
      .map(([time, price]) => ({ id: 'high' as const, time: snapToSeries(time, candles), price })),
    ...(geometry.swing_lows || [])
      .filter(([ts]) => inWindow(ts))
      .map(([time, price]) => ({ id: 'low' as const, time: snapToSeries(time, candles), price })),
  ];

  return {
    lines,
    swings,
    shape:      geometry.shape,
    fitQuality: geometry.fit_quality,
  };
}
