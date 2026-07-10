import { useState, useEffect, useRef, useCallback, useMemo, CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { HeaderPill } from '../components/shared';
import { formatPct, formatPnl, pnlColor } from '../utils/pnl';
import { formatPrice, formatSize } from '../utils/precision';
import { formatRelative } from '../utils/datetime';
import {
  fetchStrategyTree, fetchTreePositions, fetchPositionOrders, fetchOrderDetail, api,
} from '../api';
import type { StrategyTreeItem, TreePosition, TreeOrder, OrderDetail, PendingOrder } from '../api';
import { useLivePnl, type PnlSnapshot } from '../hooks/useLivePnl';

const MONO = '"JetBrains Mono", monospace';
const CLOSED_STEP = 3;
const HOLD_MS = 500;

// ---- long-press hook ----

function useLongPress(onTap: () => void, onHold: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const held = useRef(false);

  const start = useCallback(() => {
    held.current = false;
    timer.current = setTimeout(() => { held.current = true; onHold(); }, HOLD_MS);
  }, [onHold]);

  const end = useCallback((fire: boolean) => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    if (fire && !held.current) onTap();
  }, [onTap]);

  return {
    onPointerDown: (e: React.PointerEvent) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      start();
    },
    onPointerUp:     () => end(true),
    onPointerLeave:  () => end(false),
    onPointerCancel: () => end(false),
    onContextMenu:   (e: React.MouseEvent) => e.preventDefault(),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onTap(); }
    },
  };
}

// ---- tiny shared pieces ----

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{
        fontSize: 9, fontWeight: 600, letterSpacing: '0.10em',
        textTransform: 'uppercase', color: 'var(--dim)',
      }}>
        {label}
      </span>
      <span style={{
        fontFamily: MONO, fontSize: 13, fontWeight: 700,
        lineHeight: 1, color: color ?? 'var(--text)',
      }}>
        {value}
      </span>
    </div>
  );
}

function KV({ k, v, vColor }: { k: string; v: string; vColor?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '3px 0', fontSize: 12.5 }}>
      <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{k}</span>
      <span style={{ fontFamily: MONO, textAlign: 'right', color: vColor ?? 'var(--text)', wordBreak: 'break-all' }}>{v}</span>
    </div>
  );
}

type DRSeg = { text: string; color?: string };

function DR({ label, segs, sep = ' · ' }: { label: string; segs: DRSeg[]; sep?: string }) {
  if (segs.length === 0) return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '3px 0', fontSize: 12.5 }}>
      <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{label}</span>
      <span style={{ fontFamily: MONO, textAlign: 'right' }}>
        {segs.map((s, i) => (
          <span key={i}>
            {i > 0 && <span style={{ color: 'var(--dim)' }}>{sep}</span>}
            <span style={{ color: s.color ?? 'var(--text)' }}>{s.text}</span>
          </span>
        ))}
      </span>
    </div>
  );
}

function SectionLabel({ text }: { text: string }) {
  return (
    <div style={{
      fontSize: 10, letterSpacing: '.06em', textTransform: 'uppercase',
      color: 'var(--dim)', padding: '7px 4px 3px',
    }}>
      {text}
    </div>
  );
}

function Spinner() {
  return <div style={{ color: 'var(--muted)', fontSize: 12, padding: '6px 4px', textAlign: 'center' }}>Loading…</div>;
}

// ---- filters/sort dropdown ----

function Dropdown({ label, active, children }: { label: string; active: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative', flexShrink: 0 }}>
      <span onClick={() => setOpen(o => !o)} style={treeChip(active)}>
        {label}{open ? ' ▴' : ' ▾'}
      </span>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 20,
          background: 'var(--bg2)', border: '1px solid var(--border)',
          borderRadius: 10, padding: 8,
          boxShadow: '0 4px 16px rgba(0,0,0,.14)',
          display: 'flex', flexDirection: 'column', gap: 6,
          minWidth: 150,
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

// ---- filter/sort helpers ----

type SortKey = 'symbol' | 'last_opened' | 'activity';
type SortDir = 'asc' | 'desc';

const SS_SYMBOL   = 'matp_tree_symbol';
const SS_STATUS   = 'matp_tree_status';
const SS_OPENPOS  = 'matp_tree_openpos';
const SS_TYPE     = 'matp_tree_type';
const SS_SORT_KEY = 'matp_tree_sortkey';
const SS_SORT_DIR = 'matp_tree_sortdir';

const DEFAULT_SORT_KEY: SortKey = 'activity';
const DEFAULT_SORT_DIR: SortDir = 'desc';

function ssGet(key: string, fallback: string): string {
  try { return sessionStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function ssSet(key: string, val: string) {
  try { sessionStorage.setItem(key, val); } catch {}
}

function nullSafeCompare(a: number | null, b: number | null, dir: SortDir): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return dir === 'asc' ? a - b : b - a;
}

function hasOpenFor(s: StrategyTreeItem, livePnl: PnlSnapshot | null): boolean {
  const livePids = livePnl?.strategies[s.id]?.position_ids;
  return livePids ? livePids.length > 0 : s.open_positions_count > 0;
}

// "Last Change" grouping: strategies with open positions first (by most-recently-opened),
// then active strategies (by most-recent signal), then inactive strategies (same tiebreak).
// Group order is fixed; sortDir only flips the recency comparison within each group.
function lastChangeBucket(s: StrategyTreeItem, livePnl: PnlSnapshot | null): 0 | 1 | 2 {
  if (hasOpenFor(s, livePnl)) return 0;
  return s.enabled ? 1 : 2;
}

// ---- page ----

export default function StrategyTreePage() {
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState<StrategyTreeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const livePnl = useLivePnl();

  // filter state — persisted in sessionStorage
  const [filterSymbol,  setFilterSymbolRaw]  = useState<string>(() => ssGet(SS_SYMBOL,   'all'));
  const [filterStatus,  setFilterStatusRaw]  = useState<string>(() => ssGet(SS_STATUS,   'all'));
  const [filterOpenPos, setFilterOpenPosRaw] = useState<string>(() => ssGet(SS_OPENPOS,  'all'));
  const [filterType,    setFilterTypeRaw]    = useState<string>(() => ssGet(SS_TYPE,     'all'));
  const [sortKey,       setSortKeyRaw]       = useState<SortKey>(() => ssGet(SS_SORT_KEY, DEFAULT_SORT_KEY) as SortKey);
  const [sortDir,       setSortDirRaw]       = useState<SortDir>(() => ssGet(SS_SORT_DIR, DEFAULT_SORT_DIR) as SortDir);

  const setFilterSymbol  = (v: string)  => { ssSet(SS_SYMBOL,   v); setFilterSymbolRaw(v);  };
  const setFilterStatus  = (v: string)  => { ssSet(SS_STATUS,   v); setFilterStatusRaw(v);  };
  const setFilterOpenPos = (v: string)  => { ssSet(SS_OPENPOS,  v); setFilterOpenPosRaw(v); };
  const setFilterType    = (v: string)  => { ssSet(SS_TYPE,     v); setFilterTypeRaw(v);    };
  const setSortKey       = (v: SortKey) => { ssSet(SS_SORT_KEY, v); setSortKeyRaw(v);       };
  const setSortDir       = (v: SortDir) => { ssSet(SS_SORT_DIR, v); setSortDirRaw(v);       };

  const loadStrategies = useCallback(() => {
    return fetchStrategyTree()
      .then(setStrategies)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadStrategies(); }, [loadStrategies]);

  const uniqueSymbols = useMemo(
    () => [...new Set(strategies.map(s => s.symbol))].sort(),
    [strategies],
  );

  const filtered = useMemo(() => strategies.filter(s => {
    if (filterSymbol !== 'all' && s.symbol !== filterSymbol) return false;
    if (filterStatus !== 'all') {
      if (filterStatus === 'active'   && !s.enabled) return false;
      if (filterStatus === 'inactive' &&  s.enabled) return false;
    }
    if (filterOpenPos === 'hasopen' && !hasOpenFor(s, livePnl)) return false;
    if (filterType !== 'all') {
      const src = s.strategy_source ?? 'tradingview';
      if (filterType === 'tradingview' && src !== 'tradingview') return false;
      if (filterType === 'ai'          && src !== 'ai_engine')   return false;
      if (filterType === 'social'      && src !== 'social')      return false;
      if (filterType === 'internal'    && src !== 'internal')    return false;
    }
    return true;
  }), [strategies, filterSymbol, filterStatus, filterOpenPos, filterType, livePnl]);

  const sorted = useMemo(() => [...filtered].sort((a, b) => {
    if (sortKey === 'symbol') {
      const cmp = a.symbol.localeCompare(b.symbol);
      return sortDir === 'asc' ? cmp : -cmp;
    }

    if (sortKey === 'activity') {
      const ba = lastChangeBucket(a, livePnl);
      const bb = lastChangeBucket(b, livePnl);
      if (ba !== bb) return ba - bb;
      // bucket 0 (open): most-recently-opened first. buckets 1/2: most-recent signal first.
      const field = ba === 0 ? 'last_position_opened_at' : 'last_activity_at';
      const aT = a[field] ? new Date(a[field]!).getTime() : null;
      const bT = b[field] ? new Date(b[field]!).getTime() : null;
      return nullSafeCompare(aT, bT, sortDir);
    }

    const aT = a.last_position_opened_at ? new Date(a.last_position_opened_at).getTime() : null;
    const bT = b.last_position_opened_at ? new Date(b.last_position_opened_at).getTime() : null;
    return nullSafeCompare(aT, bT, sortDir);
  }), [filtered, sortKey, sortDir, livePnl]);

  const handleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }, [sortKey, sortDir]);

  const handleReset = useCallback(() => {
    setFilterSymbol('all');
    setFilterStatus('all');
    setFilterOpenPos('all');
    setFilterType('all');
    setSortKey(DEFAULT_SORT_KEY);
    setSortDir(DEFAULT_SORT_DIR);
    try {
      [SS_SYMBOL, SS_STATUS, SS_OPENPOS, SS_TYPE, SS_SORT_KEY, SS_SORT_DIR]
        .forEach(k => sessionStorage.removeItem(k));
    } catch {}
  }, []);

  const anyFilterActive = filterSymbol !== 'all' || filterStatus !== 'all' ||
    filterOpenPos !== 'all' || filterType !== 'all';
  const anySortActive = sortKey !== DEFAULT_SORT_KEY || sortDir !== DEFAULT_SORT_DIR;
  const anyNonDefault = anyFilterActive || anySortActive;

  // cycle helpers
  const cycleStatus = () => {
    const next: Record<string, string> = { all: 'active', active: 'inactive', inactive: 'all' };
    setFilterStatus(next[filterStatus] ?? 'all');
  };
  const cycleType = () => {
    const order = ['all', 'tradingview', 'ai', 'social', 'internal'];
    const i = order.indexOf(filterType);
    setFilterType(order[(i + 1) % order.length]);
  };
  const toggleOpenPos = () => setFilterOpenPos(filterOpenPos === 'all' ? 'hasopen' : 'all');

  const statusLabel: Record<string, string> = { all: 'All Status', active: 'Active', inactive: 'Inactive' };
  const typeLabel:   Record<string, string> = {
    all: 'All Type', tradingview: 'TradingView', ai: 'AI', social: 'Social', internal: 'Internal',
  };
  const dirArrow = (key: SortKey) => sortKey === key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

  return (
    <div style={{ maxWidth: 460, margin: '0 auto', paddingBottom: 70 }}>
      {/* Filter + sort bar */}
      <div style={{
        display: 'flex', gap: 6, padding: '8px 10px', alignItems: 'center',
        flexWrap: 'wrap', flexShrink: 0,
        borderBottom: '1px solid var(--border)',
      }}>
        {/* Add strategy */}
        <button
          type="button"
          aria-label="Add strategy"
          onClick={() => navigate('/strategies', { state: { openAdd: true } })}
          style={addBtn}
        >
          ＋
        </button>

        {/* Filters dropdown */}
        <Dropdown label="Filters" active={anyFilterActive}>
          <select
            value={filterSymbol}
            onChange={e => setFilterSymbol(e.target.value)}
            style={{
              background:   filterSymbol !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
              border:       `1px solid ${filterSymbol !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
              borderRadius: '20px', padding: '5px 12px',
              fontSize: 10, fontWeight: 500,
              color: filterSymbol !== 'all' ? 'var(--blue)' : 'var(--muted)',
              cursor: 'pointer', outline: 'none',
            }}
          >
            <option value="all">All Symbols</option>
            {uniqueSymbols.map(sym => <option key={sym} value={sym}>{sym}</option>)}
          </select>

          <span onClick={cycleStatus} style={treeChip(filterStatus !== 'all')}>
            {statusLabel[filterStatus]}
          </span>

          <span onClick={toggleOpenPos} style={treeChip(filterOpenPos !== 'all')}>
            {filterOpenPos === 'all' ? 'All' : 'Has Open'}
          </span>

          <span onClick={cycleType} style={treeChip(filterType !== 'all')}>
            {typeLabel[filterType] ?? 'All Type'}
          </span>
        </Dropdown>

        {/* Sort dropdown */}
        <Dropdown label="Sort" active={anySortActive}>
          <span onClick={() => handleSort('symbol')}      style={treeChip(sortKey === 'symbol')}>
            {`Symbol${dirArrow('symbol')}`}
          </span>
          <span onClick={() => handleSort('last_opened')} style={treeChip(sortKey === 'last_opened')}>
            {`Opened${dirArrow('last_opened')}`}
          </span>
          <span onClick={() => handleSort('activity')}    style={treeChip(sortKey === 'activity')}>
            {`Last Change${dirArrow('activity')}`}
          </span>
        </Dropdown>

        {/* Reset */}
        {anyNonDefault && (
          <span onClick={handleReset} style={treeChip(false, true)}>Reset</span>
        )}
      </div>

      {/* Cards */}
      <div style={{ padding: '14px 10px 0' }}>
        {loading && <div style={{ padding: 24, color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>Loading…</div>}
        {error && <div style={{ padding: 24, color: 'var(--red)', fontSize: 13 }}>{error}</div>}
        {!loading && !error && sorted.length === 0 && (
          <div style={{ padding: 24, color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>
            {strategies.length === 0 ? 'No strategies' : 'No strategies match the current filters'}
          </div>
        )}
        {sorted.map(s => <StrategyCard key={s.id} strategy={s} onL1Refresh={loadStrategies} livePnl={livePnl} />)}
      </div>
    </div>
  );
}

// ---- strategy card ----

type ExpandState = 'collapsed' | 'open' | 'all';

function StrategyCard({ strategy: s, onL1Refresh, livePnl }: { strategy: StrategyTreeItem; onL1Refresh: () => void; livePnl: PnlSnapshot | null }) {
  const livePids = livePnl?.strategies[s.id]?.position_ids;
  const hasOpen = livePids ? livePids.length > 0 : s.open_positions_count > 0;
  const hasPending = s.pending_orders.length > 0;
  const pidKey = livePids ? [...livePids].sort().join(',') : null;
  const isStopped = !s.enabled;
  const navigate = useNavigate();

  const [expandState, setExpandState] = useState<ExpandState>('collapsed');
  const [openPositions, setOpenPositions] = useState<TreePosition[] | null>(null);
  const [allPositions, setAllPositions]   = useState<TreePosition[] | null>(null);
  const [loadingPos, setLoadingPos] = useState(false);
  const [closedShown, setClosedShown] = useState(CLOSED_STEP);
  const [actionInFlight, setActionInFlight] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [detailData, setDetailData] = useState<Record<string, any> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const lastRefetchedKey = useRef<string | null>(null);

  const doFetchOpen = useCallback(async () => {
    if (openPositions !== null) return;
    setLoadingPos(true);
    try { setOpenPositions(await fetchTreePositions(s.id, 'open')); }
    catch { setOpenPositions([]); }
    finally { setLoadingPos(false); }
  }, [s.id, openPositions]);

  const doFetchAll = useCallback(async () => {
    if (allPositions !== null) return;
    setLoadingPos(true);
    try { setAllPositions(await fetchTreePositions(s.id, 'all')); }
    catch { setAllPositions([]); }
    finally { setLoadingPos(false); }
  }, [s.id, allPositions]);

  // Refetch open positions when the live snapshot reports a position-set membership change.
  // Keyed on pidKey (sorted id join) so it fires once per membership change, not once per tick.
  useEffect(() => {
    if (pidKey === null || expandState === 'collapsed' || loadingPos) return;
    if (pidKey === lastRefetchedKey.current) return;

    const currentIds = (
      expandState === 'open' ? (openPositions ?? []) : (allPositions ?? []).filter(p => p.status === 'open')
    ).map(p => p.id).sort().join(',');

    lastRefetchedKey.current = pidKey;
    if (pidKey === currentIds) return;

    const scope = expandState === 'all' ? 'all' : 'open';
    setLoadingPos(true);
    fetchTreePositions(s.id, scope)
      .then(fresh => { if (scope === 'open') setOpenPositions(fresh); else setAllPositions(fresh); })
      .catch(() => {})
      .finally(() => setLoadingPos(false));
  }, [pidKey, expandState, loadingPos, openPositions, allPositions, s.id]);

  const handleTap = useCallback(() => {
    if (expandState === 'collapsed') {
      if (hasOpen) {
        doFetchOpen();
        setExpandState('open');
      } else {
        doFetchAll();
        setExpandState('all');
      }
    } else if (expandState === 'open') {
      doFetchAll();
      setExpandState('all');
    } else {
      setExpandState('collapsed');
    }
  }, [expandState, hasOpen, doFetchOpen, doFetchAll]);

  const handleHold = useCallback(() => {
    setExpandState('collapsed');
  }, []);

  const pressHandlers = useLongPress(handleTap, handleHold);

  const handlePauseResume = useCallback(async () => {
    setActionError(null);
    if (isStopped) {
      setActionInFlight(true);
      try {
        await api.post(`/strategies/${s.id}/start`);
        onL1Refresh();
      } catch (e: any) {
        setActionError(e.message);
      } finally {
        setActionInFlight(false);
      }
    } else {
      if (!window.confirm('Pausing closes all open positions for this strategy. Continue?')) return;
      setActionInFlight(true);
      try {
        await api.post(`/strategies/${s.id}/stop`);
        setOpenPositions(null);
        setAllPositions(null);
        setExpandState('collapsed');
        onL1Refresh();
      } catch (e: any) {
        setActionError(e.message);
      } finally {
        setActionInFlight(false);
      }
    }
  }, [isStopped, s.id, onL1Refresh]);

  const handlePositionClose = useCallback(async () => {
    setOpenPositions(null);
    setAllPositions(null);
    if (expandState === 'open' || expandState === 'all') {
      const scope = expandState === 'all' ? 'all' : 'open';
      setLoadingPos(true);
      try {
        const fresh = await fetchTreePositions(s.id, scope);
        if (scope === 'open') setOpenPositions(fresh);
        else setAllPositions(fresh);
      } catch {
        if (expandState === 'open') setOpenPositions([]);
        else setAllPositions([]);
      } finally {
        setLoadingPos(false);
      }
    }
    onL1Refresh();
  }, [expandState, s.id, onL1Refresh]);

  const handleToggleDetail = useCallback(async () => {
    const opening = !showDetail;
    setShowDetail(opening);
    if (opening && detailData === null && !detailLoading) {
      setDetailLoading(true);
      try {
        const data = await api.get<Record<string, any>>(`/strategies/${s.id}`);
        setDetailData(data);
      } catch {
        setDetailData({});
      } finally {
        setDetailLoading(false);
      }
    }
  }, [showDetail, detailData, detailLoading, s.id]);

  const handleEdit = useCallback(() => {
    navigate('/strategies', { state: { editId: s.id } });
  }, [navigate, s.id]);

  // derive visible lists
  const shownOpen = expandState === 'open'
    ? (openPositions ?? [])
    : expandState === 'all'
      ? (allPositions ?? []).filter(p => p.status === 'open')
      : [];

  const allClosed     = expandState === 'all' ? (allPositions ?? []).filter(p => p.status === 'closed') : [];
  const shownClosed   = allClosed.slice(0, closedShown);
  const hasMoreClosed = allClosed.length > closedShown;

  return (
    <div style={{
      background: 'var(--bg2)', border: '1px solid var(--border)',
      borderRadius: 11, marginBottom: 11, overflow: 'hidden',
      position: 'relative', boxShadow: '0 1px 2px rgba(20,30,50,.04)',
    }}>
      {/* Left accent bar */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: 4,
        borderRadius: '11px 0 0 11px',
        background: isStopped ? 'var(--stopped-bar)' : 'var(--blue)', zIndex: 1,
      }} />

      {/* Top-right icons */}
      <div style={{ position: 'absolute', top: 0, right: 2, display: 'flex', alignItems: 'flex-start', zIndex: 2 }}>
        <button
          type="button"
          disabled={actionInFlight}
          aria-label={isStopped ? 'Resume' : 'Pause'}
          style={{ ...iconBtnSm, cursor: actionInFlight ? 'default' : 'pointer', opacity: actionInFlight ? 0.5 : 1 }}
          onClick={handlePauseResume}
        >
          {actionInFlight ? '…' : (isStopped ? '▶' : '⏸')}
        </button>
        <button
          type="button"
          aria-label="Details"
          style={{ ...iconBtnLg, margin: '6px 9px 6px 0', cursor: 'pointer', opacity: showDetail ? 1 : 0.7 }}
          onClick={handleToggleDetail}
        >
          ⓘ
        </button>
      </div>

      {/* Rows 1 + 2 + 3 (tap/long-press target) */}
      <div
        role="button"
        tabIndex={0}
        aria-label={`${s.name} — tap to expand`}
        style={{ padding: '9px 6px 9px 12px', paddingRight: 84, cursor: 'pointer', userSelect: 'none', WebkitUserSelect: 'none' }}
        {...pressHandlers}
      >
        {/* Row 1 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
          {hasOpen ? <GreenDot /> : hasPending && <YellowDot />}
          <HeaderPill
            variant="neutral"
            style={{ fontWeight: 700, background: 'var(--bg3)', color: 'var(--text)', borderColor: 'var(--border-hi)' }}
          >
            {s.symbol}
          </HeaderPill>
          <span style={{ fontWeight: 700, fontSize: 14, letterSpacing: '-.01em', color: isStopped ? 'var(--muted)' : 'var(--text)' }}>
            {s.name}
          </span>
        </div>

        {/* Row 2 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
          {s.strategy_source === 'ai_engine' && <HeaderPill variant="ai">AI</HeaderPill>}
          {s.strategy_source === 'ai_engine' && s.ai_llm_provider && (
            <LlmChip>{s.ai_llm_provider}{s.ai_llm_model ? ` / ${s.ai_llm_model}` : ''}</LlmChip>
          )}
          <AccountChip>{s.account_label}</AccountChip>
          {isStopped && <StopChip reason={s.stop_reason} />}
          {actionError && (
            <span style={{ fontSize: 10.5, color: 'var(--red)' }}>{actionError}</span>
          )}
        </div>

        {/* Row 3 */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 18, marginTop: 6, flexWrap: 'nowrap' }}>
          <Metric label="Allocation" value={Number(s.capital_allocation).toFixed(1)} />
          <Metric label="Total Return" value={formatPct(s.total_return)} color={pnlColor(s.total_return)} />
          {hasOpen && (() => {
            const liveOpenPnl = livePnl?.strategies[s.id]?.open_pnl ?? s.open_pnl;
            return <Metric label="Open PnL" value={formatPnl(liveOpenPnl)} color={pnlColor(liveOpenPnl)} />;
          })()}
        </div>
      </div>

      {/* Detail panel (ⓘ) */}
      {showDetail && (
        <div style={{
          margin: '0 9px 9px 13px', padding: '8px 10px',
          borderTop: '1px solid var(--border)',
          background: 'var(--bg3)', borderRadius: 8,
        }}>
          {detailLoading && <Spinner />}
          {!detailLoading && (
            <>
              <KV k="Interval"        v={detailData?.interval ?? '—'} />
              <KV k="Default leverage" v={detailData ? `${detailData.default_leverage}×` : '—'} />
              <KV k="Margin / trade"  v={detailData ? `$${Number(detailData.margin_per_trade).toFixed(2)}` : '—'} />
              <KV k="Max drawdown"    v={detailData ? `${detailData.max_drawdown_pct}%` : '—'} />
              <KV k="Committed"       v={detailData ? `$${Number(detailData.initial_allocation ?? detailData.capital_allocation).toFixed(2)}` : '—'} />
              {detailData?.ai_llm_provider && (
                <KV k="LLM" v={`${detailData.ai_llm_provider}${detailData.ai_llm_model ? ` / ${detailData.ai_llm_model}` : ''}`} />
              )}
              {detailData?.last_signal_at && (
                <KV k="Last signal" v={formatRelative(detailData.last_signal_at)} />
              )}
              <div style={{ marginTop: 10 }}>
                <button
                  type="button"
                  onClick={handleEdit}
                  style={{
                    display: 'block', width: '100%',
                    padding: '7px 10px', fontSize: 12, fontFamily: 'inherit',
                    color: 'var(--blue)', background: 'var(--blue-a)',
                    border: '1px solid var(--blue-b)', borderRadius: 8,
                    cursor: 'pointer', textAlign: 'center',
                  }}
                >
                  ✎ Edit strategy
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Strategy track */}
      {expandState !== 'collapsed' && (
        <div style={{ margin: '2px 9px 11px 13px', padding: '2px 4px 6px' }}>
          {loadingPos && <Spinner />}

          {shownOpen.length > 0 && (
            <>
              <SectionLabel text="Open Positions" />
              {shownOpen.map(p => (
                <PositionCard key={p.id} position={p} stratLabel={s.account_label} stratExchange={s.account_exchange} onClose={handlePositionClose} livePnl={livePnl} />
              ))}
            </>
          )}

          {s.pending_orders.length > 0 && (
            <>
              <SectionLabel text="Pending Orders" />
              {s.pending_orders.map(o => (
                <PendingOrderCard key={o.id} order={o} />
              ))}
            </>
          )}

          {expandState === 'all' && !loadingPos && (
            <>
              {allClosed.length > 0 && (
                <>
                  <SectionLabel text="Closed Positions" />
                  {shownClosed.map(p => (
                    <PositionCard key={p.id} position={p} stratLabel={s.account_label} stratExchange={s.account_exchange} onClose={handlePositionClose} livePnl={livePnl} />
                  ))}
                </>
              )}
              {shownOpen.length === 0 && allClosed.length === 0 && (
                <div style={{ color: 'var(--muted)', fontSize: 12, padding: '8px 4px' }}>No positions</div>
              )}
            </>
          )}

          {/* Bottom buttons */}
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            {expandState === 'all' && hasMoreClosed && (
              <button type="button" onClick={() => setClosedShown(n => n + CLOSED_STEP)} style={loadMoreBtn}>
                Load more
              </button>
            )}
            <button
              type="button"
              onClick={() => setExpandState('collapsed')}
              style={{ ...collapseBtn, flex: expandState === 'all' && hasMoreClosed ? '0 0 auto' : 1 }}
            >
              Collapse
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- position card ----

const CLOSE_REASON_LABELS: Record<string, string> = {
  manual_close:        'Manual close',
  signal_flat:          'Signal: flat',
  signal_close:         'Signal close',
  flatten_on_disable:   'Strategy stopped',
  flip_close:           'Position flipped',
  Liquidated:           'Liquidated',
  'Closed on exchange': 'Closed on exchange',
};

function formatCloseReason(reason: string | null): string {
  if (!reason) return 'Unknown';
  return CLOSE_REASON_LABELS[reason] ?? reason.replace(/_/g, ' ');
}

function fmtMoney(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 10_000)    return `$${(v / 1_000).toFixed(1)}k`;
  return `$${Math.round(v)}`;
}

function fmtPnlPct(pnl: number | null | undefined, margin: number): string {
  if (pnl == null || !margin) return '';
  const pct = (pnl / margin) * 100;
  const sign = pct >= 0 ? '+' : '';
  return ` (${sign}${pct.toFixed(2)}%)`;
}

type PosState = 'header' | 'details' | 'orders';

function PositionCard({
  position: p,
  stratLabel,
  stratExchange,
  onClose,
  livePnl,
}: {
  position: TreePosition;
  stratLabel: string;
  stratExchange: string;
  onClose: () => void;
  livePnl: PnlSnapshot | null;
}) {
  const [posState, setPosState] = useState<PosState>('header');
  const [orders, setOrders] = useState<TreeOrder[] | null>(null);
  const [loadingOrders, setLoadingOrders] = useState(false);
  const [closeInFlight, setCloseInFlight] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);

  const isOpen = p.status === 'open';
  const symbol = `${p.base_asset}-${p.quote_asset}`;
  const diffAcct = p.account_label !== stratLabel || p.account_exchange !== stratExchange;

  const posSnap = isOpen ? livePnl?.positions[p.id] : undefined;
  const displayMarkPrice = posSnap?.mark_price ?? p.mark_price;
  const displayUnrealizedPnl = posSnap !== undefined ? posSnap.unrealized_pnl : p.unrealized_pnl;
  const displayLiqPrice = posSnap?.liquidation_price ?? p.liquidation_price;

  const handleTap = useCallback(() => {
    if (posState === 'header') {
      setPosState('details');
    } else if (posState === 'details') {
      if (orders === null) {
        setLoadingOrders(true);
        fetchPositionOrders(p.id)
          .then(o => setOrders(o))
          .catch(() => setOrders([]))
          .finally(() => setLoadingOrders(false));
      }
      setPosState('orders');
    } else {
      setPosState('header');
    }
  }, [posState, orders, p.id]);

  const handleClosePosition = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm(`Close this ${p.side} position?`)) return;
    setCloseInFlight(true);
    setCloseError(null);
    try {
      await api.post(`/positions/${p.id}/close`);
      onClose();
    } catch (err: any) {
      setCloseError(err.message);
    } finally {
      setCloseInFlight(false);
    }
  }, [p.id, p.side, onClose]);

  const pnlVal = isOpen ? displayUnrealizedPnl : p.realized_pnl;

  const priceSpec = { price_mode: p.price_mode, price_tick: p.price_tick, price_sigfigs: p.price_sigfigs };
  const sizeSpec  = { size_dp: p.size_dp };

  const margin = Number(p.entry_price) * Number(p.size) / (Number(p.leverage) || 1);
  const notionalValue = isOpen
    ? Number(p.size) * Number(displayMarkPrice)
    : Number(p.size) * Number(p.closing_price ?? p.entry_price);
  const notionalStr = notionalValue > 0 ? `≈${fmtMoney(notionalValue)}` : '';
  const pnlPctSuffix  = fmtPnlPct(pnlVal, margin);
  const unrealizedPct = fmtPnlPct(displayUnrealizedPnl, margin);
  const realizedPct   = fmtPnlPct(p.realized_pnl, margin);

  const priceGridCols = isOpen
    ? [
        { label: 'Open', value: formatPrice(symbol, p.entry_price,    priceSpec), color: 'var(--muted)' },
        { label: 'Mark', value: formatPrice(symbol, displayMarkPrice,  priceSpec), color: 'var(--green)' },
        { label: 'SL',   value: p.sl_price      != null ? formatPrice(symbol, p.sl_price,      priceSpec) : '—', color: 'var(--muted)' },
        { label: 'TP',   value: p.tp_price      != null ? formatPrice(symbol, p.tp_price,      priceSpec) : '—', color: 'var(--muted)' },
        { label: 'Liq',  value: displayLiqPrice != null ? formatPrice(symbol, displayLiqPrice, priceSpec) : '—', color: 'var(--muted)' },
      ]
    : [
        { label: 'Open',  value: formatPrice(symbol, p.entry_price,    priceSpec), color: 'var(--muted)' },
        { label: 'Close', value: p.closing_price != null ? formatPrice(symbol, p.closing_price, priceSpec) : '—', color: 'var(--muted)' },
        { label: 'SL',    value: p.sl_price      != null ? formatPrice(symbol, p.sl_price,      priceSpec) : '—', color: 'var(--muted)' },
        { label: 'TP',    value: p.tp_price      != null ? formatPrice(symbol, p.tp_price,      priceSpec) : '—', color: 'var(--muted)' },
      ];

  return (
    <div style={{
      background: 'var(--bg2)', border: '1px solid var(--border)',
      borderRadius: 9, marginBottom: 8, overflow: 'hidden',
      boxShadow: '0 1px 1px rgba(20,30,50,.03)',
    }}>
      {/* Position header */}
      <div
        role="button"
        tabIndex={0}
        onClick={handleTap}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleTap(); } }}
        style={{ display: 'flex', flexDirection: 'column', padding: '6px 10px', cursor: 'pointer', userSelect: 'none', gap: 3 }}
      >
        {/* top row */}
        <div style={{ display: 'flex', alignItems: 'center', minHeight: 30, gap: 7 }}>
          {/* side cell: side pill + leverage pill stacked, centered */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, flexShrink: 0 }}>
            <HeaderPill variant={p.side === 'long' ? 'long' : 'short'}>
              {p.side === 'long' ? 'LONG' : 'SHORT'}
            </HeaderPill>
            <HeaderPill variant="neutral" style={{ fontSize: 9, padding: '1px 5px' }}>
              {p.leverage}×
            </HeaderPill>
          </div>
          {/* asset / size / notional — hidden when expanded */}
          {posState === 'header' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <span style={{ fontFamily: MONO, fontSize: 13, color: 'var(--text)' }}>
                <span style={{ fontWeight: 600 }}>{p.base_asset}</span>
                {' '}
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>{formatSize(symbol, p.size, sizeSpec)}</span>
              </span>
              {notionalStr && (
                <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--dim)' }}>
                  {notionalStr}
                </span>
              )}
            </div>
          ) : (
            <span style={{ fontFamily: MONO, fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>
              {p.base_asset}-{p.quote_asset}
            </span>
          )}
          {diffAcct && <DiffChip label={p.account_label} />}
          <span style={{ flex: 1 }} />
          {/* PnL cell — hidden when expanded */}
          {posState === 'header' && pnlVal != null && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
              <span style={{ fontFamily: MONO, fontSize: 12, color: pnlColor(pnlVal) }}>
                {formatPnl(pnlVal)}
              </span>
              {pnlPctSuffix && (
                <span style={{ fontFamily: MONO, fontSize: 10, color: pnlColor(pnlVal) }}>
                  {pnlPctSuffix.trim()}
                </span>
              )}
            </div>
          )}
          {isOpen && (
            <button
              type="button"
              disabled={closeInFlight}
              aria-label="Close position"
              style={{ ...closeIcBtn, cursor: closeInFlight ? 'default' : 'pointer', opacity: closeInFlight ? 0.5 : 1 }}
              onClick={handleClosePosition}
            >
              {closeInFlight ? '…' : '✕'}
            </button>
          )}
        </div>
        {/* two-row price grid — hidden when expanded */}
        {posState === 'header' && (
          <div style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${priceGridCols.length}, auto)`,
            gap: '0 10px',
            fontFamily: MONO, fontSize: 11, letterSpacing: 0,
          }}>
            {priceGridCols.map(c => (
              <span key={c.label} style={{ color: 'var(--dim)' }}>{c.label}</span>
            ))}
            {priceGridCols.map(c => (
              <span key={c.label + '_v'} style={{ color: c.color }}>{c.value}</span>
            ))}
          </div>
        )}
      </div>

      {/* Detail panel */}
      {posState !== 'header' && (
        <div style={{ padding: '4px 12px 8px', borderTop: '1px solid var(--border)' }}>
          {isOpen ? (
            <>
              {displayUnrealizedPnl != null && (
                <DR label="PnL" segs={[{ text: `${formatPnl(displayUnrealizedPnl)}${unrealizedPct}`, color: pnlColor(displayUnrealizedPnl) }]} />
              )}
              <DR label="Levels" segs={[
                { text: `Liq ${displayLiqPrice != null ? formatPrice(symbol, displayLiqPrice, priceSpec) : '—'}` },
                { text: `SL ${p.sl_price != null ? formatPrice(symbol, p.sl_price, priceSpec) : '—'}` },
                { text: `TP ${p.tp_price != null ? formatPrice(symbol, p.tp_price, priceSpec) : '—'}` },
              ]} />
              <DR label="Price" sep=" → " segs={[
                { text: `Entry ${formatPrice(symbol, p.entry_price, priceSpec)}` },
                { text: `Mark ${formatPrice(symbol, displayMarkPrice, priceSpec)}` },
              ]} />
              <DR label="Size" segs={[
                { text: formatSize(symbol, p.size, sizeSpec) },
                ...(notionalStr ? [{ text: notionalStr }] : []),
              ]} />
              <DR label="Margin" segs={[
                { text: fmtMoney(margin) },
                { text: `${p.leverage}×` },
              ]} />
              <DR label="Opened" segs={[{ text: formatRelative(p.opened_at) }]} />
            </>
          ) : (
            <>
              <DR label="PnL" segs={[{ text: `Realized ${formatPnl(p.realized_pnl)}${realizedPct}`, color: pnlColor(p.realized_pnl) }]} />
              {(p.sl_price != null || p.tp_price != null) && (
                <DR label="Levels" segs={[
                  ...(p.sl_price != null ? [{ text: `SL ${formatPrice(symbol, p.sl_price, priceSpec)}` }] : []),
                  ...(p.tp_price != null ? [{ text: `TP ${formatPrice(symbol, p.tp_price, priceSpec)}` }] : []),
                ]} />
              )}
              <DR label="Price" sep=" → " segs={[
                { text: `Entry ${formatPrice(symbol, p.entry_price, priceSpec)}` },
                ...(p.closing_price != null ? [{ text: `Close ${formatPrice(symbol, p.closing_price, priceSpec)}` }] : []),
              ]} />
              <DR label="Size" segs={[
                { text: formatSize(symbol, p.size, sizeSpec) },
                ...(notionalStr ? [{ text: notionalStr }] : []),
              ]} />
              <DR label="Margin" segs={[
                { text: fmtMoney(margin) },
                { text: `${p.leverage}×` },
              ]} />
              <DR label="Close reason" segs={[{ text: formatCloseReason(p.close_reason) }]} />
              <DR label="Time" segs={[
                { text: `Opened ${formatRelative(p.opened_at)}` },
                ...(p.closed_at ? [{ text: `Closed ${formatRelative(p.closed_at)}` }] : []),
              ]} />
            </>
          )}
        </div>
      )}

      {/* Orders track */}
      {posState === 'orders' && (
        <div style={{
          margin: '0 8px 9px 11px',
          background: 'rgba(37,99,235,.04)',
          borderLeft: '2px solid var(--blue-b)',
          borderRadius: 4, padding: '5px 7px 6px',
        }}>
          {loadingOrders && <Spinner />}
          {orders?.map(o => <OrderRow key={o.id} order={o} symbol={symbol} priceSpec={priceSpec} sizeSpec={sizeSpec} />)}
          {orders && orders.length === 0 && (
            <div style={{ color: 'var(--muted)', fontSize: 12, padding: '4px 0' }}>No orders</div>
          )}
          {isOpen && (
            <>
              {closeError && (
                <div style={{ color: 'var(--red)', fontSize: 11, padding: '4px 0' }}>{closeError}</div>
              )}
              <button
                type="button"
                disabled={closeInFlight}
                aria-label="Close position"
                style={{ ...closePosBtn, cursor: closeInFlight ? 'default' : 'pointer', opacity: closeInFlight ? 0.6 : 1 }}
                onClick={handleClosePosition}
              >
                {closeInFlight ? 'Closing…' : 'Close position'}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---- pending order card ----

function PendingOrderCard({ order: o }: { order: PendingOrder }) {
  const priceCols = [
    { label: 'Price', value: formatPrice(o.symbol, o.price),      color: 'var(--muted)' },
    { label: 'Mark',  value: o.mark_price != null ? formatPrice(o.symbol, o.mark_price) : '—', color: 'var(--green)' },
    { label: 'SL',    value: o.sl_price   != null ? formatPrice(o.symbol, o.sl_price)   : '—', color: 'var(--muted)' },
    { label: 'TP',    value: o.tp_price   != null ? formatPrice(o.symbol, o.tp_price)   : '—', color: 'var(--muted)' },
  ];
  return (
    <div style={{
      background: 'var(--bg2)', border: '1px solid var(--border)',
      borderRadius: 9, marginBottom: 8, overflow: 'hidden',
      position: 'relative', boxShadow: '0 1px 1px rgba(20,30,50,.03)',
    }}>
      {/* Left accent band — this card is the pending order, not the whole strategy */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: 4,
        borderRadius: '9px 0 0 9px', background: 'var(--yellow)', zIndex: 1,
      }} />
      <div style={{ display: 'flex', flexDirection: 'column', padding: '6px 10px 6px 14px', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <HeaderPill variant={o.side === 'buy' ? 'buy' : 'sell'}>
            {o.side.toUpperCase()}
          </HeaderPill>
          <span style={{ fontFamily: MONO, fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>{o.symbol}</span>
          <span style={{ flex: 1 }} />
          <span style={{
            fontSize: 9.5, fontWeight: 600, letterSpacing: '.04em', textTransform: 'uppercase',
            color: 'var(--yellow)', background: 'var(--yellow-a)', border: '1px solid var(--yellow-b)',
            borderRadius: 20, padding: '2px 7px', flexShrink: 0,
          }}>
            Pending
          </span>
        </div>
        <div style={{
          display: 'grid', gridTemplateColumns: `repeat(${priceCols.length}, auto)`,
          gap: '0 10px', fontFamily: MONO, fontSize: 11,
        }}>
          {priceCols.map(c => <span key={c.label} style={{ color: 'var(--dim)' }}>{c.label}</span>)}
          {priceCols.map(c => <span key={c.label + '_v'} style={{ color: c.color }}>{c.value}</span>)}
        </div>
      </div>
    </div>
  );
}

// ---- order row ----

function OrderRow({ order: o, symbol, priceSpec, sizeSpec }: {
  order: TreeOrder; symbol: string;
  priceSpec: { price_mode: 'tick' | 'sigfig' | null; price_tick: number | null; price_sigfigs: number | null };
  sizeSpec: { size_dp: number | null };
}) {
  const [keyOpen, setKeyOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<OrderDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const toggleFullInfo = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (detail !== null) { setDetailOpen(d => !d); return; }
    setLoadingDetail(true);
    fetchOrderDetail(o.id)
      .then(d => { setDetail(d); setDetailOpen(true); })
      .catch(() => setDetail(null))
      .finally(() => setLoadingDetail(false));
  }, [detail, o.id]);

  const fillStr  = o.fill  != null ? formatPrice(symbol, o.fill, priceSpec) : '—';
  const absDelta = o.delta != null ? Math.abs(o.delta) : null;
  const deltaStr = absDelta != null
    ? ((o.delta! >= 0 ? '+' : '−') + formatSize(symbol, absDelta, sizeSpec))
    : '—';

  return (
    <div style={{
      background: 'var(--bg2)', border: '1px solid var(--border)',
      borderRadius: 8, marginBottom: 6,
    }}>
      {/* Order header */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setKeyOpen(k => !k)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setKeyOpen(k => !k); } }}
        style={{ display: 'flex', alignItems: 'center', minHeight: 42, padding: '4px 10px', cursor: 'pointer', gap: 6 }}
      >
        <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--dim)', flexShrink: 0 }}>
          {formatRelative(o.time)}
        </span>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', flex: 1 }}>
          {o.type}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--muted)' }}>
          {fillStr} · Δ{deltaStr}
        </span>
      </div>

      {/* Key details */}
      {keyOpen && (
        <div style={{ padding: '4px 10px 8px', borderTop: '1px solid var(--border)' }}>
          <KV k="Avg fill"  v={o.key.avg_fill != null ? formatPrice(symbol, o.key.avg_fill, priceSpec) : '—'} />
          <KV k="Realized"  v={formatPnl(o.key.realized)}   vColor={pnlColor(o.key.realized)} />
          <KV k="Fee"       v={o.key.fee != null ? String(o.key.fee) : '—'} />
          <KV k="Status"    v={o.status} />

          <button
            type="button"
            onClick={toggleFullInfo}
            disabled={loadingDetail}
            style={{
              display: 'block', width: '100%', marginTop: 6,
              padding: '6px 8px', fontSize: 12, fontFamily: 'inherit',
              color: 'var(--blue)', background: 'var(--blue-a)',
              border: '1px solid var(--blue-b)', borderRadius: 7,
              cursor: loadingDetail ? 'default' : 'pointer',
              opacity: loadingDetail ? 0.6 : 1,
            }}
          >
            {loadingDetail ? 'Loading…' : detailOpen ? 'Hide full info' : 'full info'}
          </button>

          {/* Full detail */}
          {detailOpen && detail && (
            <div style={{ marginTop: 8 }}>
              <SectionLabel text="Origin" />
              <KV k="Source" v={detail.origin.signal_source ?? '—'} />

              <SectionLabel text="Justification" />
              <KV k="Indicator price" v={detail.justification.indicator_price != null ? String(detail.justification.indicator_price) : '—'} />
              <KV k="AI reasoning"    v={detail.justification.ai_reasoning ?? '—'} />
              <KV k="AI confidence"   v={detail.justification.ai_confidence != null ? `${detail.justification.ai_confidence}` : '—'} />

              {detail.execution && (
                <>
                  <SectionLabel text="Execution" />
                  <KV k="Requested"   v={detail.execution.requested_price != null ? formatPrice(symbol, detail.execution.requested_price, priceSpec) : '—'} />
                  <KV k="Actual fill" v={detail.execution.actual_fill_price != null ? formatPrice(symbol, detail.execution.actual_fill_price, priceSpec) : '—'} />
                  <KV k="Fee"         v={detail.execution.exchange_fee != null ? String(detail.execution.exchange_fee) : '—'} />
                  <KV k="Exch order"  v={detail.execution.exchange_order_id ?? '—'} />
                  <KV k="Placed"      v={formatRelative(detail.execution.placed_at)} />
                  <KV k="Filled"      v={formatRelative(detail.execution.filled_at)} />
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- atoms ----

function GreenDot() {
  return (
    <span style={{
      width: 7, height: 7, borderRadius: '50%',
      background: 'var(--green)', boxShadow: '0 0 0 2px var(--green-a)',
      flexShrink: 0, display: 'inline-block',
    }} />
  );
}

function YellowDot() {
  return (
    <span style={{
      width: 7, height: 7, borderRadius: '50%',
      background: 'var(--yellow)', boxShadow: '0 0 0 2px var(--yellow-a)',
      flexShrink: 0, display: 'inline-block',
    }} />
  );
}

function AccountChip({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      fontSize: 10.5, padding: '1px 7px', borderRadius: 20,
      border: '1px solid var(--blue-b)', color: 'var(--blue)', background: 'var(--blue-a)',
      whiteSpace: 'nowrap',
    }}>
      {children}
    </span>
  );
}

function LlmChip({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      fontSize: 10.5, padding: '1px 7px', borderRadius: 20,
      border: '1px solid rgba(83,74,183,.25)', color: '#534AB7', background: 'rgba(83,74,183,.10)',
      whiteSpace: 'nowrap',
    }}>
      {children}
    </span>
  );
}

function StopChip({ reason }: { reason: string | null }) {
  let chipColor: CSSProperties;
  if (reason === 'drawdown') {
    chipColor = {
      border: '1px solid var(--failed-color-b)',
      background: 'var(--failed-color-a)',
      color: 'var(--failed-color)',
    };
  } else if (reason === 'error') {
    chipColor = {
      border: '1px solid var(--red-b)',
      background: 'var(--red-a)',
      color: 'var(--red)',
    };
  } else {
    chipColor = {
      border: '1px solid var(--border-hi)',
      background: 'var(--bg3)',
      color: 'var(--muted)',
    };
  }
  return (
    <span style={{
      fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.02em',
      padding: '2px 7px', borderRadius: 6,
      whiteSpace: 'nowrap', lineHeight: 1,
      ...chipColor,
    }}>
      {reason ?? 'stopped'}
    </span>
  );
}

function DiffChip({ label }: { label: string }) {
  return (
    <span style={{
      fontSize: 10, padding: '1px 6px', borderRadius: 20,
      border: '1px solid var(--failed-color-b)',
      background: 'var(--failed-color-a)', color: 'var(--failed-color)',
      whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  );
}

// ---- shared style objects ----

const BAR_ITEM_HEIGHT = 28;

function treeChip(active: boolean, clear = false): CSSProperties {
  return {
    whiteSpace:   'nowrap',
    background:   clear ? 'var(--red-a)' : active ? 'var(--blue-a)' : 'var(--bg2)',
    border:       `1px solid ${clear ? 'var(--red-b)' : active ? 'var(--blue)' : 'var(--border)'}`,
    borderRadius: '20px',
    padding:      '0 12px',
    height:       BAR_ITEM_HEIGHT,
    boxSizing:    'border-box',
    display:      'inline-flex',
    alignItems:   'center',
    justifyContent: 'center',
    lineHeight:   1,
    fontSize:     10,
    fontWeight:   500,
    color:        clear ? 'var(--red)' : active ? 'var(--blue)' : 'var(--muted)',
    cursor:       'pointer',
    flexShrink:   0,
  };
}

const addBtn: CSSProperties = {
  width: BAR_ITEM_HEIGHT, height: BAR_ITEM_HEIGHT, borderRadius: '50%', flexShrink: 0,
  boxSizing: 'border-box',
  border: '1px solid var(--blue-b)', background: 'var(--blue-a)', color: 'var(--blue)',
  fontSize: 14, fontWeight: 600, lineHeight: 1,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  cursor: 'pointer', padding: 0,
};

const iconBtnBase: CSSProperties = {
  width: 30, height: 30, borderRadius: '50%',
  border: '1px solid var(--border)', background: 'var(--bg3)', color: 'var(--muted)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  margin: '6px 5px', flexShrink: 0, cursor: 'default', padding: 0,
};
const iconBtnSm: CSSProperties = { ...iconBtnBase, fontSize: 11 };
const iconBtnLg: CSSProperties = { ...iconBtnBase, fontSize: 13, fontWeight: 600 };

const closeIcBtn: CSSProperties = {
  width: 30, height: 30, borderRadius: '50%',
  border: '1px solid var(--red-b)', background: 'var(--red-a)', color: 'var(--red)',
  fontSize: 10, display: 'flex', alignItems: 'center', justifyContent: 'center',
  flexShrink: 0, cursor: 'pointer', padding: 0,
};

const closePosBtn: CSSProperties = {
  display: 'block', width: '100%', marginTop: 8,
  padding: 9, fontSize: 12.5, fontFamily: 'inherit',
  color: 'var(--red)', background: 'var(--red-a)',
  border: '1px solid var(--red-b)', borderRadius: 8, cursor: 'pointer',
};

const loadMoreBtn: CSSProperties = {
  flex: 1, padding: 8, fontSize: 12, fontFamily: 'inherit',
  color: 'var(--blue)', background: 'var(--blue-a)',
  border: '1px solid var(--blue-b)', borderRadius: 8, cursor: 'pointer',
};

const collapseBtn: CSSProperties = {
  padding: 8, fontSize: 12, fontFamily: 'inherit',
  color: 'var(--muted)', background: 'var(--bg2)',
  border: '1px solid var(--border)', borderRadius: 8, cursor: 'pointer',
};
