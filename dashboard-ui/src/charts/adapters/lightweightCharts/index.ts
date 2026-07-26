/**
 * Layer B — the lightweight-charts adapter.
 *
 * The ONLY folder allowed to import 'lightweight-charts'. It implements the
 * ChartAdapter interface declared in Layer A, so swapping to klinecharts/ECharts
 * later means adding a sibling folder and changing the single re-export in
 * src/charts/index.ts — no page and no core module changes.
 */
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type IPriceLine,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';

import type {
  ChartAdapter,
  ChartHandle,
  ChartMountOptions,
} from '../../core';

import { RiskRewardPrimitive } from './riskRewardPrimitive';

const toUtc = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

const COLORS = {
  up:        '#22c55e',
  down:      '#ef4444',
  text:      '#94a3b8',
  grid:      'rgba(148, 163, 184, 0.12)',
  border:    'rgba(148, 163, 184, 0.25)',
  boundary:  '#38bdf8',
  stop:      '#ef4444',
  target:    '#22c55e',
  entry:     '#e2e8f0',
};

class LightweightChartHandle implements ChartHandle {
  private chart:      IChartApi;
  private candles:    ISeriesApi<'Candlestick'>;
  private boundaries: ISeriesApi<'Line'>[] = [];
  private priceLines: IPriceLine[] = [];
  private markers:    ISeriesMarkersPluginApi<Time> | null = null;
  private primitive:  RiskRewardPrimitive | null = null;
  private destroyed = false;

  constructor(private readonly container: HTMLElement, options: ChartMountOptions) {
    this.chart = createChart(container, {
      height: options.height,
      width:  container.clientWidth || 320,
      layout: {
        background: { color: 'transparent' },
        textColor:  COLORS.text,
        fontSize:   11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      rightPriceScale: { borderColor: COLORS.border },
      timeScale: {
        borderColor:     COLORS.border,
        timeVisible:     true,
        secondsVisible:  false,
        rightOffset:     4,
      },
      // Touch-first: one-finger drag pans, pinch zooms, no scroll hijacking of
      // the page while the finger is on the chart.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale:  { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
      crosshair:    { mode: CrosshairMode.Magnet },
      localization: { locale: 'en-GB' },
    });

    this.candles = this.chart.addSeries(CandlestickSeries, {
      upColor:       COLORS.up,
      downColor:     COLORS.down,
      borderUpColor: COLORS.up,
      borderDownColor: COLORS.down,
      wickUpColor:   COLORS.up,
      wickDownColor: COLORS.down,
      priceFormat:   { type: 'price', precision: options.priceDecimals, minMove: 10 ** -options.priceDecimals },
    });

    this.applyData(options);
  }

  private clearOverlays(): void {
    this.priceLines.forEach(l => { try { this.candles.removePriceLine(l); } catch { /* already gone */ } });
    this.priceLines = [];

    this.boundaries.forEach(s => { try { this.chart.removeSeries(s); } catch { /* already gone */ } });
    this.boundaries = [];

    if (this.markers) { try { this.markers.setMarkers([]); } catch { /* noop */ } }

    if (this.primitive) {
      try { this.candles.detachPrimitive(this.primitive); } catch { /* noop */ }
      this.primitive = null;
    }
  }

  private applyData(options: ChartMountOptions): void {
    const { candles, riskReward, geometry, priceDecimals } = options;

    this.candles.setData(candles.map(c => ({
      time:  toUtc(c.time),
      open:  c.open,
      high:  c.high,
      low:   c.low,
      close: c.close,
    })));

    this.clearOverlays();

    // ── AI range boundaries ────────────────────────────────────────────────
    if (geometry) {
      for (const line of geometry.lines) {
        const series = this.chart.addSeries(LineSeries, {
          color:            COLORS.boundary,
          lineWidth:        2,
          lineStyle:        LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        series.setData(line.points.map(p => ({ time: toUtc(p.time), value: p.price })));
        this.boundaries.push(series);
      }

      const markers: SeriesMarker<Time>[] = geometry.swings.map(s => ({
        time:     toUtc(s.time),
        position: s.id === 'high' ? 'aboveBar' : 'belowBar',
        color:    COLORS.boundary,
        shape:    s.id === 'high' ? 'arrowDown' : 'arrowUp',
        size:     0.6,
      }));
      markers.sort((a, b) => Number(a.time) - Number(b.time));

      if (this.markers) this.markers.setMarkers(markers);
      else this.markers = createSeriesMarkers(this.candles, markers);
    }

    // ── Risk/reward boxes + price-axis tags ────────────────────────────────
    if (riskReward) {
      const fmt = (v: number) => v.toFixed(priceDecimals);

      const tag = (price: number | null, color: string, title: string, dashed: boolean) => {
        if (price == null) return;
        this.priceLines.push(this.candles.createPriceLine({
          price,
          color,
          lineWidth:        1,
          lineStyle:        dashed ? LineStyle.Dashed : LineStyle.Solid,
          // The primitive already draws these across the box; the price line is
          // here for the axis tag only.
          lineVisible:      false,
          axisLabelVisible: true,
          title:            `${title} ${fmt(price)}`,
        }));
      };

      tag(riskReward.targetPrice, COLORS.target, 'TP', false);
      tag(riskReward.entryPrice,  COLORS.entry,  'ENT', true);
      tag(riskReward.stopPrice,   COLORS.stop,   'SL', false);

      this.primitive = new RiskRewardPrimitive(riskReward);
      this.candles.attachPrimitive(this.primitive);
    }

    this.chart.timeScale().fitContent();
  }

  update(options: ChartMountOptions): void {
    if (this.destroyed) return;
    this.chart.applyOptions({ height: options.height });
    this.applyData(options);
  }

  resize(): void {
    if (this.destroyed) return;
    const width = this.container.clientWidth;
    if (width > 0) this.chart.applyOptions({ width });
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.clearOverlays();
    this.chart.remove();
  }
}

export const lightweightChartsAdapter: ChartAdapter = {
  name: 'lightweight-charts',
  mount(container: HTMLElement, options: ChartMountOptions): ChartHandle {
    return new LightweightChartHandle(container, options);
  },
};

export default lightweightChartsAdapter;
