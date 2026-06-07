import React, { useState, useEffect, useCallback } from 'react';
import { HeaderPill } from '../components/shared/HeaderPill';
import { TopBar }     from '../components/shared/TopBar';
import { FilterBar }  from '../components/shared/FilterBar';
import { formatPrice, formatSize } from '../utils/precision';
import { formatRelative } from '../utils/datetime';

interface Order {
  id:               string;
  symbol:           string;
  side:             'buy' | 'sell';
  leverage?:        number;
  status:           string; // Using string to allow backend statuses before mapping
  strategy_id?:     string;
  strategy_name?:   string;
  signal_source?:    string;
  account_exchange?: string;
  account_id?:      string;
  account_label?:   string;
  created_at:       string;
  price?:           number;
  size?:            number;
  margin?:          number;
  error_msg?:       string;
}

type ChipStatus = 'filled' | 'lag-fail' | 'route-fail' | 'pending' | 'rejected' | 'cancelled';

const CHIP_STYLES: Record<ChipStatus, React.CSSProperties> = {
  'filled':     { background:'var(--green-a)',         color:'var(--green)',        borderColor:'var(--green-b)' },
  'lag-fail':   { background:'var(--failed-color-a)',  color:'var(--failed-color)', borderColor:'var(--failed-color-b)' },
  'route-fail': { background:'var(--failed-color-a)',  color:'var(--failed-color)', borderColor:'var(--failed-color-b)' },
  'pending':    { background:'var(--blue-a)',           color:'var(--blue)',         borderColor:'var(--blue-b)' },
  'rejected':   { background:'var(--failed-color-a)',  color:'var(--failed-color)', borderColor:'var(--failed-color-b)' },
  'cancelled':  { background:'var(--failed-color-a)',  color:'var(--failed-color)', borderColor:'var(--failed-color-b)' },
};

function StatusChip({ status }: { status: ChipStatus }) {
  return (
    <span style={{
      fontFamily:    'JetBrains Mono, monospace',
      fontSize:      '10px',
      fontWeight:    700,
      letterSpacing: '.04em',
      borderRadius:  'var(--pill-r)',
      padding:       '2px 6px',
      border:        '1px solid',
      textTransform: 'uppercase',
      flexShrink:    0,
      ...CHIP_STYLES[status],
    }}>
      {status}
    </span>
  );
}

function OrderCard({
  order,
  onRetry,
  onDelete,
  onCancel,
  retryingId,
}: {
  order:      Order;
  onRetry:    (id: string) => void;
  onDelete:   (id: string) => void;
  onCancel:   (id: string) => void;
  retryingId: string | null;
}) {
  // Normalize status for UI
  let uiStatus: ChipStatus = 'pending';
  if (order.status === 'filled') uiStatus = 'filled';
  else if (order.status === 'route_failed') uiStatus = 'route-fail';
  else if (order.status === 'lag_failed' || order.status === 'lag-fail') uiStatus = 'lag-fail';
  else if (order.status === 'rejected') uiStatus = 'rejected';
  else if (order.status === 'cancelled') uiStatus = 'cancelled';
  else if (['received', 'routing', 'pending'].includes(order.status)) uiStatus = 'pending';

  const isFailed = uiStatus === 'lag-fail' || uiStatus === 'route-fail' || uiStatus === 'rejected' || uiStatus === 'cancelled';

  // Left bar: failed orders always use failed-color regardless of side
  const barColor = isFailed
    ? 'var(--failed-color)'
    : order.side === 'buy' ? 'var(--green)' : 'var(--red)';

  const sideVariant = order.side === 'buy' ? 'buy' : 'sell';

  const src = order.signal_source;
  const source = src === 'tradingview' ? 'TradingView'
    : src === 'internal' ? 'Engine'
    : 'MATP';
  const exch = order.account_exchange;
  const destination = order.account_label
    || (exch ? exch.charAt(0).toUpperCase() + exch.slice(1) : '')
    || 'Exchange';

  // Footer buttons per status
  const footerButtons: { label: string; color: 'red' | 'blue'; onClick: () => void; fullWidth?: boolean }[] = (() => {
    switch (uiStatus) {
      case 'lag-fail':
        return [
          { label: '✕ Delete', color: 'red' as const,
            onClick: () => onDelete(order.id), fullWidth: true },
        ];
      case 'route-fail':
        return []; // rendered inline with retrying state
      case 'rejected':
        return [
          { label: '↺ Retry',  color: 'blue' as const, onClick: () => onRetry(order.id) },
          { label: '✕ Delete', color: 'red'  as const, onClick: () => onDelete(order.id) },
        ];
      case 'pending':
        return [
          { label: '✕ Cancel Order', color: 'red' as const,
            onClick: () => onCancel(order.id), fullWidth: true },
        ];
      case 'cancelled':
        return [
          { label: '✕ Delete', color: 'red' as const,
            onClick: () => onDelete(order.id), fullWidth: true },
        ];
      default:
        return [];
    }
  })();

  return (
    <div style={{
      background:    'var(--bg3)',
      borderRadius:  'var(--r)',
      border:        '1px solid var(--border)',
      marginBottom:  '10px',
      position:      'relative',
      display:       'flex',
      flexDirection: 'column',
      overflow:      'hidden',
    }}>
      {/* Left bar */}
      <div style={{
        position:'absolute', left:0, top:0, bottom:0,
        width:'4px', background: barColor, zIndex:1,
      }} />

      {/* Row 1: side + leverage + symbol + chip */}
      <div style={{
        display:'flex', alignItems:'center', gap:'6px',
        padding:'12px 12px 0 18px', lineHeight:1,
      }}>
        <HeaderPill variant={sideVariant}>
          {order.side.toUpperCase()}
        </HeaderPill>
        {order.leverage && (
          <HeaderPill variant="lev">{order.leverage}x</HeaderPill>
        )}
        <span style={{
          fontSize:'16px', fontWeight:700, letterSpacing:'-.01em',
          color:'var(--text)', whiteSpace:'nowrap', flexShrink:0,
          marginRight:'2px',
        }}>
          {order.symbol}
        </span>
        <div style={{ marginLeft:'auto' }}>
          <StatusChip status={uiStatus} />
        </div>
      </div>

      {/* Strategy row */}
      {(order.strategy_name || order.strategy_id) && (
        <div style={{ padding:'5px 12px 0 18px' }}>
          <span style={{
            fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
            fontWeight:600, background:'var(--bg3)',
            border:'1px solid var(--border)', borderRadius:'var(--pill-r)',
            padding:'1px 6px', color:'var(--muted)',
            whiteSpace:'nowrap', lineHeight:1.25,
          }}>
            {order.strategy_name || order.strategy_id}
          </span>
        </div>
      )}

      {/* Row 1b: route + timestamp */}
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'space-between',
        padding:'5px 12px 2px 18px',
      }}>
        <div style={{ display:'flex', alignItems:'center', gap:'4px' }}>
          <HeaderPill variant="neutral">{source}</HeaderPill>
          <span style={{
            fontSize:'10px', color:'var(--dim)',
            fontFamily:'monospace', fontWeight:'bold',
          }}>→</span>
          <HeaderPill variant="neutral">{destination}</HeaderPill>
        </div>
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
          fontWeight:500, color:'var(--muted)',
        }}>
          {formatRelative(order.created_at)}
        </span>
      </div>

      {/* Data row: Price | Size | Margin */}
      <div style={{
        display:'flex', margin:'8px 12px 0 18px',
        borderRadius:'var(--pill-r)', overflow:'hidden',
        border:'1px solid var(--border)', background:'rgba(226,232,240,.4)',
      }}>
        {[
          { label: 'Price',  value: formatPrice(order.symbol, order.price ?? null) },
          { label: 'Size',   value: formatSize(order.symbol,  order.size  ?? null) },
          { label: 'Margin', value: order.margin != null ? Number(order.margin).toFixed(2) : '—' },
        ].map((cell, idx) => (
          <div key={cell.label} style={{
            flex:1, padding:'6px 10px', display:'flex',
            flexDirection:'column', gap:'1px',
            borderRight: idx < 2 ? '1px solid var(--border)' : 'none',
          }}>
            <span style={{
              fontSize:'9px', fontWeight:600, letterSpacing:'.11em',
              textTransform:'uppercase', color:'var(--dim)', marginBottom:'2px',
            }}>
              {cell.label}
            </span>
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
              fontWeight: cell.label === 'Size' ? 700 : 600,
              color:'var(--text)', whiteSpace:'nowrap',
            }}>
              {cell.value}
            </span>
          </div>
        ))}
      </div>

      {/* Footer */}
      {uiStatus === 'route-fail' ? (
        <div style={{
          borderTop:'1px solid var(--border)', background:'var(--bg2)',
          display:'flex', marginTop:'8px',
        }}>
          <button
            onClick={() => !retryingId && onRetry(order.id)}
            disabled={retryingId === order.id}
            style={{
              flex:1, background:'transparent', border:'none',
              borderRight:'1px solid var(--border)',
              color: retryingId === order.id ? 'var(--dim)' : 'var(--blue)',
              fontSize:'11px', fontWeight:700, letterSpacing:'.06em',
              textTransform:'uppercase', padding:'10px',
              cursor: retryingId === order.id ? 'not-allowed' : 'pointer',
              textAlign:'center',
            }}>
            {retryingId === order.id ? '↺ Retrying…' : '↺ Retry'}
          </button>
          <button
            onClick={() => onDelete(order.id)}
            style={{
              flex:1, background:'transparent', border:'none',
              color:'var(--red)', fontSize:'11px', fontWeight:700,
              letterSpacing:'.06em', textTransform:'uppercase',
              padding:'10px', cursor:'pointer', textAlign:'center',
            }}>
            ✕ Delete
          </button>
        </div>
      ) : footerButtons.length > 0 && (
        <div style={{
          borderTop:'1px solid var(--border)', background:'var(--bg2)',
          display:'flex', marginTop:'8px',
        }}>
          {footerButtons.map((btn, idx) => (
            <button
              key={idx}
              onClick={btn.onClick}
              style={{
                flex:          1,
                background:    'transparent',
                border:        'none',
                borderRight:   !btn.fullWidth && idx < footerButtons.length - 1
                  ? '1px solid var(--border)'
                  : 'none',
                color:         btn.color === 'red' ? 'var(--red)' : 'var(--blue)',
                fontSize:      '11px',
                fontWeight:    700,
                letterSpacing: '.06em',
                textTransform: 'uppercase',
                padding:       '10px',
                cursor:        'pointer',
                textAlign:     'center',
              }}
            >
              {btn.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Orders() {
  const [orders, setOrders]     = useState<Order[]>([]);
  const [loading, setLoading]   = useState(true);
  const [total, setTotal]       = useState(0);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const [filterAsset,    setFilterAsset]    = useState<string>('all');
  const [filterStatus,   setFilterStatus]   = useState<string>('all');
  const [filterStrategy, setFilterStrategy] = useState<string>('all');
  const [page, setPage]                     = useState<number>(1);
  const PAGE_SIZE = 50;

  const fetchOrders = useCallback(async (pageNum: number = 1, append: boolean = false) => {
    try {
      const res  = await fetch(`/api/dashboard/orders?limit=${PAGE_SIZE}&page=${pageNum}`);
      const data = await res.json();
      const items = (data.items ?? (Array.isArray(data) ? data : [])).map((o: any) => ({
        ...o,
        created_at: o.received_at, // Map backend received_at to created_at
        price: o.actual_fill_price || o.indicator_price || o.price,
      }));
      setOrders(prev => append ? [...prev, ...items] : items);
      setTotal(data.total ?? items.length);
    } catch (err) {
      console.error('fetchOrders error:', err);
      if (!append) setOrders([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOrders(1, false);
    const interval = setInterval(() => fetchOrders(1, false), 20000);
    return () => clearInterval(interval);
  }, [fetchOrders]);

  const uniqueAssets = Array.from(
    new Set(orders.map(o => o.symbol))
  ).sort();

  const uniqueStrategies = Array.from(
    new Set(orders.map(o => o.strategy_name || o.strategy_id).filter(Boolean))
  ).sort() as string[];

  const handleLoadMore = () => {
    const nextPage = page + 1;
    setPage(nextPage);
    fetchOrders(nextPage, true);
  };

  const handleRetry = async (id: string) => {
    setRetryingId(id);
    try {
      await fetch(`/api/dashboard/orders/${id}/retry`, { method: 'POST' });
      fetchOrders();
    } catch {}
    finally {
      setRetryingId(null);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this order log?')) return;
    try {
      const res = await fetch(`/api/dashboard/orders/${id}`, { method: 'DELETE' });
      if (res.ok) {
        setOrders(prev => prev.filter(o => o.id !== id));
        setTotal(prev => Math.max(0, prev - 1));
      }
    } catch {}
  };

  const handleCancel = async (id: string) => {
    if (!confirm('Cancel this pending order?')) return;
    try {
      const res = await fetch(`/api/dashboard/orders/${id}/cancel`, { method: 'POST' });
      if (res.ok) {
        setOrders(prev => prev.map(o => o.id === id ? { ...o, status: 'cancelled' } : o));
      }
    } catch {}
  };

  try {
    const filteredOrders = orders.filter(o => {
      if (filterAsset    !== 'all' && o.symbol                          !== filterAsset)    return false;
      if (filterStatus   !== 'all' && o.status                          !== filterStatus)   return false;
      if (filterStrategy !== 'all' && (o.strategy_name || o.strategy_id) !== filterStrategy) return false;
      return true;
    });

    if (loading) {
      return (
        <div style={{ padding:'24px', color:'var(--dim)' }}>
          Loading orders...
        </div>
      );
    }

    return (
      <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>

        <TopBar
          title="Orders"
          right={
            <span style={{
              background:'var(--bg3)', border:'1px solid var(--border)',
              borderRadius:'20px', padding:'4px 11px',
              fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
              color:'var(--muted)',
            }}>
              {total} total
            </span>
          }
        />

        <div style={{
          display:'flex', gap:'6px', padding:'10px 14px',
          borderBottom:'1px solid var(--border)',
          overflowX:'auto', flexShrink:0, scrollbarWidth:'none',
        }}>
          <select
            value={filterAsset}
            onChange={e => { setFilterAsset(e.target.value); setPage(1); }}
            style={{
              background: filterAsset !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
              border: `1px solid ${filterAsset !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
              borderRadius:'20px', padding:'5px 12px', fontSize:'10px',
              fontWeight:500, color: filterAsset !== 'all' ? 'var(--blue)' : 'var(--muted)',
              cursor:'pointer', outline:'none',
            }}>
            <option value="all">All Assets</option>
            {uniqueAssets.map(a => <option key={a} value={a}>{a}</option>)}
          </select>

          <select
            value={filterStatus}
            onChange={e => { setFilterStatus(e.target.value); setPage(1); }}
            style={{
              background: filterStatus !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
              border: `1px solid ${filterStatus !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
              borderRadius:'20px', padding:'5px 12px', fontSize:'10px',
              fontWeight:500, color: filterStatus !== 'all' ? 'var(--blue)' : 'var(--muted)',
              cursor:'pointer', outline:'none',
            }}>
            <option value="all">All Statuses</option>
            <option value="filled">Filled</option>
            <option value="route_failed">Route Fail</option>
            <option value="lag_failed">Lag Fail</option>
            <option value="pending">Pending</option>
            <option value="cancelled">Cancelled</option>
          </select>

          <select
            value={filterStrategy}
            onChange={e => { setFilterStrategy(e.target.value); setPage(1); }}
            style={{
              background: filterStrategy !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
              border: `1px solid ${filterStrategy !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
              borderRadius:'20px', padding:'5px 12px', fontSize:'10px',
              fontWeight:500, color: filterStrategy !== 'all' ? 'var(--blue)' : 'var(--muted)',
              cursor:'pointer', outline:'none',
            }}>
            <option value="all">All Strategies</option>
            {uniqueStrategies.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          {(filterAsset !== 'all' || filterStatus !== 'all' || filterStrategy !== 'all') && (
            <span
              onClick={() => {
                setFilterAsset('all');
                setFilterStatus('all');
                setFilterStrategy('all');
                setPage(1);
              }}
              style={{
                whiteSpace:'nowrap', background:'var(--bg2)',
                border:'1px solid var(--border)', borderRadius:'20px',
                padding:'5px 12px', fontSize:'10px', fontWeight:500,
                color:'var(--red)', cursor:'pointer',
              }}>
              ✕ Clear
            </span>
          )}
        </div>

        <div style={{
          flex:1, overflowY:'auto', padding:'14px 14px 80px',
          scrollbarWidth:'none',
        }}>
          {filteredOrders.length === 0 ? (
            <p style={{ color:'var(--dim)', textAlign:'center', padding:'40px 0' }}>
              No orders found.
            </p>
          ) : (
            filteredOrders.map(o => (
              <OrderCard
                key={o.id}
                order={o}
                onRetry={handleRetry}
                onDelete={handleDelete}
                onCancel={handleCancel}
                retryingId={retryingId}
              />
            ))
          )}
          {total > orders.length && (
            <div style={{ textAlign:'center', padding:'16px 0 24px' }}>
              <button
                onClick={handleLoadMore}
                style={{
                  padding:'8px 20px',
                  border:'1px solid var(--border)', borderRadius:'20px',
                  background:'var(--bg2)', fontSize:'12px', fontWeight:600,
                  color:'var(--blue)', cursor:'pointer',
                }}>
                Load {Math.min(PAGE_SIZE, total - orders.length)} more
                ({orders.length} / {total})
              </button>
            </div>
          )}
        </div>
      </div>
    );
  } catch (err) {
    console.error('Orders render error:', err);
    return (
      <div style={{ padding: '24px', color: 'red' }}>
        Render error: {String(err)}
      </div>
    );
  }
}
