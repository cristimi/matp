import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ResponsiveContainer, LineChart, Line, ReferenceLine,
  XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import { TopBar, SectionHeader, DataGrid } from '../components/shared';
import {
  getRun, getEquityCurve, getPositions, getSignals,
  BacktestRun, EquityCurvePoint, Position, Signal,
} from '../api';

// ── helpers ────────────────────────────────────────────────────────────────────

const fmtPct = (v: number | null): string =>
  v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';

const fmtPrice = (v: number | null): string =>
  v == null
    ? '—'
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtDt = (s: string | null): string => {
  if (!s) return '—';
  const d = new Date(s);
  return (
    (d.getMonth() + 1).toString().padStart(2, '0') + '-' +
    d.getDate().toString().padStart(2, '0') + ' ' +
    d.getHours().toString().padStart(2, '0') + ':00'
  );
};

const CLOSE_LABELS: Record<string, string> = {
  tp_hit:    'Take Profit Hit',
  sl_hit:    'Stop Loss Hit',
  llm_close: 'LLM Close',
  run_end:   'Run End',
};

// ── equity curve ──────────────────────────────────────────────────────────────

function EquityCurve({ points, initial }: { points: EquityCurvePoint[]; initial: number }) {
  if (!points.length) return null;

  const vals  = points.map(p => p.mark_balance);
  const minY  = Math.min(...vals);
  const maxY  = Math.max(...vals);
  const range = maxY - minY;

  // gradient stop: fraction from top where initial_balance sits
  const baseStop = range > 0
    ? Math.min(100, Math.max(0, ((maxY - initial) / range) * 100)).toFixed(1)
    : '50.0';

  // ~5 x-axis ticks evenly spaced
  const step  = Math.max(1, Math.floor(points.length / 4));
  const ticks = points
    .filter((_, i) => i % step === 0 || i === points.length - 1)
    .map(p => p.candle_ts);

  const fmtTick = (ts: string) => {
    const d = new Date(ts);
    return (
      (d.getMonth() + 1).toString().padStart(2, '0') + '/' +
      d.getDate().toString().padStart(2, '0')
    );
  };

  return (
    <div style={{
      background: 'var(--bg3)', borderRadius: 'var(--r)',
      border: '1px solid var(--border)', padding: '12px', marginBottom: '10px',
    }}>
      <div style={{
        fontSize: '10px', fontWeight: 700, color: 'var(--dim)',
        letterSpacing: '.1em', textTransform: 'uppercase', marginBottom: '8px',
      }}>
        Equity Curve
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="eqLine" x1="0" y1="0" x2="0" y2="1">
              {/* green above initial_balance, red below — split at baseStop% from top */}
              <stop offset={`${baseStop}%`} stopColor="#00a877" />
              <stop offset={`${baseStop}%`} stopColor="#e11d48" />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="candle_ts"
            ticks={ticks}
            tickFormatter={fmtTick}
            tick={{ fontSize: 9, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={[minY * 0.9995, maxY * 1.0005]}
            tick={{ fontSize: 9, fill: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace' }}
            tickFormatter={(v: number) => v.toFixed(0)}
            axisLine={false}
            tickLine={false}
            width={42}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--bg2)', border: '1px solid var(--border)',
              borderRadius: 'var(--pill-r)', fontSize: '10px',
              fontFamily: 'JetBrains Mono, monospace',
            }}
            labelFormatter={(l: string) =>
              new Date(l).toISOString().slice(0, 16).replace('T', ' ')}
            formatter={(v: number | string) => [`$${Number(v).toFixed(4)}`, 'Balance']}
          />
          <ReferenceLine
            y={initial}
            stroke="var(--border-hi)"
            strokeDasharray="4 3"
            strokeWidth={1.5}
          />
          <Line
            dataKey="mark_balance"
            stroke="url(#eqLine)"
            dot={false}
            strokeWidth={2}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── trade card ────────────────────────────────────────────────────────────────

function SidePill({ side }: { side: string }) {
  const isLong = side === 'long';
  return (
    <span style={{
      fontSize: '9px', fontWeight: 700, fontFamily: 'JetBrains Mono, monospace',
      letterSpacing: '.1em', textTransform: 'uppercase',
      padding: '2px 7px', borderRadius: '10px',
      background: isLong ? 'var(--green-a)' : 'var(--red-a)',
      color:      isLong ? 'var(--green)'   : 'var(--red)',
      border:     `1px solid ${isLong ? 'var(--green-b)' : 'var(--red-b)'}`,
    }}>
      {side}
    </span>
  );
}

function Mono({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <span style={{
      fontFamily: 'JetBrains Mono, monospace', fontSize: '13px',
      fontWeight: 600, color: 'var(--text)', ...style,
    }}>
      {children}
    </span>
  );
}

function TradeCard({ pos }: { pos: Position }) {
  const isWin      = (pos.pnl_realized ?? 0) > 0;
  const pnlColor   = isWin ? 'var(--green)' : 'var(--red)';
  const closeLabel = pos.close_reason
    ? (CLOSE_LABELS[pos.close_reason] ?? pos.close_reason.replace(/_/g, ' '))
    : '—';
  const totalFees  = pos.fee_open != null && pos.fee_close != null
    ? (pos.fee_open + pos.fee_close).toFixed(4)
    : '—';

  return (
    <div style={{
      background: 'var(--bg3)', borderRadius: 'var(--r)',
      border: '1px solid var(--border)', overflow: 'hidden',
      marginBottom: '8px', position: 'relative',
    }}>
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: '4px',
        background: isWin ? 'var(--green)' : 'var(--red)',
      }} />

      {/* symbol + side + pnl */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '10px 12px 0 18px' }}>
        <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text)' }}>
          {pos.symbol}
        </span>
        <SidePill side={pos.side} />
        <span style={{ marginLeft: 'auto', fontSize: '13px', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, color: pnlColor }}>
          {pos.pnl_realized != null
            ? (pos.pnl_realized >= 0 ? '+' : '') + pos.pnl_realized.toFixed(4)
            : '—'}
        </span>
      </div>

      <DataGrid rows={[
        [
          { label: 'Entry', value: <Mono>{fmtPrice(pos.entry_price)}</Mono> },
          { label: 'Exit',  value: <Mono>{fmtPrice(pos.closing_price)}</Mono> },
          { label: 'Size',  value: <Mono style={{ fontSize: '12px' }}>{pos.size.toFixed(6)}</Mono> },
        ],
        [
          { label: 'Open',  value: <Mono style={{ fontSize: '11px' }}>{fmtDt(pos.opened_at)}</Mono> },
          { label: 'Close', value: <Mono style={{ fontSize: '11px' }}>{fmtDt(pos.closed_at)}</Mono> },
          { label: 'Fees',  value: <Mono>{totalFees}</Mono> },
        ],
      ]} />

      {/* close reason band */}
      <div style={{
        background: 'var(--bg2)', borderTop: '1px solid var(--border)',
        padding: '5px 12px 5px 18px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{
          fontSize: '10px', color: 'var(--dim)',
          fontFamily: 'JetBrains Mono, monospace',
          textTransform: 'uppercase', letterSpacing: '.06em',
        }}>
          {closeLabel}
        </span>
        <span style={{ fontSize: '10px', fontWeight: 700, color: pnlColor }}>
          {isWin ? 'WIN' : 'LOSS'}
        </span>
      </div>
    </div>
  );
}

// ── signal row ────────────────────────────────────────────────────────────────

function SignalRow({ sig }: { sig: Signal }) {
  return (
    <div style={{
      padding: '6px 12px', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', gap: '8px', minHeight: '30px',
    }}>
      <span style={{
        fontSize: '10px', fontFamily: 'JetBrains Mono, monospace',
        color: 'var(--dim)', minWidth: '90px', flexShrink: 0,
      }}>
        {fmtDt(sig.triggered_at)}
      </span>
      <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text)', minWidth: '50px' }}>
        {sig.proposed_action}
      </span>
      <span style={{ fontSize: '10px', color: 'var(--dim)', marginLeft: 'auto', textAlign: 'right' }}>
        {sig.gate_rejection_reason ?? '—'}
      </span>
    </div>
  );
}

// ── meta pill ─────────────────────────────────────────────────────────────────

function MetaPill({ children, dim }: { children: React.ReactNode; dim?: boolean }) {
  return (
    <span style={{
      fontSize: '10px', fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,
      padding: '3px 8px', borderRadius: '10px',
      background: dim ? 'var(--bg3)' : 'var(--bg2)',
      border: `1px solid ${dim ? 'var(--border)' : 'var(--border-hi)'}`,
      color: dim ? 'var(--dim)' : 'var(--muted)',
    }}>
      {children}
    </span>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

export function SimulationScreen() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate  = useNavigate();

  const [run,       setRun]       = useState<BacktestRun | null>(null);
  const [curve,     setCurve]     = useState<EquityCurvePoint[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [signals,   setSignals]   = useState<Signal[]>([]);
  const [loading,   setLoading]   = useState(false);
  const [error,     setError]     = useState<string | null>(null);
  const [sigsOpen,  setSigsOpen]  = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const [r, ec, pos, sigs] = await Promise.all([
        getRun(id),
        getEquityCurve(id),
        getPositions(id),
        getSignals(id, false),
      ]);
      setRun(r);
      setCurve(ec.items);
      setPositions(pos.items);
      setSignals(sigs.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (runId) load(runId);
  }, [runId, load]);

  // re-poll while run is live
  useEffect(() => {
    if (!run || (run.status !== 'running' && run.status !== 'pending')) return;
    const t = setTimeout(() => { if (runId) load(runId); }, 5000);
    return () => clearTimeout(t);
  }, [run, runId, load]);

  // ── no runId placeholder ───────────────────────────────────────────────────

  if (!runId) {
    return (
      <>
        <TopBar title="Simulation" />
        <div className="scroll-area" style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--dim)', fontSize: '13px' }}>
          Select a run from the Strategies screen to view results.
        </div>
      </>
    );
  }

  if (loading && !run) {
    return (
      <>
        <TopBar title="Simulation" onBack={() => navigate('/')} />
        <div className="scroll-area" style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--dim)', fontSize: '13px' }}>
          Loading…
        </div>
      </>
    );
  }

  if (error) {
    return (
      <>
        <TopBar title="Simulation" onBack={() => navigate('/')} />
        <div className="scroll-area" style={{ padding: '20px', textAlign: 'center', color: 'var(--red)', fontSize: '13px' }}>
          {error}
        </div>
      </>
    );
  }

  if (!run) return null;

  // ── derived ────────────────────────────────────────────────────────────────

  const symbol    = positions.length > 0 ? positions[0].symbol : run.strategy_id;
  const pnlPct    = run.total_pnl_pct ?? 0;
  const pnlColor  = pnlPct >= 0 ? 'var(--green)' : 'var(--red)';
  const isAborted = run.status === 'aborted_high_failure_rate';
  const isRunning = run.status === 'running' || run.status === 'pending';
  const modelTag  = run.llm_model
    ? `${run.llm_provider ?? ''}/${run.llm_model}`.replace(/^\//, '')
    : null;

  const summaryItems = [
    { label: 'Trades',   value: String(run.total_trades ?? '—'),                               color: 'var(--text)' },
    { label: 'Win Rate', value: run.win_rate != null ? run.win_rate.toFixed(1) + '%' : '—',    color: 'var(--text)' },
    { label: 'Net P&L',  value: fmtPct(pnlPct),                                                color: pnlColor      },
  ];

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <>
      <TopBar
        title="Simulation"
        onBack={() => navigate('/')}
        right={isRunning ? (
          <span style={{ fontSize: '11px', color: 'var(--blue)', fontFamily: 'JetBrains Mono, monospace', fontWeight: 600 }}>
            ⟳ running
          </span>
        ) : undefined}
      />

      <div className="scroll-area">

        {/* metadata pill row */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '10px' }}>
          <MetaPill>{symbol}</MetaPill>
          <MetaPill>{run.timeframe}</MetaPill>
          <MetaPill>{run.date_from} → {run.date_to}</MetaPill>
          {modelTag ? <MetaPill>{modelTag}</MetaPill> : <MetaPill dim>dry-signal</MetaPill>}
          <MetaPill dim>{run.status}</MetaPill>
        </div>

        {/* aborted badge */}
        {isAborted && (
          <div style={{
            background: 'var(--failed-color-a)', border: '1px solid var(--failed-color-b)',
            borderRadius: 'var(--pill-r)', padding: '8px 12px', marginBottom: '10px',
          }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: 'var(--failed-color)', margin: 0 }}>
              ⚠ Aborted: High LLM Failure Rate
            </p>
            <p style={{ fontSize: '10px', color: 'var(--failed-color)', margin: '3px 0 0', lineHeight: 1.4 }}>
              Run stopped because the LLM failure rate exceeded the threshold.
              {run.error_message ? ` ${run.error_message}` : ''}
            </p>
          </div>
        )}

        {/* summary bar */}
        <div style={{
          background: 'var(--bg3)', border: '1px solid var(--border)',
          borderRadius: 'var(--r)', display: 'flex', marginBottom: '10px',
        }}>
          {summaryItems.map((c, i) => (
            <div key={c.label} style={{
              flex: 1, padding: '10px', textAlign: 'center',
              borderRight: i < summaryItems.length - 1 ? '1px solid var(--border)' : undefined,
            }}>
              <div style={{ fontSize: '10px', color: 'var(--dim)', fontWeight: 600, letterSpacing: '.06em', textTransform: 'uppercase' }}>
                {c.label}
              </div>
              <div style={{ fontSize: '15px', fontWeight: 700, color: c.color, fontFamily: 'JetBrains Mono, monospace', marginTop: '2px' }}>
                {c.value}
              </div>
            </div>
          ))}
        </div>

        {/* equity curve */}
        {curve.length > 0 && <EquityCurve points={curve} initial={run.initial_balance} />}

        {/* statistics card */}
        <div style={{
          background: 'var(--bg3)', borderRadius: 'var(--r)',
          border: '1px solid var(--border)', overflow: 'hidden',
          marginBottom: '10px', position: 'relative',
        }}>
          <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: '4px', background: 'var(--border-hi)' }} />
          <div style={{ padding: '10px 12px 0 18px', fontSize: '10px', fontWeight: 700, color: 'var(--dim)', letterSpacing: '.1em', textTransform: 'uppercase' }}>
            Statistics
          </div>
          <DataGrid rows={[
            [
              { label: 'Long',          value: <Mono>{String(run.long_count  ?? '—')}</Mono> },
              { label: 'Short',         value: <Mono>{String(run.short_count ?? '—')}</Mono> },
              { label: 'Profit Factor', value: <Mono>{run.profit_factor != null ? run.profit_factor.toFixed(4) : '—'}</Mono> },
            ],
            [
              { label: 'Max Drawdown', value: <Mono style={{ color: 'var(--red)'   }}>{run.max_drawdown_pct != null ? run.max_drawdown_pct.toFixed(2) + '%' : '—'}</Mono> },
              { label: 'Avg Win',      value: <Mono style={{ color: 'var(--green)' }}>{run.avg_win  != null ? '+' + run.avg_win.toFixed(4)  : '—'}</Mono> },
              { label: 'Avg Loss',     value: <Mono style={{ color: 'var(--red)'   }}>{run.avg_loss != null ? run.avg_loss.toFixed(4) : '—'}</Mono> },
            ],
          ]} />
        </div>

        {/* trade cards */}
        {positions.length > 0 && (
          <>
            <SectionHeader label="Trades" count={positions.length} variant="closed" />
            {positions.map(pos => (
              <TradeCard key={pos.id} pos={pos} />
            ))}
          </>
        )}

        {/* filtered signals — collapsed by default */}
        {signals.length > 0 && (
          <div style={{
            marginTop: '8px', background: 'var(--bg3)',
            border: '1px solid var(--border)', borderRadius: 'var(--r)', overflow: 'hidden',
          }}>
            <div
              onClick={() => setSigsOpen(v => !v)}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', cursor: 'pointer', userSelect: 'none' }}
            >
              <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--dim)', letterSpacing: '.1em', textTransform: 'uppercase' }}>
                Filtered Signals
              </span>
              <span style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: '11px', fontWeight: 600,
                color: 'var(--dim)', background: 'var(--bg2)', border: '1px solid var(--border)',
                borderRadius: '10px', padding: '1px 8px',
              }}>
                {signals.length} {sigsOpen ? '▲' : '▼'}
              </span>
            </div>
            {sigsOpen && (
              <div style={{ borderTop: '1px solid var(--border)' }}>
                {signals.map(sig => <SignalRow key={String(sig.id)} sig={sig} />)}
              </div>
            )}
          </div>
        )}

      </div>
    </>
  );
}
