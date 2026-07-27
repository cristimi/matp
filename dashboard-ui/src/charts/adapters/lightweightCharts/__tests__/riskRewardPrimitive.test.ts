/**
 * Layer B tests for the position-tool renderer.
 *
 * The chart engine is stubbed, not loaded: the primitive only imports
 * lightweight-charts *types*, so its drawing can be exercised by handing it a
 * fake chart/series pair and a fake canvas context, then reading back the
 * rectangles it asked for.
 *
 * Coordinates are arranged so they are trivial to assert:
 *   x = ms / 1000        (timeToCoordinate)
 *   y = 1000 − price     (priceToCoordinate — y grows downward, price upward)
 */
import { describe, it, expect } from 'vitest';

import { RiskRewardPrimitive, DEFAULT_COLORS } from '../riskRewardPrimitive';
import type { RiskRewardModel, RiskRewardSegment } from '../../../core';

interface Rect { x: number; y: number; w: number; h: number; fill: string }

function render(model: RiskRewardModel) {
  const rects: Rect[] = [];
  const strokedRects: Rect[] = [];
  const lines: Array<{ x1: number; y1: number; x2: number; y2: number; dash: number[] }> = [];
  const texts: string[] = [];

  let fillStyle = '';
  let dash: number[] = [];
  let path: { x1: number; y1: number; x2: number; y2: number } | null = null;
  let pendingRect: Rect | null = null;

  const context: any = {
    save() {}, restore() {},
    set fillStyle(v: string) { fillStyle = v; },
    get fillStyle() { return fillStyle; },
    strokeStyle: '', lineWidth: 0, font: '', textBaseline: '',
    setLineDash(d: number[]) { dash = d; },
    fillRect(x: number, y: number, w: number, h: number) {
      rects.push({ x, y, w, h, fill: fillStyle });
    },
    beginPath() { path = null; pendingRect = null; },
    rect(x: number, y: number, w: number, h: number) {
      pendingRect = { x, y, w, h, fill: fillStyle };
    },
    moveTo(x: number, y: number) { path = { x1: x, y1: y, x2: x, y2: y }; },
    lineTo(x: number, y: number) { if (path) { path.x2 = x; path.y2 = y; } },
    stroke() {
      if (pendingRect) strokedRects.push(pendingRect);
      else if (path) lines.push({ ...path, dash: [...dash] });
    },
    fill() { if (pendingRect) rects.push(pendingRect); },
    measureText(t: string) { return { width: t.length * 6 }; },
    fillText(t: string) { texts.push(t); },
  };

  const chart: any = {
    timeScale: () => ({
      getVisibleRange: () => null,
      timeToCoordinate: (t: number) => t,     // seconds in → px out
    }),
  };
  const series: any = { priceToCoordinate: (p: number) => 1000 - p };

  const p = new RiskRewardPrimitive(model, DEFAULT_COLORS, 2);
  p.attached({ chart, series, requestUpdate: () => {} } as any);
  p.paneViews()[0].renderer().draw({
    useMediaCoordinateSpace: (fn: any) => fn({ context, mediaSize: { width: 5000 } }),
  } as any);

  return { rects, strokedRects, lines, texts };
}

const SEC = 1000;

function seg(from: number, to: number, entry: number,
             stop: number | null, target: number | null): RiskRewardSegment {
  return { from: from * SEC, to: to * SEC, entry, stop, target };
}

function model(patch: Partial<RiskRewardModel> = {}): RiskRewardModel {
  const segments = patch.segments ?? [seg(100, 200, 500, 480, 560)];
  return {
    direction: 'long',
    segments,
    stepped: segments.length > 1,
    reconstructed: false,
    outer: { from: segments[0].from, to: segments[segments.length - 1].to, low: 480, high: 560 },
    inner: null,
    entryPrice: 500, stopPrice: 480, targetPrice: 560, currentPrice: 510,
    riskPct: 4, rewardPct: 12, riskReward: 3,
    progressPct: 16.6, towardStopPct: 0,
    pnlPct: 2, inProfit: true,
    ...patch,
  };
}

describe('RiskRewardPrimitive — TradingView-style position zones', () => {
  it('fills a green zone entry→target and a red zone entry→stop', () => {
    const { rects } = render(model());

    const green = rects.find(r => r.fill === DEFAULT_COLORS.profitFill)!;
    const red   = rects.find(r => r.fill === DEFAULT_COLORS.lossFill)!;
    expect(green).toBeDefined();
    expect(red).toBeDefined();

    // Long: target 560 is above entry 500 → the green zone is the upper band.
    expect(green.y).toBe(1000 - 560);        // top edge at the target
    expect(green.h).toBe(60);                // 560 − 500
    // Red runs from the entry down to the stop.
    expect(red.y).toBe(1000 - 500);          // top edge at the entry
    expect(red.h).toBe(20);                  // 500 − 480

    // Both zones span the segment, not the whole pane.
    expect(green.x).toBe(100);
    expect(green.w).toBe(100);
    expect(red.x).toBe(100);
    expect(red.w).toBe(100);
  });

  it('puts the green zone below the entry for a short', () => {
    const { rects } = render(model({
      direction: 'short',
      segments: [seg(100, 200, 500, 520, 440)],
      stopPrice: 520, targetPrice: 440,
    }));
    const green = rects.find(r => r.fill === DEFAULT_COLORS.profitFill)!;
    const red   = rects.find(r => r.fill === DEFAULT_COLORS.lossFill)!;
    expect(green.y).toBe(1000 - 500);   // entry at the top, target 440 below
    expect(green.h).toBe(60);
    expect(red.y).toBe(1000 - 520);     // stop 520 above the entry
    expect(red.h).toBe(20);
  });

  it('draws one pair of zones per rung of an amended order', () => {
    const { rects } = render(model({
      segments: [
        seg(100, 200, 500, 480, 560),
        seg(200, 300, 510, 490, 570),
        seg(300, 400, 520, 500, 580),
      ],
    }));
    expect(rects.filter(r => r.fill === DEFAULT_COLORS.profitFill)).toHaveLength(3);
    expect(rects.filter(r => r.fill === DEFAULT_COLORS.lossFill)).toHaveLength(3);

    const greens = rects.filter(r => r.fill === DEFAULT_COLORS.profitFill);
    expect(greens.map(g => g.x)).toEqual([100, 200, 300]);
    expect(greens.map(g => g.y)).toEqual([1000 - 560, 1000 - 570, 1000 - 580]);
  });

  it('connects consecutive rungs with a vertical riser', () => {
    const { lines } = render(model({
      segments: [seg(100, 200, 500, 480, 560), seg(200, 300, 510, 490, 570)],
    }));
    const risers = lines.filter(l => l.dash.length > 0 && l.x1 === l.x2);
    expect(risers).toHaveLength(1);
    expect(risers[0].x1).toBe(200.5);          // step boundary, +0.5 to crisp the line
    expect(risers[0].y1).toBe(1000 - 500);     // from the old entry
    expect(risers[0].y2).toBe(1000 - 510);     // to the new one
  });

  it('draws a solid entry line across each rung', () => {
    const { lines } = render(model({
      segments: [seg(100, 200, 500, 480, 560), seg(200, 300, 510, 490, 570)],
    }));
    const entries = lines.filter(l => l.dash.length === 0 && l.y1 === l.y2);
    expect(entries).toHaveLength(2);
    expect(entries[0].y1).toBe(1000 - 500 + 0.5);
    expect(entries[1].y1).toBe(1000 - 510 + 0.5);
  });

  it('labels the newest levels with price, percentage and R:R', () => {
    const { texts } = render(model({
      segments: [seg(100, 200, 500, 480, 560), seg(200, 300, 510, 490, 570)],
    }));
    expect(texts.some(t => t.startsWith('TP 570.00') && t.includes('+12.00%'))).toBe(true);
    expect(texts.some(t => t.startsWith('SL 490.00') && t.includes('4.00%'))).toBe(true);
    expect(texts.some(t => t.includes('510.00') && t.includes('R:R 3.00'))).toBe(true);
  });

  it('draws nothing when the zones are scrolled out of view', () => {
    const rects: Rect[] = [];
    const chart: any = {
      timeScale: () => ({
        getVisibleRange: () => ({ from: 900, to: 1000 }),   // model ends at 200s
        timeToCoordinate: (t: number) => t,
      }),
    };
    const series: any = { priceToCoordinate: (p: number) => 1000 - p };
    const p = new RiskRewardPrimitive(model(), DEFAULT_COLORS, 2);
    p.attached({ chart, series, requestUpdate: () => {} } as any);
    p.paneViews()[0].renderer().draw({
      useMediaCoordinateSpace: (fn: any) => fn({
        context: { fillRect: (x: number, y: number, w: number, h: number) =>
          rects.push({ x, y, w, h, fill: '' }), save() {}, restore() {} },
        mediaSize: { width: 5000 },
      }),
    } as any);
    expect(rects).toHaveLength(0);
  });
});
