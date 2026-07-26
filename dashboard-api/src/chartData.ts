/**
 * Chart payload builder for the position/order risk-reward overlay.
 *
 * One request returns everything the chart needs: the candle series, the newest
 * AI geometry read for the row's strategy, and the row's own overlay numbers
 * (placed/filled times, entry, stop, target). Two round-trips would let the UI
 * render candles before it knows where to draw the box.
 *
 * All timestamps are epoch milliseconds — the same unit geometry_data uses for
 * anchor_ts / first_swing_ts / swing points. Converting to whatever unit a chart
 * engine wants is the adapter's job, not the API's.
 */
import { getPool } from './db';
import { getRedis } from './redis';

// market-ingestion namespaces its Redis keys by the exchange it ingests from.
// strategy_positions.exchange / orders.platform hold routing words ('auto',
// 'exchange'), not venue names, so the stream exchange comes from the account
// (exchange_accounts.exchange) and falls back to the ingestion default.
const INGESTION_EXCHANGE = process.env.INGESTION_EXCHANGE || 'blofin';

// Tried in order when the strategy's own interval has no stream ingested.
const TIMEFRAME_FALLBACKS = ['1h', '4h', '15m', '1m'];

/**
 * The ladder the chart's timeframe picker offers, finest first. Only these are
 * accepted from `?tf=` — an arbitrary string would let a caller probe for
 * streams that the picker never shows.
 */
export const CHART_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

// A chart opens two rungs below the strategy's own interval, so the entry is
// seen in more detail than the strategy trades on: a 1h strategy charts 15m.
const DEFAULT_STEPS_DOWN = 2;

const DEFAULT_LIMIT = 300;
const MAX_LIMIT     = 2000;   // market-ingestion caps each stream at STREAM_MAXLEN = 2000

export interface Candle {
  time:   number;   // bar open time, epoch ms
  open:   number;
  high:   number;
  low:    number;
  close:  number;
  volume: number;
}

export interface ChartOverlay {
  side:          string | null;
  status:        string | null;
  placed_at:     number | null;   // when the order was submitted — outer box starts here
  filled_at:     number | null;   // when it filled — inner progress box starts here
  entry_price:   number | null;
  stop_price:    number | null;
  target_price:  number | null;
  current_price: number | null;   // last candle close, so box and candles agree
  closed_at:     number | null;
  close_price:   number | null;
}

export interface ChartPayload {
  symbol:              string;
  exchange:            string;
  timeframe:           string | null;   // null when nothing is ingested for this symbol
  timeframe_requested: string | null;
  /** Ladder rungs that actually have a stream — what the picker may offer. */
  available_timeframes: string[];
  bar_seconds:         number | null;
  candles:             Candle[];
  geometry:            Record<string, any> | null;
  geometry_at:         number | null;
  overlay:             ChartOverlay;
  note?:               string;
}

const streamKey = (exchange: string, symbol: string, tf: string) =>
  `stream:candles:${exchange}:${symbol}:${tf}`;

const formingKey = (exchange: string, symbol: string, tf: string) =>
  `candle:forming:${exchange}:${symbol}:${tf}`;

const TIMEFRAME_SECONDS: Record<string, number> = {
  '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800,
  '12h': 43200, '1d': 86400,
};

const toMs = (d: any): number | null =>
  d == null ? null : new Date(d).getTime();

const toNum = (v: any): number | null =>
  v == null || v === '' ? null : Number(v);

const dedupe = (values: Array<string | null>): string[] =>
  values.filter((v, i, arr): v is string => !!v && arr.indexOf(v) === i);

/** `?tf=` guard: anything not on the picker's ladder is ignored, not honoured. */
export function normalizeTimeframe(raw: any): string | null {
  const tf = String(raw ?? '').trim();
  return CHART_TIMEFRAMES.includes(tf) ? tf : null;
}

/**
 * Where the chart opens when the caller names no timeframe: two rungs below the
 * strategy's interval. Intervals off the ladder (3m, 2h) snap down to the rung
 * at or below them first, so a 2h strategy counts from 1h and opens on 15m.
 */
function defaultTimeframe(strategyInterval: string | null): string | null {
  if (!strategyInterval) return null;
  const seconds = TIMEFRAME_SECONDS[strategyInterval];
  if (seconds == null) return strategyInterval;

  let rung = -1;
  CHART_TIMEFRAMES.forEach((tf, i) => {
    if (TIMEFRAME_SECONDS[tf] <= seconds) rung = i;
  });
  if (rung < 0) return strategyInterval;

  return CHART_TIMEFRAMES[Math.max(0, rung - DEFAULT_STEPS_DOWN)];
}

/** Which ladder rungs are ingested for this symbol — the picker's option list. */
async function availableTimeframes(exchange: string, symbol: string): Promise<string[]> {
  const redis = getRedis();
  const found = await Promise.all(
    CHART_TIMEFRAMES.map(tf => redis.exists(streamKey(exchange, symbol, tf))),
  );
  return CHART_TIMEFRAMES.filter((_, i) => found[i]);
}

/**
 * First (exchange, timeframe) pair that actually has a stream.
 *
 * The account's venue is tried first so a chart matches where the trade lives,
 * but market-ingestion only ingests from INGESTION_EXCHANGE — a strategy running
 * on hyperliquid still has to fall back to the blofin candles for the same
 * symbol. Candles are near-identical across venues; the alternative is no chart.
 */
async function pickStream(
  exchanges: Array<string | null>,
  symbol: string,
  preferred: string | null,
): Promise<{ exchange: string; timeframe: string } | null> {
  const redis      = getRedis();
  const venues     = dedupe(exchanges);
  const timeframes = dedupe([preferred, ...TIMEFRAME_FALLBACKS]);

  for (const exchange of venues) {
    for (const tf of timeframes) {
      if (await redis.exists(streamKey(exchange, symbol, tf))) {
        return { exchange, timeframe: tf };
      }
    }
  }
  return null;
}

/**
 * Closed candles oldest-first, with the forming candle appended when it is newer
 * than the last closed bar. XREVRANGE gives newest-first, so the slice is taken
 * from the recent end and then reversed.
 *
 * `endMs` windows the series on a moment in the past (an AI signal's trigger
 * time) instead of "now". Filtering is done on the candles' own open-time rather
 * than on the Redis entry ID, because the entry ID is the time the bar was
 * *written* — roughly the bar's close, one full bar later.
 */
async function readCandles(
  exchange: string,
  symbol: string,
  timeframe: string,
  limit: number,
  endMs?: number,
): Promise<Candle[]> {
  const redis = getRedis();

  // Windowed reads over-fetch, then slice, since the cut is on a field rather
  // than on the stream key.
  const count   = endMs != null ? Math.min(limit * 6, MAX_LIMIT) : limit;
  const entries = await redis.xRevRange(
    streamKey(exchange, symbol, timeframe), '+', '-', { COUNT: count },
  );

  let candles: Candle[] = entries.map((e: any) => ({
    time:   Number(e.message.t),
    open:   Number(e.message.o),
    high:   Number(e.message.h),
    low:    Number(e.message.l),
    close:  Number(e.message.c),
    volume: Number(e.message.v),
  })).reverse();

  if (endMs != null) {
    const windowed = candles.filter(c => c.time <= endMs);
    // An empty window means the moment predates what the stream still retains —
    // fall back to the recent bars rather than returning nothing.
    if (windowed.length) return windowed.slice(-limit);
    return candles.slice(-limit);
  }

  const rawForming = await redis.get(formingKey(exchange, symbol, timeframe));
  if (rawForming) {
    try {
      const f    = JSON.parse(rawForming);
      const last = candles[candles.length - 1];
      if (!last || Number(f.t) > last.time) {
        candles.push({
          time:   Number(f.t),
          open:   Number(f.o),
          high:   Number(f.h),
          low:    Number(f.l),
          close:  Number(f.c),
          volume: Number(f.v),
        });
      }
    } catch {
      // A malformed forming candle must not cost the caller the closed series.
    }
  }

  return candles.filter(c => Number.isFinite(c.time) && Number.isFinite(c.close));
}

/** Newest geometry read for a strategy — what the LLM saw on its last cycle. */
async function latestGeometry(
  strategyId: string,
): Promise<{ data: Record<string, any>; at: number | null } | null> {
  const { rows } = await getPool().query(
    `SELECT geometry_data, triggered_at
       FROM ai_signal_log
      WHERE strategy_id = $1
        AND geometry_data IS NOT NULL
      ORDER BY triggered_at DESC
      LIMIT 1`,
    [strategyId],
  );
  if (!rows.length) return null;
  return { data: rows[0].geometry_data, at: toMs(rows[0].triggered_at) };
}

export function clampLimit(raw: any): number {
  const n = parseInt(String(raw ?? ''), 10);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_LIMIT;
  return Math.min(n, MAX_LIMIT);
}

interface AssembleOptions {
  /**
   * Use this geometry instead of the strategy's newest. Set for AI-log charts,
   * where the point is the range that signal actually saw.
   */
  geometry?:   Record<string, any> | null;
  geometryAt?: number | null;
  /** Window the candles on a past moment rather than on "now". */
  endMs?:      number;
}

/** Shared tail: resolve the stream, read candles, attach geometry and overlay. */
async function assemble(
  symbol: string,
  accountExchange: string | null,
  preferredTimeframe: string | null,
  strategyId: string,
  limit: number,
  overlay: Omit<ChartOverlay, 'current_price'>,
  opts: AssembleOptions = {},
): Promise<ChartPayload> {
  const accountVenue = accountExchange ? accountExchange.toLowerCase() : null;
  const stream = await pickStream(
    [accountVenue, INGESTION_EXCHANGE], symbol, preferredTimeframe,
  );

  const candles = stream
    ? await readCandles(stream.exchange, symbol, stream.timeframe, limit, opts.endMs)
    : [];

  const geo = opts.geometry !== undefined
    ? (opts.geometry ? { data: opts.geometry, at: opts.geometryAt ?? null } : null)
    : await latestGeometry(strategyId);
  const last = candles[candles.length - 1];

  const payload: ChartPayload = {
    symbol,
    exchange:            stream?.exchange ?? accountVenue ?? INGESTION_EXCHANGE,
    timeframe:           stream?.timeframe ?? null,
    timeframe_requested: preferredTimeframe,
    available_timeframes: stream ? await availableTimeframes(stream.exchange, symbol) : [],
    bar_seconds:         stream ? (TIMEFRAME_SECONDS[stream.timeframe] ?? null) : null,
    candles,
    geometry:            geo?.data ?? null,
    geometry_at:         geo?.at   ?? null,
    overlay: { ...overlay, current_price: last ? last.close : null },
  };

  const notes: string[] = [];
  if (!stream) {
    notes.push(
      `No candle stream ingested for ${symbol}. ` +
      `Add "${symbol}:${preferredTimeframe || '1h'}" to INGESTION_SUBSCRIPTIONS.`,
    );
  } else {
    if (accountVenue && accountVenue !== stream.exchange) {
      notes.push(`Candles from ${stream.exchange} — ${accountVenue} is not ingested.`);
    }
    if (preferredTimeframe && preferredTimeframe !== stream.timeframe) {
      notes.push(`${preferredTimeframe} is not ingested — showing ${stream.timeframe}.`);
    }
  }
  if (notes.length) payload.note = notes.join(' ');

  return payload;
}

/**
 * Position chart. The outer box runs from the opening order's submit time; the
 * inner progress box from the position's fill. Stop and target come from the
 * opening order — a later amend writes a new order row, so a re-fitted stop is
 * not reflected here.
 */
export async function buildPositionChart(
  positionId: string,
  limit: number,
  requestedTimeframe: string | null = null,
): Promise<ChartPayload | null> {
  const { rows } = await getPool().query(
    `SELECT sp.strategy_id,
            sp.symbol,
            sp.side,
            sp.status,
            sp.entry_price,
            sp.opened_at,
            sp.closed_at,
            sp.closing_price,
            s.interval        AS strategy_interval,
            ea.exchange       AS account_exchange,
            o.received_at     AS received_at,
            o.sl_price,
            o.tp_price,
            oel.placed_at     AS oel_placed_at,
            oel.filled_at     AS oel_filled_at
       FROM strategy_positions sp
       JOIN strategies s        ON s.id  = sp.strategy_id
       LEFT JOIN exchange_accounts ea ON ea.id = s.account_id
       LEFT JOIN orders o       ON o.id  = sp.opening_order_id
       LEFT JOIN order_execution_log oel
              ON oel.exchange_order_id = o.exchange_order_id
             AND o.exchange_order_id IS NOT NULL
      WHERE sp.id = $1`,
    [positionId],
  );
  if (!rows.length) return null;
  const r = rows[0];

  return assemble(
    r.symbol,
    r.account_exchange,
    requestedTimeframe ?? defaultTimeframe(r.strategy_interval),
    r.strategy_id,
    limit,
    {
      side:         r.side,
      status:       r.status,
      // order_execution_log carries the exchange's own placed/filled stamps; the
      // orders row and the position's opened_at are the fallbacks for rows that
      // predate OEL or were closed without one.
      placed_at:    toMs(r.oel_placed_at) ?? toMs(r.received_at) ?? toMs(r.opened_at),
      filled_at:    toMs(r.oel_filled_at) ?? toMs(r.opened_at),
      entry_price:  toNum(r.entry_price),
      stop_price:   toNum(r.sl_price),
      target_price: toNum(r.tp_price),
      closed_at:    toMs(r.closed_at),
      close_price:  toNum(r.closing_price),
    },
  );
}

/**
 * Order chart. Fill time comes from order_execution_log, falling back to
 * updated_at for filled orders that have no OEL row (closes are never logged
 * there — OEL is written on placement). A still-resting order has no fill, so
 * the inner progress box is absent and only the outer stop→target box is drawn.
 */
export async function buildOrderChart(
  orderId: string,
  limit: number,
  requestedTimeframe: string | null = null,
): Promise<ChartPayload | null> {
  const { rows } = await getPool().query(
    `SELECT o.strategy_id,
            o.symbol,
            o.side,
            o.status,
            o.price,
            o.actual_fill_price,
            o.tp_price,
            o.sl_price,
            o.received_at,
            o.updated_at,
            s.interval  AS strategy_interval,
            ea.exchange AS account_exchange,
            oel.placed_at AS oel_placed_at,
            oel.filled_at AS oel_filled_at
       FROM orders o
       JOIN strategies s ON s.id = o.strategy_id
       LEFT JOIN exchange_accounts ea ON ea.id = COALESCE(o.account_id, s.account_id)
       LEFT JOIN order_execution_log oel
              ON oel.exchange_order_id = o.exchange_order_id
             AND o.exchange_order_id IS NOT NULL
      WHERE o.id = $1`,
    [orderId],
  );
  if (!rows.length) return null;
  const r = rows[0];

  const isFilled = String(r.status || '').toLowerCase() === 'filled';

  return assemble(
    r.symbol,
    r.account_exchange,
    requestedTimeframe ?? defaultTimeframe(r.strategy_interval),
    r.strategy_id,
    limit,
    {
      side:         r.side,
      status:       r.status,
      placed_at:    toMs(r.oel_placed_at) ?? toMs(r.received_at),
      filled_at:    toMs(r.oel_filled_at) ?? (isFilled ? toMs(r.updated_at) : null),
      entry_price:  toNum(r.actual_fill_price) ?? toNum(r.price),
      stop_price:   toNum(r.sl_price),
      target_price: toNum(r.tp_price),
      closed_at:    null,
      close_price:  null,
    },
  );
}

/**
 * AI-log chart. Two things differ from the position/order charts:
 *
 *  - The geometry is **that row's own**, not the strategy's newest — the point of
 *    the chart is to show the range the model was looking at when it decided.
 *  - The candle window ends a little after the trigger, so the chart shows the
 *    market as it stood at decision time plus what happened next, rather than
 *    only the most recent bars.
 *
 * The overlay comes from the order the signal produced, when it produced one; a
 * hold or a gate rejection has no order, so the chart is candles + range only.
 */
const SIGNAL_LOOKAHEAD_BARS = 40;

export async function buildSignalChart(
  signalId: string,
  limit: number,
  requestedTimeframe: string | null = null,
): Promise<ChartPayload | null> {
  const { rows } = await getPool().query(
    `SELECT l.strategy_id,
            l.triggered_at,
            l.geometry_data,
            l.order_id,
            s.symbol,
            s.interval    AS strategy_interval,
            ea.exchange   AS account_exchange,
            o.side,
            o.status,
            o.price,
            o.actual_fill_price,
            o.tp_price,
            o.sl_price,
            o.received_at,
            o.updated_at,
            oel.placed_at AS oel_placed_at,
            oel.filled_at AS oel_filled_at
       FROM ai_signal_log l
       JOIN strategies s ON s.id = l.strategy_id
       LEFT JOIN exchange_accounts ea ON ea.id = s.account_id
       LEFT JOIN orders o             ON o.id  = l.order_id
       LEFT JOIN order_execution_log oel
              ON oel.exchange_order_id = o.exchange_order_id
             AND o.exchange_order_id IS NOT NULL
      WHERE l.id = $1`,
    [signalId],
  );
  if (!rows.length) return null;
  const r = rows[0];

  const triggeredAt = toMs(r.triggered_at);
  // The lookahead is counted in bars of the timeframe on screen, so switching to
  // a finer one zooms in on the decision rather than showing the same wide span.
  const displayTf   = requestedTimeframe ?? defaultTimeframe(r.strategy_interval);
  const barSeconds  = TIMEFRAME_SECONDS[displayTf ?? ''] ?? 3600;
  const endMs       = triggeredAt != null
    ? triggeredAt + SIGNAL_LOOKAHEAD_BARS * barSeconds * 1000
    : undefined;

  const hasOrder  = r.order_id != null;
  const isFilled  = String(r.status || '').toLowerCase() === 'filled';

  return assemble(
    r.symbol,
    r.account_exchange,
    displayTf,
    r.strategy_id,
    limit,
    {
      side:         hasOrder ? r.side   : null,
      status:       hasOrder ? r.status : null,
      placed_at:    hasOrder ? (toMs(r.oel_placed_at) ?? toMs(r.received_at)) : null,
      filled_at:    hasOrder
        ? (toMs(r.oel_filled_at) ?? (isFilled ? toMs(r.updated_at) : null))
        : null,
      entry_price:  hasOrder ? (toNum(r.actual_fill_price) ?? toNum(r.price)) : null,
      stop_price:   hasOrder ? toNum(r.sl_price) : null,
      target_price: hasOrder ? toNum(r.tp_price) : null,
      closed_at:    null,
      close_price:  null,
    },
    { geometry: r.geometry_data ?? null, geometryAt: triggeredAt, endMs },
  );
}
