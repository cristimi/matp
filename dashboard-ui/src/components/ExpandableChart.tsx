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

  const containerRef = useRef<HTMLDivElement | null>(null);
  const handleRef    = useRef<ChartHandle | null>(null);

  // Fetch once per expand. `cancelled` stops a late response from setting state
  // on a panel the user already collapsed.
  useEffect(() => {
    let cancelled = false;
    setPayload(null);
    setError(null);

    api.get<ChartPayload>(path)
      .then(data => { if (!cancelled) setPayload(data); })
      .catch(err => { if (!cancelled) setError(err.message || 'Failed to load chart'); });

    return () => { cancelled = true; };
  }, [path]);

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

  return (
    <div style={{ borderTop: '1px solid var(--border)', background: 'var(--bg2)' }}>
      {/* Context strip */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: '14px 18px',
        padding: '10px 12px 8px 18px', alignItems: 'flex-start',
      }}>
        <Stat label="Timeframe" value={payload.timeframe || '—'} />
        {rr?.riskPct != null && (
          <Stat label="Risk" value={`${rr.riskPct.toFixed(2)}%`} color="var(--red)" />
        )}
        {rr?.rewardPct != null && (
          <Stat label="Reward" value={`${rr.rewardPct.toFixed(2)}%`} color="var(--green)" />
        )}
        {rr?.riskReward != null && (
          <Stat label="R:R" value={`${rr.riskReward.toFixed(2)}`} />
        )}
        {rr?.progressPct != null && (
          <Stat
            label="To target"
            value={`${rr.progressPct.toFixed(0)}%`}
            color={rr.progressPct > 0 ? 'var(--green)' : 'var(--muted)'}
          />
        )}
        {rr?.towardStopPct != null && rr.towardStopPct > 0 && (
          <Stat label="To stop" value={`${rr.towardStopPct.toFixed(0)}%`} color="var(--red)" />
        )}
        {geo && (
          <Stat label="AI range" value={`${geo.shape.replace(/_/g, ' ')} · ${geo.fitQuality}`} />
        )}
      </div>

      <div ref={containerRef} style={{ width: '100%', height: `${CHART_HEIGHT}px` }} />

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
