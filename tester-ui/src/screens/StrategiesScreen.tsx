import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  TopBar, SummaryBar, FilterBar, SectionHeader,
  ActionBand, DataGrid, HeaderPill,
} from '../components/shared';
import {
  listStrategies, cancelRun, Strategy,
} from '../api';
import { RunPanel } from '../components/RunPanel';
import { PromoteSheet } from '../components/PromoteSheet';

// ── helpers ────────────────────────────────────────────────────────────────────

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function fmtPnlPct(v: number | null | undefined): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
}

function fmtDateRange(from: string | null, to: string | null): string {
  if (!from || !to) return '—';
  const d0 = new Date(from);
  const d1 = new Date(to);
  const months = Math.round((d1.getTime() - d0.getTime()) / (1000 * 60 * 60 * 24 * 30));
  if (months >= 12) return `${Math.round(months / 12)}y`;
  if (months >= 1)  return `${months}mo`;
  return `${Math.round((d1.getTime() - d0.getTime()) / (1000 * 60 * 60 * 24))}d`;
}

function stratStatus(s: Strategy): 'active' | 'running' | 'inactive' {
  if (s.latest_run_status === 'running' || s.latest_run_status === 'pending') return 'running';
  if (s.enabled) return 'active';
  return 'inactive';
}

// ── strategy card ──────────────────────────────────────────────────────────────

interface StratCardProps {
  strategy:       Strategy;
  onRunOpen:      (id: string) => void;
  onCancelRun:    (id: string) => void;
  onPromoteOpen:  (id: string) => void;
  onViewRun:      (runId: string) => void;
}

function StratCard({
  strategy: s, onRunOpen, onCancelRun, onPromoteOpen, onViewRun,
}: StratCardProps) {
  const st = stratStatus(s);
  const provider = s.llm_provider ?? 'google';
  const model    = s.llm_model    ?? 'gemini-2.0-flash';
  const shortModel = model.replace('gemini-', 'g-').replace('-flash', '-fl').replace('-pro', '-pro');

  const hasRun   = !!s.latest_run_id;
  const bestTag  = hasRun
    ? `${fmtPnlPct(s.latest_run_total_pnl_pct)} / ${Number(s.latest_run_win_rate ?? 0).toFixed(1)}% WR / ${s.latest_run_timeframe ?? s.interval} / ${fmtDateRange(s.latest_run_date_from, s.latest_run_date_to)}`
    : 'No runs yet';

  const leftBorder = st === 'active' || st === 'running' ? 'var(--green)' : 'var(--gray)';
  const statusPill: { variant: 'open' | 'closed' | 'stale'; label: string } =
    st === 'running' ? { variant: 'stale', label: 'running' } :
    st === 'active'  ? { variant: 'open',  label: 'active'  } :
                       { variant: 'closed', label: 'inactive' };

  const actionButtons = st === 'running'
    ? [{ label: '⏹ Cancel Run', color: 'red'   as const, onClick: () => onCancelRun(s.latest_run_id!) }]
    : st === 'active'
    ? [{ label: '▶ Run Backtest', color: 'green' as const, onClick: () => onRunOpen(s.id) }]
    : [
        { label: '▶ Start',           color: 'green'  as const, onClick: () => onRunOpen(s.id) },
        { label: '⇑ Promote to MATP', color: 'orange' as const, onClick: () => onPromoteOpen(s.id) },
      ];

  return (
    <div style={{
      background:    'var(--bg3)',
      borderRadius:  'var(--r)',
      border:        `1px solid ${st === 'inactive' ? 'var(--border-hi)' : 'var(--border)'}`,
      overflow:      'hidden',
      marginBottom:  '10px',
      position:      'relative',
    }}>
      {/* left accent bar */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: '4px', zIndex: 1,
        background: leftBorder,
      }} />

      {/* AI config defaulted banner */}
      {s.ai_config_defaulted && (
        <div style={{
          background:  'var(--failed-color-a)',
          borderBottom: '1px solid var(--failed-color-b)',
          padding:     '6px 12px 6px 18px',
          display:     'flex',
          alignItems:  'center',
          gap:         '6px',
        }}>
          <span style={{ fontSize: '11px', color: 'var(--failed-color)' }}>⚠</span>
          <span style={{ fontSize: '10px', fontWeight: 600, color: 'var(--failed-color)' }}>
            AI config defaulted — review before backtesting
          </span>
        </div>
      )}

      {/* row 1: symbol + pills */}
      <div style={{ display:'flex', alignItems:'center', gap:'6px', padding:'12px 12px 0 18px' }}>
        {(st === 'active' || st === 'running') && (
          <span style={{
            width:'8px', height:'8px', borderRadius:'50%',
            background: st === 'running' ? 'var(--failed-color)' : 'var(--green)',
            flexShrink:0,
          }} />
        )}
        <span style={{ fontSize:'16px', fontWeight:700, letterSpacing:'-.01em', color:'var(--text)', whiteSpace:'nowrap', marginRight:'2px' }}>
          {s.symbol}
        </span>
        <HeaderPill variant="lev">{s.interval}</HeaderPill>
        <HeaderPill variant={statusPill.variant} style={{ marginLeft: 'auto' }}>
          {statusPill.label}
        </HeaderPill>
      </div>

      {/* row: name + ID */}
      <div style={{ padding:'5px 12px 0 18px', display:'flex', gap:'6px', alignItems:'center', flexWrap:'wrap' }}>
        <span style={{
          fontFamily:'JetBrains Mono,monospace', fontSize:'10px', fontWeight:700,
          background:'var(--bg3)', border:'1px solid var(--border)',
          borderRadius:'var(--pill-r)', padding:'2px 6px', color:'var(--muted)',
          textTransform:'uppercase', letterSpacing:'.04em',
        }}>{s.name}</span>
        <span style={{
          fontFamily:'JetBrains Mono,monospace', fontSize:'10px', fontWeight:500,
          border:'1px dashed var(--border-hi)', borderRadius:'var(--pill-r)',
          padding:'2px 6px', color:'var(--dim)',
        }}>{s.id}</span>
      </div>

      {/* row: route + best-run tag */}
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'5px 12px 4px 18px' }}>
        <div style={{ display:'flex', alignItems:'center', gap:'4px', flexShrink:0 }}>
          <HeaderPill variant="neutral">{shortModel}</HeaderPill>
          <span style={{ fontSize:'10px', color:'var(--dim)', fontFamily:'monospace', fontWeight:'bold' }}>→</span>
          <HeaderPill variant="neutral">simulated</HeaderPill>
        </div>
        <span
          onClick={hasRun && s.latest_run_id ? () => onViewRun(s.latest_run_id!) : undefined}
          style={{
            fontFamily:'JetBrains Mono,monospace', fontSize:'10px', fontWeight: hasRun ? 600 : 400,
            color: hasRun ? (Number(s.latest_run_total_pnl_pct ?? 0) >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--dim)',
            cursor: hasRun ? 'pointer' : 'default',
            whiteSpace: 'nowrap',
          }}
        >
          {bestTag}
        </span>
      </div>

      {/* running: progress bar */}
      {st === 'running' && (
        <div style={{ margin:'4px 18px 0', height:'2px', background:'var(--border)', borderRadius:'2px', overflow:'hidden' }}>
          <div style={{ height:'100%', width:'40%', background:'var(--blue)', borderRadius:'2px',
            animation:'progress-pulse 1.5s ease-in-out infinite' }} />
        </div>
      )}

      {/* data grid: best-run stats or provider info */}
      <DataGrid rows={[
        [
          { label: 'Trades',  value: <Mono>{s.latest_run_total_trades != null ? String(s.latest_run_total_trades) : '—'}</Mono> },
          { label: 'Win Rate', value: <Mono>{s.latest_run_win_rate != null ? Number(s.latest_run_win_rate).toFixed(1) + '%' : '—'}</Mono> },
          { label: 'Net P&L',  value: <MonoPnl v={s.latest_run_total_pnl_pct}>{fmtPnlPct(s.latest_run_total_pnl_pct)}</MonoPnl> },
        ],
        [
          { label: 'Provider',  value: <Mono style={{ textTransform:'capitalize' }}>{provider}</Mono> },
          { label: 'Model',     value: <Mono style={{ fontSize:'11px' }}>{model}</Mono> },
          { label: 'Interval',  value: <Mono>{s.interval}</Mono> },
        ],
      ]} />

      <ActionBand buttons={actionButtons} />
    </div>
  );
}

// ── tiny display helpers ───────────────────────────────────────────────────────

function Mono({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <span style={{
      fontFamily: 'JetBrains Mono, monospace',
      fontSize:   '13px',
      fontWeight: 600,
      color:      'var(--text)',
      ...style,
    }}>
      {children}
    </span>
  );
}

function MonoPnl({ v, children }: { v: number | null | undefined; children: React.ReactNode }) {
  const color = v == null ? 'var(--text)' : Number(v) >= 0 ? 'var(--green)' : 'var(--red)';
  return (
    <span style={{ fontFamily:'JetBrains Mono,monospace', fontSize:'13px', fontWeight:700, color }}>
      {children}
    </span>
  );
}

// ── main screen ────────────────────────────────────────────────────────────────

export function StrategiesScreen() {
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState<string | null>(null);
  const [runPanelId,    setRunPanelId]    = useState<string | null>(null);
  const [promotePanelId, setPromotePanelId] = useState<string | null>(null);
  const [filter,     setFilter]     = useState<'all' | 'active' | 'inactive'>('all');

  const load = useCallback(async () => {
    try {
      const data = await listStrategies();
      setStrategies(data);
      setError(null);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // poll while any run is active
  useEffect(() => {
    const hasActive = strategies.some(s =>
      s.latest_run_status === 'running' || s.latest_run_status === 'pending'
    );
    if (!hasActive) return;
    const t = setTimeout(load, 5000);
    return () => clearTimeout(t);
  }, [strategies, load]);

  const active   = strategies.filter(s => s.enabled && s.latest_run_status !== 'running' && s.latest_run_status !== 'pending');
  const running  = strategies.filter(s => s.latest_run_status === 'running' || s.latest_run_status === 'pending');
  const inactive = strategies.filter(s => !s.enabled);

  const displayed = filter === 'active'   ? [...running, ...active]
                  : filter === 'inactive' ? inactive
                  : strategies;

  const handleCancelRun = async (runId: string) => {
    try { await cancelRun(runId); load(); }
    catch (e) { alert('Cancel failed: ' + String(e)); }
  };

  const handleRunDone = (runId?: string) => {
    setRunPanelId(null);
    load();
    if (runId) navigate(`/simulation/${runId}`);
  };

  return (
    <>
      <style>{`
        @keyframes progress-pulse {
          0%,100% { opacity:.5; transform:translateX(-20%); }
          50%      { opacity:1;  transform:translateX(20%); }
        }
      `}</style>

      <TopBar
        title="Strategies"
        right={
          <span style={{
            background:'var(--bg3)', border:'1px solid var(--border)', borderRadius:'20px',
            padding:'4px 11px', fontFamily:'JetBrains Mono,monospace', fontSize:'12px', color:'var(--muted)',
          }}>
            {strategies.length} total
          </span>
        }
      />

      <FilterBar filters={[
        { label: 'All',      active: filter === 'all',      onClick: () => setFilter('all') },
        { label: 'Active',   active: filter === 'active',   onClick: () => setFilter('active') },
        { label: 'Inactive', active: filter === 'inactive', onClick: () => setFilter('inactive') },
      ]} />

      <SummaryBar cells={[
        { count: active.length + running.length, label: 'Active',   variant: 'live'   },
        { count: inactive.length,                label: 'Inactive', variant: 'closed' },
      ]} />

      <div className="scroll-area">
        {loading && (
          <p style={{ textAlign:'center', color:'var(--dim)', padding:'40px', fontSize:'13px' }}>Loading…</p>
        )}
        {error && (
          <p style={{ textAlign:'center', color:'var(--red)', padding:'20px', fontSize:'13px' }}>{error}</p>
        )}

        {/* Running section */}
        {running.length > 0 && filter !== 'inactive' && (
          <>
            <SectionHeader label="Running" count={running.length} variant="running" />
            {running.map(s => (
              <StratCard key={s.id} strategy={s}
                onRunOpen={setRunPanelId} onCancelRun={handleCancelRun}
                onPromoteOpen={setPromotePanelId} onViewRun={id => navigate(`/simulation/${id}`)} />
            ))}
          </>
        )}

        {/* Active section */}
        {(filter === 'all' || filter === 'active') && active.length > 0 && (
          <>
            <SectionHeader label="Active" count={active.length} variant="live" />
            {active.map(s => (
              <StratCard key={s.id} strategy={s}
                onRunOpen={setRunPanelId} onCancelRun={handleCancelRun}
                onPromoteOpen={setPromotePanelId} onViewRun={id => navigate(`/simulation/${id}`)} />
            ))}
          </>
        )}

        {/* Inactive section */}
        {(filter === 'all' || filter === 'inactive') && inactive.length > 0 && (
          <>
            <SectionHeader label="Inactive" count={inactive.length} variant="closed" />
            {inactive.map(s => (
              <StratCard key={s.id} strategy={s}
                onRunOpen={setRunPanelId} onCancelRun={handleCancelRun}
                onPromoteOpen={setPromotePanelId} onViewRun={id => navigate(`/simulation/${id}`)} />
            ))}
          </>
        )}

        {!loading && displayed.length === 0 && (
          <p style={{ textAlign:'center', color:'var(--dim)', padding:'40px', fontSize:'13px' }}>
            No strategies yet.
          </p>
        )}
      </div>

      {/* Run Backtest panel — Steps 8.1 + 8.2 combined */}
      {runPanelId && (
        <RunPanel
          strategyId={runPanelId}
          onClose={() => setRunPanelId(null)}
          onStarted={handleRunDone}
        />
      )}

      {/* Promote sheet — Step 8.4 */}
      {promotePanelId && (
        <PromoteSheet
          strategyId={promotePanelId}
          onClose={() => setPromotePanelId(null)}
          onDone={() => { setPromotePanelId(null); load(); }}
        />
      )}
    </>
  );
}
