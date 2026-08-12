/**
 * Layer A tests. These must pass with no chart library installed — nothing here,
 * and nothing it imports, may reach for lightweight-charts or any engine.
 */
import { describe, it, expect } from 'vitest';

import { computeRiskReward, snapToBar, snapToSeries } from '../riskReward';
import { computeGeometryModel } from '../geometryLines';
import type { Candle, ChartOverlay, GeometryData } from '../types';

const HOUR = 3_600_000;
const T0   = 1_753_000_000_000 - (1_753_000_000_000 % HOUR); // bar-aligned base

function candles(n: number, closeAt: (i: number) => number): Candle[] {
  return Array.from({ length: n }, (_, i) => {
    const c = closeAt(i);
    return { time: T0 + i * HOUR, open: c, high: c + 1, low: c - 1, close: c, volume: 10 };
  });
}

function overlay(patch: Partial<ChartOverlay> = {}): ChartOverlay {
  return {
    side:          'long',
    status:        'open',
    placed_at:     T0 + 10 * HOUR,
    filled_at:     T0 + 12 * HOUR,
    entry_price:   100,
    stop_price:    90,
    target_price:  130,
    current_price: 110,
    closed_at:     null,
    close_price:   null,
    ...patch,
  };
}

// ── the amend staircase ──────────────────────────────────────────────────────

describe('computeRiskReward — amended orders', () => {
  const series = candles(24, () => 105);

  it('draws one segment when the order has no recorded history', () => {
    const m = computeRiskReward({ overlay: overlay(), candles: series, barSeconds: 3600 })!;
    expect(m.stepped).toBe(false);
    expect(m.reconstructed).toBe(false);
    expect(m.segments).toHaveLength(1);
    expect(m.segments[0].entry).toBe(100);
    expect(m.segments[0].from).toBe(T0 + 10 * HOUR);
    expect(m.segments[0].to).toBe(series[series.length - 1].time);
  });

  it('gives each recorded price its own span, ending where the next begins', () => {
    const m = computeRiskReward({
      overlay: overlay({
        entry_price: 104, stop_price: 94, target_price: 134,
        steps: [
          { at: T0 + 10 * HOUR, entry: 100, stop: 90, target: 130, source: 'placement' },
          { at: T0 + 14 * HOUR, entry: 102, stop: 92, target: 132, source: 'amend' },
          { at: T0 + 18 * HOUR, entry: 104, stop: 94, target: 134, source: 'amend' },
        ],
      }),
      candles: series,
      barSeconds: 3600,
    })!;

    expect(m.stepped).toBe(true);
    expect(m.segments).toHaveLength(3);
    expect(m.segments[0]).toMatchObject({ from: T0 + 10 * HOUR, to: T0 + 14 * HOUR, entry: 100 });
    expect(m.segments[1]).toMatchObject({ from: T0 + 14 * HOUR, to: T0 + 18 * HOUR, entry: 102 });
    expect(m.segments[2].entry).toBe(104);
    expect(m.segments[2].to).toBe(series[series.length - 1].time);
  });

  it('steps the stop and target too, not just the entry', () => {
    const m = computeRiskReward({
      overlay: overlay({
        steps: [
          { at: T0 + 10 * HOUR, entry: 100, stop: 90, target: 130, source: 'placement' },
          { at: T0 + 16 * HOUR, entry: 100, stop: 80, target: 150, source: 'amend' },
        ],
      }),
      candles: series,
      barSeconds: 3600,
    })!;
    expect(m.segments[0]).toMatchObject({ stop: 90, target: 130 });
    expect(m.segments[1]).toMatchObject({ stop: 80, target: 150 });
    // The bounding box has to cover every rung, not just the newest.
    expect(m.outer.low).toBe(80);
    expect(m.outer.high).toBe(150);
  });

  it('flags a reconstructed history so the UI can say the walk is incomplete', () => {
    const m = computeRiskReward({
      overlay: overlay({
        steps: [
          { at: T0 + 10 * HOUR, entry: 100, stop: 90, target: 130, source: 'backfill' },
          { at: T0 + 20 * HOUR, entry: 108, stop: 98, target: 138, source: 'backfill' },
        ],
      }),
      candles: series,
      barSeconds: 3600,
    })!;
    expect(m.stepped).toBe(true);
    expect(m.reconstructed).toBe(true);
  });

  it('sorts out-of-order steps and ignores ones with no price', () => {
    const m = computeRiskReward({
      overlay: overlay({
        steps: [
          { at: T0 + 18 * HOUR, entry: 104, stop: 94, target: 134, source: 'amend' },
          { at: T0 + 10 * HOUR, entry: 100, stop: 90, target: 130, source: 'placement' },
          { at: T0 + 12 * HOUR, entry: null, stop: 92, target: 132, source: 'amend' },
        ],
      }),
      candles: series,
      barSeconds: 3600,
    })!;
    expect(m.segments.map(s => s.entry)).toEqual([100, 104]);
  });

  it('collapses steps that fall outside the charted window into one rung', () => {
    // Every step predates the series, so they all snap onto the first bar and
    // would otherwise draw as a stack of zero-width rungs.
    const m = computeRiskReward({
      overlay: overlay({
        placed_at: T0 - 50 * HOUR,
        steps: [
          { at: T0 - 50 * HOUR, entry: 100, stop: 90, target: 130, source: 'placement' },
          { at: T0 - 40 * HOUR, entry: 101, stop: 91, target: 131, source: 'amend' },
          { at: T0 - 30 * HOUR, entry: 102, stop: 92, target: 132, source: 'amend' },
        ],
      }),
      candles: series,
      barSeconds: 3600,
    })!;
    expect(m.segments).toHaveLength(1);
    expect(m.segments[0].entry).toBe(102);   // the last one still standing
    expect(m.segments[0].to).toBe(series[series.length - 1].time);
  });

  it('keeps the risk/reward numbers on the newest levels, not the first', () => {
    const m = computeRiskReward({
      overlay: overlay({
        entry_price: 200, stop_price: 180, target_price: 260,
        steps: [
          { at: T0 + 10 * HOUR, entry: 100, stop: 90, target: 130, source: 'placement' },
          { at: T0 + 16 * HOUR, entry: 200, stop: 180, target: 260, source: 'amend' },
        ],
      }),
      candles: series,
      barSeconds: 3600,
    })!;
    expect(m.entryPrice).toBe(200);
    expect(m.riskPct).toBeCloseTo(10, 6);    // 200 → 180
    expect(m.rewardPct).toBeCloseTo(30, 6);  // 200 → 260
  });
});

// ── snapToBar ────────────────────────────────────────────────────────────────

describe('snapToBar', () => {
  it('snaps down to the containing bar open', () => {
    expect(snapToBar(T0 + HOUR + 1234, 3600)).toBe(T0 + HOUR);
  });

  it('is a no-op without a bar duration', () => {
    const t = T0 + 1234;
    expect(snapToBar(t, null)).toBe(t);
    expect(snapToBar(t, 0)).toBe(t);
  });
});

describe('snapToSeries', () => {
  const series = candles(10, () => 100);

  it('snaps to the last bar at or before the timestamp', () => {
    expect(snapToSeries(T0 + 3 * HOUR + 59 * 60_000, series)).toBe(T0 + 3 * HOUR);
    expect(snapToSeries(T0 + 3 * HOUR, series)).toBe(T0 + 3 * HOUR);
  });

  it('clamps to the series ends', () => {
    expect(snapToSeries(T0 - 99 * HOUR, series)).toBe(T0);
    expect(snapToSeries(T0 + 999 * HOUR, series)).toBe(T0 + 9 * HOUR);
  });

  it('passes the value through when there is no series', () => {
    expect(snapToSeries(1234, [])).toBe(1234);
  });

  it('handles a gapped series without inventing a missing bar', () => {
    const gapped = [series[0], series[1], series[5], series[6]];
    // 3h falls in the gap → snaps back to the last real bar, 1h
    expect(snapToSeries(T0 + 3 * HOUR, gapped)).toBe(T0 + HOUR);
  });
});

// ── computeRiskReward ────────────────────────────────────────────────────────

describe('computeRiskReward', () => {
  const series = candles(30, () => 110);

  it('builds the outer box from stop to target, starting at the placed bar', () => {
    const m = computeRiskReward({ overlay: overlay(), candles: series, barSeconds: 3600 })!;

    expect(m.outer.low).toBe(90);
    expect(m.outer.high).toBe(130);
    expect(m.outer.from).toBe(T0 + 10 * HOUR);
    expect(m.outer.to).toBe(series[series.length - 1].time);
  });

  it('builds the inner progress box from the fill bar, entry to current price', () => {
    const m = computeRiskReward({ overlay: overlay(), candles: series, barSeconds: 3600 })!;

    expect(m.inner).not.toBeNull();
    expect(m.inner!.from).toBe(T0 + 12 * HOUR);
    expect(m.inner!.low).toBe(100);    // entry
    expect(m.inner!.high).toBe(110);   // current
  });

  it('inner box tracks price against the position when it moves to a loss', () => {
    const losing = candles(30, () => 95);
    const m = computeRiskReward({
      overlay: overlay({ current_price: 95 }), candles: losing, barSeconds: 3600,
    })!;

    expect(m.inner!.low).toBe(95);
    expect(m.inner!.high).toBe(100);
    expect(m.inProfit).toBe(false);
    expect(m.pnlPct).toBeCloseTo(-5, 6);
  });

  it('has no inner box while the order is unfilled', () => {
    const m = computeRiskReward({
      overlay: overlay({ filled_at: null, status: 'pending' }),
      candles: series,
      barSeconds: 3600,
    })!;

    expect(m.inner).toBeNull();
    expect(m.outer.low).toBe(90);
  });

  it('derives risk, reward and the R:R ratio', () => {
    const m = computeRiskReward({ overlay: overlay(), candles: series, barSeconds: 3600 })!;

    expect(m.riskPct).toBeCloseTo(10, 6);    // 100 → 90
    expect(m.rewardPct).toBeCloseTo(30, 6);  // 100 → 130
    expect(m.riskReward).toBeCloseTo(3, 6);
  });

  it('reports progress toward target and nothing toward stop while winning', () => {
    const m = computeRiskReward({ overlay: overlay(), candles: series, barSeconds: 3600 })!;

    // 100 → 110 of a 100 → 130 run
    expect(m.progressPct).toBeCloseTo(100 / 3, 4);
    expect(m.towardStopPct).toBe(0);
  });

  it('reports progress toward stop and nothing toward target while losing', () => {
    const m = computeRiskReward({
      overlay: overlay({ current_price: 95 }), candles: series, barSeconds: 3600,
    })!;

    expect(m.towardStopPct).toBeCloseTo(50, 6);   // half way from 100 down to 90
    expect(m.progressPct).toBe(0);
  });

  it('flips the sign of pnlPct for a short', () => {
    const m = computeRiskReward({
      overlay: overlay({ side: 'short', stop_price: 110, target_price: 70, current_price: 90 }),
      candles: series,
      barSeconds: 3600,
    })!;

    expect(m.direction).toBe('short');
    expect(m.pnlPct).toBeCloseTo(10, 6);   // price fell 10% — good for a short
    expect(m.inProfit).toBe(true);
    expect(m.progressPct).toBeCloseTo(100 / 3, 4);
  });

  it('infers direction from the target when the side word is unknown', () => {
    const m = computeRiskReward({
      overlay: overlay({ side: 'weird' }), candles: series, barSeconds: 3600,
    })!;
    expect(m.direction).toBe('long');
  });

  it('freezes a closed position at its close price instead of following the market', () => {
    const m = computeRiskReward({
      overlay: overlay({
        closed_at:   T0 + 20 * HOUR,
        close_price: 125,
        status:      'closed',
      }),
      candles: series,
      barSeconds: 3600,
    })!;

    expect(m.currentPrice).toBe(125);
    expect(m.outer.to).toBe(T0 + 20 * HOUR);
    expect(m.inner!.high).toBe(125);
  });

  it('clamps a box that starts before the retained candles', () => {
    const m = computeRiskReward({
      overlay: overlay({ placed_at: T0 - 500 * HOUR, filled_at: T0 - 400 * HOUR }),
      candles: series,
      barSeconds: 3600,
    })!;

    expect(m.outer.from).toBe(series[0].time);
    expect(m.inner!.from).toBe(series[0].time);
  });

  it('snaps box edges onto real bars when the row time is mid-bar', () => {
    // A fill recorded 37 minutes into the bar must not create a phantom time slot.
    const m = computeRiskReward({
      overlay: overlay({
        placed_at: T0 + 10 * HOUR + 37 * 60_000,
        filled_at: T0 + 12 * HOUR + 15 * 60_000,
      }),
      candles: series,
      barSeconds: 3600,
    })!;

    const times = series.map(c => c.time);
    expect(times).toContain(m.outer.from);
    expect(times).toContain(m.outer.to);
    expect(times).toContain(m.inner!.from);
    expect(m.outer.from).toBe(T0 + 10 * HOUR);
    expect(m.inner!.from).toBe(T0 + 12 * HOUR);
  });

  it('works with only a stop, or only a target', () => {
    const stopOnly = computeRiskReward({
      overlay: overlay({ target_price: null }), candles: series, barSeconds: 3600,
    })!;
    expect(stopOnly.rewardPct).toBeNull();
    expect(stopOnly.riskReward).toBeNull();
    expect(stopOnly.progressPct).toBeNull();
    expect(stopOnly.outer.low).toBe(90);
    expect(stopOnly.outer.high).toBe(100);

    const targetOnly = computeRiskReward({
      overlay: overlay({ stop_price: null }), candles: series, barSeconds: 3600,
    })!;
    expect(targetOnly.riskPct).toBeNull();
    expect(targetOnly.towardStopPct).toBeNull();
    expect(targetOnly.outer.high).toBe(130);
  });

  it('returns null when there is nothing to draw', () => {
    const noEntry  = overlay({ entry_price: null });
    const noLevels = overlay({ stop_price: null, target_price: null });

    expect(computeRiskReward({ overlay: noEntry,  candles: series, barSeconds: 3600 })).toBeNull();
    expect(computeRiskReward({ overlay: noLevels, candles: series, barSeconds: 3600 })).toBeNull();
  });
});

// ── computeGeometryModel ─────────────────────────────────────────────────────

function geometry(patch: Partial<GeometryData> = {}): GeometryData {
  return {
    shape:                   'ascending_channel',
    fit_quality:             'strong',
    upper_boundary:          130,
    lower_boundary:          110,
    upper_touches:           4,
    lower_touches:           4,
    convergence_pct_per_bar: 0,
    pattern_age_bars:        20,
    position_in_range_pct:   50,
    upper_slope:             0.5,
    lower_slope:             0.5,
    anchor_ts:               T0,
    bar_seconds:             3600,
    first_swing_ts:          T0 + 5 * HOUR,
    swing_highs:             [[T0 + 5 * HOUR, 117.5], [T0 + 15 * HOUR, 122.5]],
    swing_lows:              [[T0 + 8 * HOUR, 99.0]],
    ...patch,
  };
}

describe('computeGeometryModel', () => {
  const series = candles(30, () => 120);
  const lastTime = series[series.length - 1].time;

  it('projects a sloped boundary back to the first swing', () => {
    const m = computeGeometryModel({ geometry: geometry(), candles: series, barSeconds: 3600 })!;
    const upper = m.lines.find(l => l.id === 'upper')!;

    // 24 bars back from the series end at 0.5/bar → 130 − 12
    expect(upper.points[0].time).toBe(T0 + 5 * HOUR);
    expect(upper.points[0].price).toBeCloseTo(130 - 0.5 * 24, 6);
    expect(upper.points[1].time).toBe(lastTime);
    expect(upper.points[1].price).toBe(130);
  });

  it('draws flat lines for legacy rows that carry no slope', () => {
    const m = computeGeometryModel({
      geometry: geometry({ upper_slope: null, lower_slope: null }),
      candles: series,
      barSeconds: 3600,
    })!;
    const upper = m.lines.find(l => l.id === 'upper')!;

    expect(upper.points[0].price).toBe(130);
    expect(upper.points[1].price).toBe(130);
  });

  it('handles a pre-Phase-0 row where the new keys are absent entirely', () => {
    // Exactly the shape ai_signal_log rows had before the geometry change.
    const legacy = {
      shape: 'descending_channel', fit_quality: 'moderate',
      upper_boundary: 130, lower_boundary: 110,
      upper_touches: 3, lower_touches: 4,
      convergence_pct_per_bar: 0.0007, pattern_age_bars: 38,
      position_in_range_pct: 100,
    };

    const m = computeGeometryModel({ geometry: legacy, candles: series, barSeconds: 3600 })!;

    expect(m.lines).toHaveLength(2);
    expect(m.swings).toEqual([]);
    expect(m.lines[0].points[0].time).toBe(series[0].time);
    expect(m.lines[0].points[0].price).toBe(130);   // flat, no slope to project
  });

  it('keeps only the swings inside the candle window', () => {
    const m = computeGeometryModel({
      geometry: geometry({
        swing_highs: [[T0 - 50 * HOUR, 80], [T0 + 15 * HOUR, 122.5]],
        swing_lows:  [[T0 + 8 * HOUR, 99]],
      }),
      candles: series,
      barSeconds: 3600,
    })!;

    expect(m.swings).toHaveLength(2);
    expect(m.swings.map(s => s.time)).toEqual([T0 + 15 * HOUR, T0 + 8 * HOUR]);
  });

  it('snaps line endpoints and swings onto real bars', () => {
    // geometry computed on 15m bars while the chart shows 1h: raw timestamps land
    // between bars and would otherwise insert phantom slots.
    const m = computeGeometryModel({
      geometry: geometry({
        first_swing_ts: T0 + 5 * HOUR + 45 * 60_000,
        swing_highs:    [[T0 + 15 * HOUR + 30 * 60_000, 122.5]],
        swing_lows:     [[T0 + 8 * HOUR + 15 * 60_000, 99.0]],
      }),
      candles: series,
      barSeconds: 3600,
    })!;

    const times = series.map(c => c.time);
    for (const line of m.lines) {
      for (const p of line.points) expect(times).toContain(p.time);
    }
    expect(m.swings.map(s => s.time)).toEqual([T0 + 15 * HOUR, T0 + 8 * HOUR]);
  });

  it('falls back to the series start when the fit anchors on the last bar', () => {
    const m = computeGeometryModel({
      geometry: geometry({ first_swing_ts: series[series.length - 1].time }),
      candles: series,
      barSeconds: 3600,
    })!;

    const upper = m.lines.find(l => l.id === 'upper')!;
    expect(upper.points[0].time).toBe(series[0].time);
    expect(upper.points[0].time).toBeLessThan(upper.points[1].time);
  });

  it('falls back to the payload bar duration when geometry has none', () => {
    const m = computeGeometryModel({
      geometry: geometry({ bar_seconds: null }),
      candles: series,
      barSeconds: 3600,
    })!;
    expect(m.lines).toHaveLength(2);
  });

  it('returns null for a no-pattern read, no geometry, or no candles', () => {
    expect(computeGeometryModel({
      geometry: geometry({ upper_boundary: 0, lower_boundary: 0 }),
      candles: series, barSeconds: 3600,
    })).toBeNull();

    expect(computeGeometryModel({ geometry: null, candles: series, barSeconds: 3600 })).toBeNull();
    expect(computeGeometryModel({ geometry: geometry(), candles: [], barSeconds: 3600 })).toBeNull();
    expect(computeGeometryModel({
      geometry: geometry({ bar_seconds: null }), candles: series, barSeconds: null,
    })).toBeNull();
  });

  it('refuses to draw a shape:no_pattern read even when it carries boundaries', () => {
    // A no-pattern verdict still ships the two regression boundaries it computed on
    // the way to that verdict. Drawing them showed ai-btc-6f8c an inverted "range".
    expect(computeGeometryModel({
      geometry: geometry({ shape: 'no_pattern' }),
      candles: series, barSeconds: 3600,
    })).toBeNull();
  });

  it('pins the boundary where the read was taken, not on the newest bar', () => {
    // Read taken 10 bars before the series end. The boundary value belongs THERE, so
    // the line must continue past it at the fitted slope instead of being dragged
    // back so the value lands on the last bar. Getting this wrong slid a 10-day-old
    // fit 1648 points away from the bars it was measured on.
    const takenAt = series[series.length - 11].time;
    const m = computeGeometryModel({
      geometry:   geometry(),
      candles:    series,
      barSeconds: 3600,
      geometryAt: takenAt,
    })!;
    const upper = m.lines.find(l => l.id === 'upper')!;

    // start = first swing at bar 5; the read sits on bar 19 → 14 bars back at 0.5/bar
    expect(upper.points[0].price).toBeCloseTo(130 - 0.5 * 14, 6);
    // end = series end at bar 29, ten bars AFTER the read, so the line carries on
    expect(upper.points[1].time).toBe(lastTime);
    expect(upper.points[1].price).toBeCloseTo(130 + 0.5 * 10, 6);
  });

  it('still anchors on the newest bar when the payload carries no geometry_at', () => {
    const m = computeGeometryModel({
      geometry: geometry(), candles: series, barSeconds: 3600, geometryAt: null,
    })!;
    const upper = m.lines.find(l => l.id === 'upper')!;
    expect(upper.points[1].price).toBe(130);
  });
});
