/**
 * Layer B — canvas renderer for the risk/reward zones.
 *
 * This file (and its siblings in this folder) are the only place in the app that
 * knows lightweight-charts exists. It consumes the plain price/time rectangles
 * produced by Layer A and turns them into pixels via priceToCoordinate() /
 * timeToCoordinate(); it computes no trading logic of its own.
 *
 * Shaped after TradingView's Long/Short Position tool: a green reward zone from
 * entry to target, a red risk zone from entry to stop, a solid entry line
 * between them, and the numbers on the right edge. When the order was amended
 * the zones step — one rung per price it actually rested at.
 */
import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from 'lightweight-charts';

import type { RiskRewardModel } from '../../core';

export interface RiskRewardColors {
  profitFill:   string;
  profitStroke: string;
  lossFill:     string;
  lossStroke:   string;
  entryLine:    string;
  progressFill: string;
}

export const DEFAULT_COLORS: RiskRewardColors = {
  profitFill:   'rgba(34, 197, 94, 0.16)',
  profitStroke: 'rgba(34, 197, 94, 0.70)',
  lossFill:     'rgba(239, 68, 68, 0.16)',
  lossStroke:   'rgba(239, 68, 68, 0.70)',
  entryLine:    'rgba(226, 232, 240, 0.95)',
  progressFill: 'rgba(226, 232, 240, 0.10)',
};

const toUtc = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

class RiskRewardRenderer implements IPrimitivePaneRenderer {
  constructor(
    private readonly chart: IChartApi,
    private readonly series: ISeriesApi<SeriesType>,
    private readonly model: RiskRewardModel,
    private readonly colors: RiskRewardColors,
    private readonly decimals: number,
  ) {}

  draw(target: any): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }: any) => {
      const ts    = this.chart.timeScale();
      const width = mediaSize.width;

      // Skip entirely when the zones sit outside the scrolled window — otherwise
      // the null-coordinate fallbacks below would stretch them across the pane.
      const visible = ts.getVisibleRange();
      if (visible) {
        const vFrom = Number(visible.from);
        const vTo   = Number(visible.to);
        if (toUtc(this.model.outer.to) < vFrom || toUtc(this.model.outer.from) > vTo) return;
      }

      const xAt = (ms: number, fallback: number): number => {
        const x = ts.timeToCoordinate(toUtc(ms) as Time);
        return x === null ? fallback : x;
      };
      const yAt = (price: number): number | null => this.series.priceToCoordinate(price);

      const zone = (
        x1: number, x2: number, yA: number, yB: number,
        fill: string, stroke: string,
      ) => {
        const top = Math.min(yA, yB);
        const h   = Math.max(Math.abs(yB - yA), 1);
        const w   = Math.max(x2 - x1, 1);
        context.save();
        context.fillStyle = fill;
        context.fillRect(x1, top, w, h);
        context.strokeStyle = stroke;
        context.lineWidth   = 1;
        context.beginPath();
        context.rect(x1 + 0.5, top + 0.5, w - 1, h - 1);
        context.stroke();
        context.restore();
      };

      const { segments } = this.model;

      // ── The staircase ──────────────────────────────────────────────────────
      for (const seg of segments) {
        const x1 = xAt(seg.from, 0);
        const x2 = xAt(seg.to, width);
        const yEntry = yAt(seg.entry);
        if (yEntry === null) continue;

        if (seg.target != null) {
          const yT = yAt(seg.target);
          if (yT !== null) {
            zone(x1, x2, yEntry, yT, this.colors.profitFill, this.colors.profitStroke);
          }
        }
        if (seg.stop != null) {
          const yS = yAt(seg.stop);
          if (yS !== null) {
            zone(x1, x2, yEntry, yS, this.colors.lossFill, this.colors.lossStroke);
          }
        }

        // Entry line for this rung — solid, the spine of the position tool.
        context.save();
        context.strokeStyle = this.colors.entryLine;
        context.lineWidth   = 1.5;
        context.beginPath();
        context.moveTo(x1, yEntry + 0.5);
        context.lineTo(Math.max(x2, x1 + 1), yEntry + 0.5);
        context.stroke();
        context.restore();
      }

      // ── Risers: the vertical jump between consecutive rungs ────────────────
      // Without these the staircase reads as disconnected floating lines.
      if (segments.length > 1) {
        context.save();
        context.strokeStyle = this.colors.entryLine;
        context.lineWidth   = 1;
        context.setLineDash([2, 3]);
        for (let i = 1; i < segments.length; i++) {
          const yPrev = yAt(segments[i - 1].entry);
          const yCur  = yAt(segments[i].entry);
          if (yPrev === null || yCur === null) continue;
          const x = xAt(segments[i].from, 0);
          context.beginPath();
          context.moveTo(x + 0.5, yPrev);
          context.lineTo(x + 0.5, yCur);
          context.stroke();
        }
        context.restore();
      }

      // ── Progress band: how far price has come since the fill ───────────────
      const inner = this.model.inner;
      if (inner) {
        const yLow  = yAt(inner.low);
        const yHigh = yAt(inner.high);
        if (yLow !== null && yHigh !== null) {
          const x1 = xAt(inner.from, 0);
          const x2 = xAt(inner.to, width);
          context.save();
          context.fillStyle = this.colors.progressFill;
          context.fillRect(x1, Math.min(yLow, yHigh),
                           Math.max(x2 - x1, 1), Math.max(Math.abs(yLow - yHigh), 1));
          context.restore();
        }
      }

      // No text is drawn over the candles. SL / entry / TP each already carry a
      // tag on the right-hand price axis (the `createPriceLine` calls in the
      // adapter), so chips floating beside the same levels only said it twice and
      // covered the bars while doing it. Risk, reward and R:R moved out to the
      // card's own details grid.
    });
  }
}

class RiskRewardPaneView implements IPrimitivePaneView {
  constructor(private readonly paneRenderer: RiskRewardRenderer) {}
  zOrder() { return 'top' as const; }
  renderer(): IPrimitivePaneRenderer { return this.paneRenderer; }
}

export class RiskRewardPrimitive implements ISeriesPrimitive<Time> {
  private chart:  IChartApi | null = null;
  private series: ISeriesApi<SeriesType> | null = null;
  private requestUpdate?: () => void;

  constructor(
    private model: RiskRewardModel,
    private readonly colors: RiskRewardColors = DEFAULT_COLORS,
    private decimals: number = 2,
  ) {}

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this.chart         = param.chart;
    this.series        = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart  = null;
    this.series = null;
    this.requestUpdate = undefined;
  }

  setModel(model: RiskRewardModel, decimals?: number): void {
    this.model = model;
    if (decimals != null) this.decimals = decimals;
    this.requestUpdate?.();
  }

  paneViews(): IPrimitivePaneView[] {
    if (!this.chart || !this.series) return [];
    return [
      new RiskRewardPaneView(
        new RiskRewardRenderer(
          this.chart, this.series, this.model, this.colors, this.decimals,
        ),
      ),
    ];
  }
}
