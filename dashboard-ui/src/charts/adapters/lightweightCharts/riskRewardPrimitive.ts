/**
 * Layer B — canvas renderer for the risk/reward boxes.
 *
 * This file (and its siblings in this folder) are the only place in the app that
 * knows lightweight-charts exists. It consumes the plain price/time rectangles
 * produced by Layer A and turns them into pixels via priceToCoordinate() /
 * timeToCoordinate(); it computes no trading logic of its own.
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
  outerStroke:  string;
  outerFill:    string;
  profitFill:   string;
  profitStroke: string;
  lossFill:     string;
  lossStroke:   string;
  entryLine:    string;
}

export const DEFAULT_COLORS: RiskRewardColors = {
  outerStroke:  'rgba(148, 163, 184, 0.85)',
  outerFill:    'rgba(148, 163, 184, 0.10)',
  profitFill:   'rgba(34, 197, 94, 0.22)',
  profitStroke: 'rgba(34, 197, 94, 0.90)',
  lossFill:     'rgba(239, 68, 68, 0.22)',
  lossStroke:   'rgba(239, 68, 68, 0.90)',
  entryLine:    'rgba(226, 232, 240, 0.95)',
};

const toUtc = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

class RiskRewardRenderer implements IPrimitivePaneRenderer {
  constructor(
    private readonly chart: IChartApi,
    private readonly series: ISeriesApi<SeriesType>,
    private readonly model: RiskRewardModel,
    private readonly colors: RiskRewardColors,
  ) {}

  draw(target: any): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }: any) => {
      const ts    = this.chart.timeScale();
      const width = mediaSize.width;

      // Skip entirely when the box sits outside the scrolled window — otherwise
      // the null-coordinate fallbacks below would stretch it across the pane.
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

      const drawBox = (
        fromMs: number, toMs: number, low: number, high: number,
        fill: string, stroke: string, dashed = false,
      ) => {
        const yHigh = yAt(high);
        const yLow  = yAt(low);
        if (yHigh === null || yLow === null) return;

        const x1 = xAt(fromMs, 0);
        const x2 = xAt(toMs, width);
        const w  = Math.max(x2 - x1, 1);
        const h  = Math.max(yLow - yHigh, 1);

        context.save();
        context.fillStyle = fill;
        context.fillRect(x1, yHigh, w, h);
        context.strokeStyle = stroke;
        context.lineWidth   = 1;
        context.setLineDash(dashed ? [4, 3] : []);
        context.strokeRect(x1 + 0.5, yHigh + 0.5, w - 1, h - 1);
        context.restore();
      };

      const { outer, inner } = this.model;

      drawBox(
        outer.from, outer.to, outer.low, outer.high,
        this.colors.outerFill, this.colors.outerStroke, true,
      );

      if (inner) {
        const win = this.model.inProfit;
        drawBox(
          inner.from, inner.to, inner.low, inner.high,
          win ? this.colors.profitFill   : this.colors.lossFill,
          win ? this.colors.profitStroke : this.colors.lossStroke,
        );
      }

      // Dashed entry line, spanning the box width only.
      const yEntry = yAt(this.model.entryPrice);
      if (yEntry !== null) {
        const x1 = xAt(outer.from, 0);
        const x2 = xAt(outer.to, width);
        context.save();
        context.strokeStyle = this.colors.entryLine;
        context.lineWidth   = 1;
        context.setLineDash([5, 4]);
        context.beginPath();
        context.moveTo(x1, yEntry + 0.5);
        context.lineTo(x2, yEntry + 0.5);
        context.stroke();
        context.restore();
      }
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

  setModel(model: RiskRewardModel): void {
    this.model = model;
    this.requestUpdate?.();
  }

  paneViews(): IPrimitivePaneView[] {
    if (!this.chart || !this.series) return [];
    return [
      new RiskRewardPaneView(
        new RiskRewardRenderer(this.chart, this.series, this.model, this.colors),
      ),
    ];
  }
}
