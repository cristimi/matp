import { useState, useEffect, useRef, useCallback, CSSProperties } from 'react';
import { HeaderPill } from '../components/shared';
import { formatPct, formatPnl, pnlColor } from '../utils/pnl';
import { formatPrice, formatSize } from '../utils/precision';
import { formatRelative } from '../utils/datetime';
import {
  fetchStrategyTree, fetchTreePositions, fetchPositionOrders, fetchOrderDetail,
} from '../api';
import type { StrategyTreeItem, TreePosition, TreeOrder, OrderDetail } from '../api';

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

// ---- page ----

export default function StrategyTreePage() {
  const [strategies, setStrategies] = useState<StrategyTreeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchStrategyTree()
      .then(setStrategies)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ maxWidth: 460, margin: '0 auto', padding: '14px 10px 70px' }}>
      {loading && <div style={{ padding: 24, color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>Loading…</div>}
      {error && <div style={{ padding: 24, color: 'var(--red)', fontSize: 13 }}>{error}</div>}
      {!loading && !error && strategies.length === 0 && (
        <div style={{ padding: 24, color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>No strategies</div>
      )}
      {strategies.map(s => <StrategyCard key={s.id} strategy={s} />)}
    </div>
  );
}

// ---- strategy card ----

type ExpandState = 'collapsed' | 'open' | 'all';

function StrategyCard({ strategy: s }: { strategy: StrategyTreeItem }) {
  const hasOpen = s.open_positions_count > 0;
  const isStopped = !s.enabled;

  const [expandState, setExpandState] = useState<ExpandState>('collapsed');
  const [openPositions, setOpenPositions] = useState<TreePosition[] | null>(null);
  const [allPositions, setAllPositions]   = useState<TreePosition[] | null>(null);
  const [loadingPos, setLoadingPos] = useState(false);
  const [closedShown, setClosedShown] = useState(CLOSED_STEP);

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

  const handleTap = useCallback(() => {
    if (expandState === 'collapsed') {
      doFetchOpen();
      setExpandState('open');
    } else if (expandState === 'open') {
      doFetchAll();
      setExpandState('all');
    } else {
      setExpandState('collapsed');
    }
  }, [expandState, doFetchOpen, doFetchAll]);

  const handleHold = useCallback(() => {
    setExpandState('collapsed');
  }, []);

  const pressHandlers = useLongPress(handleTap, handleHold);

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
        background: isStopped ? 'var(--gray)' : 'var(--blue)', zIndex: 1,
      }} />

      {/* Top-right icons (inert Phase 2) */}
      <div style={{ position: 'absolute', top: 0, right: 2, display: 'flex', alignItems: 'flex-start', zIndex: 2 }}>
        <button type="button" disabled aria-label={isStopped ? 'Resume' : 'Pause'} style={iconBtnSm}>
          {isStopped ? '▶' : '⏸'}
        </button>
        <button type="button" disabled aria-label="Details" style={{ ...iconBtnLg, margin: '6px 9px 6px 0' }}>
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
          {hasOpen && <GreenDot />}
          <HeaderPill variant="neutral" style={{ fontWeight: 700 }}>{s.symbol}</HeaderPill>
          <span style={{ fontWeight: 700, fontSize: 14, letterSpacing: '-.01em', color: isStopped ? 'var(--muted)' : 'var(--text)' }}>
            {s.name}
          </span>
        </div>

        {/* Row 2 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
          <AccountChip>{s.account_label}</AccountChip>
          {isStopped && <StopChip reason={s.stop_reason} />}
        </div>

        {/* Row 3 */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 18, marginTop: 6, flexWrap: 'nowrap' }}>
          <Metric label="Allocation" value={Number(s.capital_allocation).toFixed(0)} />
          <Metric label="Total Return" value={formatPct(s.total_return)} color={pnlColor(s.total_return)} />
          {hasOpen && <Metric label="Open PnL" value={formatPnl(s.open_pnl)} color={pnlColor(s.open_pnl)} />}
        </div>
      </div>

      {/* Strategy track */}
      {expandState !== 'collapsed' && (
        <div style={{ margin: '2px 9px 11px 13px', padding: '2px 4px 6px' }}>
          {loadingPos && <Spinner />}

          {shownOpen.length > 0 && (
            <>
              <SectionLabel text="Open Positions" />
              {shownOpen.map(p => (
                <PositionCard key={p.id} position={p} stratLabel={s.account_label} stratExchange={s.account_exchange} />
              ))}
            </>
          )}

          {expandState === 'all' && !loadingPos && (
            <>
              {allClosed.length > 0 && (
                <>
                  <SectionLabel text="Closed Positions" />
                  {shownClosed.map(p => (
                    <PositionCard key={p.id} position={p} stratLabel={s.account_label} stratExchange={s.account_exchange} />
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

type PosState = 'header' | 'details' | 'orders';

function PositionCard({
  position: p,
  stratLabel,
  stratExchange,
}: {
  position: TreePosition;
  stratLabel: string;
  stratExchange: string;
}) {
  const [posState, setPosState] = useState<PosState>('header');
  const [orders, setOrders] = useState<TreeOrder[] | null>(null);
  const [loadingOrders, setLoadingOrders] = useState(false);

  const isOpen = p.status === 'open';
  const symbol = `${p.base_asset}-${p.quote_asset}`;
  const diffAcct = p.account_label !== stratLabel || p.account_exchange !== stratExchange;

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

  const pnlVal = isOpen ? p.unrealized_pnl : p.realized_pnl;

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
        style={{ display: 'flex', alignItems: 'center', minHeight: 42, padding: '6px 10px', cursor: 'pointer', userSelect: 'none', gap: 7 }}
      >
        <HeaderPill variant={p.side === 'long' ? 'long' : 'short'}>
          {p.side === 'long' ? 'LONG' : 'SHORT'}
        </HeaderPill>
        <span style={{ fontFamily: MONO, fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>
          {p.base_asset}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 12, color: 'var(--muted)' }}>
          {formatSize(symbol, p.size)}
        </span>
        {diffAcct && <DiffChip label={p.account_label} />}
        <span style={{ flex: 1 }} />
        {pnlVal != null && (
          <span style={{ fontFamily: MONO, fontSize: 12, color: pnlColor(pnlVal) }}>
            {formatPnl(pnlVal)}
          </span>
        )}
        {isOpen && (
          <button type="button" disabled aria-label="Close position (Phase 3)" style={closeIcBtn}>
            ✕
          </button>
        )}
      </div>

      {/* Detail panel */}
      {posState !== 'header' && (
        <div style={{ padding: '4px 12px 8px', borderTop: '1px solid var(--border)' }}>
          {isOpen ? (
            <>
              <KV k="Entry"  v={formatPrice(symbol, p.entry_price)} />
              <KV k="Mark"   v={formatPrice(symbol, p.mark_price)} />
              <KV k="Liq"    v={formatPrice(symbol, p.liquidation_price)} />
              <KV k="Lever"  v={p.leverage != null ? `${p.leverage}×` : '—'} />
              <KV k="Size"   v={formatSize(symbol, p.size)} />
              <KV k="Opened" v={formatRelative(p.opened_at)} />
              {p.unrealized_pnl != null && (
                <KV k="Unrealized" v={formatPnl(p.unrealized_pnl)} vColor={pnlColor(p.unrealized_pnl)} />
              )}
            </>
          ) : (
            <>
              <KV k="Entry"        v={formatPrice(symbol, p.entry_price)} />
              <KV k="Realized"     v={formatPnl(p.realized_pnl)} vColor={pnlColor(p.realized_pnl)} />
              {p.close_reason && <KV k="Close reason" v={p.close_reason} />}
              <KV k="Opened"       v={formatRelative(p.opened_at)} />
              {p.closed_at && <KV k="Closed" v={formatRelative(p.closed_at)} />}
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
          {orders?.map(o => <OrderRow key={o.id} order={o} symbol={symbol} />)}
          {orders && orders.length === 0 && (
            <div style={{ color: 'var(--muted)', fontSize: 12, padding: '4px 0' }}>No orders</div>
          )}
          {isOpen && (
            <button type="button" disabled aria-label="Close position (Phase 3)" style={closePosBtn}>
              Close position
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---- order row ----

function OrderRow({ order: o, symbol }: { order: TreeOrder; symbol: string }) {
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

  const fillStr  = o.fill  != null ? formatPrice(symbol, o.fill) : '—';
  const absDelta = o.delta != null ? Math.abs(o.delta) : null;
  const deltaStr = absDelta != null
    ? ((o.delta! >= 0 ? '+' : '−') + formatSize(symbol, absDelta))
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
          <KV k="Avg fill"  v={o.key.avg_fill != null ? formatPrice(symbol, o.key.avg_fill) : '—'} />
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
                  <KV k="Requested"   v={detail.execution.requested_price != null ? formatPrice(symbol, detail.execution.requested_price) : '—'} />
                  <KV k="Actual fill" v={detail.execution.actual_fill_price != null ? formatPrice(symbol, detail.execution.actual_fill_price) : '—'} />
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

function StopChip({ reason }: { reason: string | null }) {
  return (
    <span style={{
      fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.02em',
      padding: '2px 7px', borderRadius: 6,
      border: '1px solid var(--border-hi)', background: 'var(--bg3)', color: 'var(--muted)',
      whiteSpace: 'nowrap', lineHeight: 1,
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
  flexShrink: 0, cursor: 'default', padding: 0,
};

const closePosBtn: CSSProperties = {
  display: 'block', width: '100%', marginTop: 8,
  padding: 9, fontSize: 12.5, fontFamily: 'inherit',
  color: 'var(--red)', background: 'var(--red-a)',
  border: '1px solid var(--red-b)', borderRadius: 8, cursor: 'default',
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
