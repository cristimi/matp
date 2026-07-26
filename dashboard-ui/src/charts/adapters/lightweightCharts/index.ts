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
  private detachTouch: (() => void) | null = null;
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
      rightPriceScale: { borderColor: COLORS.border, autoScale: true },
      timeScale: {
        borderColor:     COLORS.border,
        timeVisible:     true,
        secondsVisible:  false,
        rightOffset:     4,
      },
      // Touch-first: one-finger drag pans, pinch zooms, no scroll hijacking of
      // the page while the finger is on the chart. vertTouchDrag is false at rest
      // so a finger dragged down the candles scrolls the page; bindPriceAxisTouch
      // flips it on for the length of a touch that starts on the price axis.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale:  {
        mouseWheel: false,
        pinch:      true,
        axisPressedMouseMove: { time: true, price: true },
        axisDoubleClickReset: { time: true, price: true },
      },
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

    this.bindPriceAxisTouch();
    this.applyData(options);
  }

  /**
   * Makes the price axis drag like the time axis on a touch screen.
   *
   * The engine decides "is this drag mine or is it the page scrolling?" from
   * handleScroll: the time axis reads horzTouchDrag (on, so it zooms), the price
   * axis reads vertTouchDrag. But vertTouchDrag is shared with the chart body, so
   * turning it on for good would stop a finger swiped over the candles from
   * scrolling the page. Instead it is on only while a finger is down on the axis
   * itself. The listener is capture-phase, so the flag is already set by the time
   * the engine reads it on the first move of that same touch.
   */
  private bindPriceAxisTouch(): void {
    const setVertDrag = (on: boolean) =>
      this.chart.applyOptions({ handleScroll: { vertTouchDrag: on } });

    const onStart = (e: TouchEvent) => {
      const touch = e.touches[0];
      if (!touch) return;
      const axisWidth = this.chart.priceScale('right').width();
      const fromRight = this.container.getBoundingClientRect().right - touch.clientX;
      setVertDrag(fromRight >= 0 && fromRight <= axisWidth);
    };
    const onEnd = () => setVertDrag(false);

    const opts = { capture: true, passive: true } as const;
    this.container.addEventListener('touchstart',  onStart, opts);
    this.container.addEventListener('touchend',    onEnd,   opts);
    this.container.addEventListener('touchcancel', onEnd,   opts);

    this.detachTouch = () => {
      this.container.removeEventListener('touchstart',  onStart, true);
      this.container.removeEventListener('touchend',    onEnd,   true);
      this.container.removeEventListener('touchcancel', onEnd,   true);
    };
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
    // New candles mean a new price range — a manual vertical zoom from the
    // previous timeframe would leave the series off screen.
    this.chart.priceScale('right').setAutoScale(true);
  }

  update(options: ChartMountOptions): void {
    if (this.destroyed) return;
    this.chart.applyOptions({ height: options.height });
    this.applyData(options);
  }

  zoomPrice(factor: number): void {
    if (this.destroyed) return;
    const scale = this.chart.priceScale('right');

    // getVisibleRange() is null while auto-scaling, so the first zoom has to pin
    // the range the chart is already showing before it can narrow it.
    let range = scale.getVisibleRange();
    if (!range) {
      scale.setAutoScale(false);
      range = scale.getVisibleRange();
    }
    if (!range) return;

    const middle = (range.from + range.to) / 2;
    const half   = ((range.to - range.from) / 2) * factor;
    if (!Number.isFinite(half) || half <= 0) return;

    scale.setVisibleRange({ from: middle - half, to: middle + half });
  }

  resetPriceZoom(): void {
    if (this.destroyed) return;
    this.chart.priceScale('right').setAutoScale(true);
  }

  resize(): void {
    if (this.destroyed) return;
    const width = this.container.clientWidth;
    if (width > 0) this.chart.applyOptions({ width });
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.detachTouch?.();
    this.detachTouch = null;
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
