/**
 * Expandable candle chart for a position or order row.
 *
 * Collapsed by default: the panel is not mounted, so nothing is fetched and no
 * chart is created until the user taps. Collapsing unmounts it again — charts are
 * not kept alive for rows nobody is looking at.
 *
 * Engine independence: this file imports `chartAdapter` and the pure helpers from
 * ../charts, never a charting library. See src/charts/index.ts.
 */
import React, { useState, useEffect, useRef, useMemo } from 'react';

import {
  chartAdapter,
  computeRiskReward,
  computeGeometryModel,
  type ChartPayload,
  type ChartHandle,
} from '../charts';
import { api } from '../api';
import { priceDecimals } from '../utils/precision';

const CHART_HEIGHT = 280;

function TimeframeTabs({
  options,
  active,
  busy,
  onPick,
}: {
  options: string[];
  active:  string | null;
  busy:    boolean;
  onPick:  (tf: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
      {options.map(tf => {
        const on = tf === active;
        return (
          <button
            key={tf}
            type="button"
            onClick={() => !on && onPick(tf)}
            disabled={busy && !on}
            aria-pressed={on}
            style={{
              minWidth:      '34px',
              minHeight:     '26px',
              padding:       '0 8px',
              borderRadius:  '5px',
              cursor:        on ? 'default' : 'pointer',
              fontFamily:    'JetBrains Mono, monospace',
              fontSize:      '11px',
              fontWeight:    700,
              color:         on ? 'var(--bg2)' : 'var(--muted)',
              background:    on ? 'var(--blue)' : 'transparent',
              border:        `1px solid ${on ? 'var(--blue)' : 'var(--border)'}`,
              opacity:       busy && !on ? 0.5 : 1,
            }}
          >
            {tf}
          </button>
        );
      })}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
      <span style={{
        fontSize: '9px', fontWeight: 700, letterSpacing: '.06em',
        textTransform: 'uppercase', color: 'var(--dim)', whiteSpace: 'nowrap',
      }}>
        {label}
      </span>
      <span style={{
        fontFamily: 'JetBrains Mono, monospace', fontSize: '12px', fontWeight: 700,
        color: color || 'var(--text)', whiteSpace: 'nowrap',
      }}>
        {value}
      </span>
    </div>
  );
}

function Message({ text }: { text: string }) {
  return (
    <div style={{
      padding: '18px 12px', textAlign: 'center',
      fontSize: '12px', color: 'var(--muted)',
    }}>
      {text}
    </div>
  );
}

/**
 * The chart itself. Exported so a caller that needs the toggle and the panel in
 * different places — the Tree puts its button in the card header and the panel
 * directly beneath it — can position each independently. Mount it only while
 * open: mounting is what triggers the fetch and creates the chart.
 */
export function ChartPanel({ path }: { path: string }) {
  const [payload, setPayload] = useState<ChartPayload | null>(null);
  const [error,   setError]   = useState<string | null>(null);
  // null = whatever the API defaults to (two rungs below the strategy's own).
  const [timeframe, setTimeframe] = useState<string | null>(null);
  const [busy,      setBusy]      = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const handleRef    = useRef<ChartHandle | null>(null);

  const url = timeframe
    ? `${path}${path.includes('?') ? '&' : '?'}tf=${timeframe}`
    : path;

  // Fetch once per expand, and again whenever the timeframe is switched. The old
  // payload is deliberately kept while the new one is in flight, so the chart
  // stays on screen instead of being torn down and remounted on every switch.
  // `cancelled` stops a late response from setting state on a panel the user
  // already collapsed, or an out-of-order one from overwriting a newer switch.
  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError(null);

    api.get<ChartPayload>(url)
      .then(data => { if (!cancelled) setPayload(data); })
      .catch(err => { if (!cancelled) setError(err.message || 'Failed to load chart'); })
      .finally(() => { if (!cancelled) setBusy(false); });

    return () => { cancelled = true; };
  }, [url]);

  // The payload's symbol is authoritative — the caller may not have one to hand
  // (an AI log row carries only a strategy id).
  const decimals = useMemo(
    () => priceDecimals(payload?.symbol ?? ''),
    [payload?.symbol],
  );

  const models = useMemo(() => {
    if (!payload) return null;
    return {
      riskReward: computeRiskReward({
        overlay:    payload.overlay,
        candles:    payload.candles,
        barSeconds: payload.bar_seconds,
      }),
      geometry: computeGeometryModel({
        geometry:   payload.geometry,
        candles:    payload.candles,
        barSeconds: payload.bar_seconds,
      }),
    };
  }, [payload]);

  // Mount / update the chart through the adapter interface.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !payload || !models || !payload.candles.length) return;

    const options = {
      candles:       payload.candles,
      riskReward:    models.riskReward,
      geometry:      models.geometry,
      priceDecimals: decimals,
      height:        CHART_HEIGHT,
    };

    if (handleRef.current) handleRef.current.update(options);
    else handleRef.current = chartAdapter.mount(container, options);

    const observer = new ResizeObserver(() => handleRef.current?.resize());
    observer.observe(container);

    return () => {
      observer.disconnect();
      handleRef.current?.destroy();
      handleRef.current = null;
    };
  }, [payload, models, decimals]);

  if (error)    return <Message text={`Chart unavailable: ${error}`} />;
  if (!payload) return <Message text="Loading chart…" />;
  if (payload.note && !payload.candles.length) return <Message text={payload.note} />;
  if (!payload.candles.length) return <Message text="No candle data for this symbol yet." />;

  const rr  = models?.riskReward;
  const geo = models?.geometry;

  // Only rungs with candles behind them are offered; the one in use is always
  // included, in case it came from a fallback outside the ladder.
  const rungs = payload.available_timeframes?.length
    ? payload.available_timeframes
    : (payload.timeframe ? [payload.timeframe] : []);
  const options = payload.timeframe && !rungs.includes(payload.timeframe)
    ? [...rungs, payload.timeframe]
    : rungs;

  const details = [
    rr?.riskPct   != null && { label: 'Risk',   value: `${rr.riskPct.toFixed(2)}%`,   color: 'var(--red)' },
    rr?.rewardPct != null && { label: 'Reward', value: `${rr.rewardPct.toFixed(2)}%`, color: 'var(--green)' },
    rr?.riskReward != null && { label: 'R:R',   value: rr.riskReward.toFixed(2) },
  ].filter(Boolean) as Array<{ label: string; value: string; color?: string }>;

  return (
    <div style={{ borderTop: '1px solid var(--border)', background: 'var(--bg2)' }}>
      {/* Context strip: the timeframe picker, plus what the AI saw */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: '10px 18px',
        padding: '10px 12px 8px 18px', alignItems: 'center',
      }}>
        <TimeframeTabs
          options={options}
          active={payload.timeframe}
          busy={busy}
          onPick={setTimeframe}
        />
        {geo && (
          <Stat label="AI range" value={`${geo.shape.replace(/_/g, ' ')} · ${geo.fitQuality}`} />
        )}
      </div>

      {/* Drag the price axis on the right to zoom vertically, the time axis
          at the bottom to zoom horizontally. */}
      <div ref={containerRef} style={{ width: '100%', height: `${CHART_HEIGHT}px` }} />

      {/* Position details, under the chart */}
      {details.length > 0 && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: '14px 18px',
          padding: '8px 12px 4px 18px', alignItems: 'flex-start',
          borderTop: '1px solid var(--border)',
        }}>
          {details.map(d => (
            <Stat key={d.label} label={d.label} value={d.value} color={d.color} />
          ))}
        </div>
      )}

      {(payload.note || !rr) && (
        <div style={{
          padding: '6px 12px 10px 18px', fontSize: '11px', color: 'var(--dim)',
          display: 'flex', flexDirection: 'column', gap: '2px',
        }}>
          {payload.note && <span>{payload.note}</span>}
          {!rr && <span>No stop or target recorded — showing candles and the AI range only.</span>}
        </div>
      )}
    </div>
  );
}

/** Two candlesticks, drawn in currentColor so the button owns the colour. */
function CandlesGlyph() {
  return (
    <svg width="17" height="17" viewBox="0 0 16 16" aria-hidden="true"
         style={{ display: 'block' }}>
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
        <path d="M4 2.2v11.6" />
        <path d="M12 2.2v11.6" />
      </g>
      <rect x="2"  y="4.5" width="4" height="5.5" rx="1" fill="currentColor" />
      <rect x="10" y="7"   width="4" height="5"   rx="1" fill="currentColor" />
    </svg>
  );
}

/**
 * Round toggle for the chart, sized to sit beside other icon buttons in a card
 * header. The caller owns the open state and renders <ChartPanel> wherever the
 * chart should appear — usually immediately below that header.
 *
 * `onClick` receives the event so the caller can stop it propagating to a
 * clickable card behind it.
 */
export function ChartIconButton({
  open,
  onClick,
  size = 30,
}: {
  open: boolean;
  onClick: (e: React.MouseEvent) => void;
  size?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      aria-label={open ? 'Hide chart' : 'Show chart'}
      title={open ? 'Hide chart' : 'Show chart'}
      style={{
        flexShrink:     0,
        width:          size,
        height:         size,
        borderRadius:   '50%',
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'center',
        cursor:         'pointer',
        // Filled while open, so it reads as a toggle rather than a link.
        color:          open ? 'var(--bg2)' : 'var(--blue)',
        background:     open ? 'var(--blue)' : 'var(--blue-a)',
        border:         '1px solid var(--blue-b)',
        padding:        0,
      }}
    >
      <CandlesGlyph />
    </button>
  );
}

/**
 * Self-contained toggle + panel, for cards that want a full-width control.
 *
 * `variant` controls only the toggle's chrome:
 *   footer  full-bleed card footer, matching ActionBand (Positions, Orders)
 *   inline  full-width, bordered — sits inside an already-indented container
 *
 * Where the button and the panel need to live apart, use ChartIconButton +
 * ChartPanel directly instead.
 */
export function ExpandableChart({
  path,
  variant = 'footer',
}: {
  path: string;
  variant?: 'footer' | 'inline';
}) {
  const [open, setOpen] = useState(false);
  const inline = variant === 'inline';

  return (
    <>
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        style={{
          width:         '100%',
          minHeight:     '44px',          // finger-sized: this is reviewed on a phone
          ...(inline
            ? { border: '1px solid var(--border)', borderRadius: '6px',
                marginTop: '4px', background: 'transparent' }
            : { border: 'none', borderTop: '1px solid var(--border)',
                borderRadius: 0, background: 'var(--bg2)' }),
          color:         'var(--blue)',
          fontSize:      '11px',
          fontWeight:    700,
          letterSpacing: '.06em',
          textTransform: 'uppercase',
          padding:       '10px',
          cursor:        'pointer',
          textAlign:     'center',
        }}
      >
        {open ? '▾ Hide chart' : '▸ Chart'}
      </button>
      {open && <ChartPanel path={path} />}
    </>
  );
}
