import { useState, useEffect, useCallback } from 'react';
import { HeaderPill }    from '../components/shared/HeaderPill';
import { DataGrid }      from '../components/shared/DataGrid';
import { ActionBand }    from '../components/shared/ActionBand';
import { SectionHeader } from '../components/shared/SectionHeader';
import { TopBar }        from '../components/shared/TopBar';
import { FilterBar }     from '../components/shared/FilterBar';
import { formatPrice, formatSize } from '../utils/precision';
import { formatPnl, formatPct, pnlColor } from '../utils/pnl';
import { formatRelative, formatAbsolute } from '../utils/datetime';

interface Position {
  id:               string;
  symbol:           string;
  side:             'long' | 'short';
  status:           'open' | 'stale' | 'closed';
  leverage?:        number;
  margin_mode?:     string;
  strategy_name?:   string;
  strategy_type?:   string;
  account_id?:      string;
  account_label?:   string;
  account_exchange?:string;
  opened_at?:       string;
  closed_at?:       string;
  entry_price?:     number;
  mark_price?:      number;
  close_price?:     number;
  size?:            number;
  margin?:          number;
  realized_pnl?:    number;
  realized_pnl_fees?:number;
  unrealized_pnl?:  number;
  pnl_pct?:         number;
  close_reason?:    string;
  strategy_source?: string;
  size_exchange?:   number | null;
  size_divergent?:  boolean;
  margin_exchange?: number | null;
}

function PositionCard({
  position,
  onClose,
  onRefresh,
}: {
  position: Position;
  onClose:   (id: string) => void;
  onRefresh: (id: string) => void;
}) {
  const { symbol, side, status } = position;
  const isStale  = status === 'stale';
  const isClosed = status === 'closed';

  // Left bar color
  const barColor = isClosed ? 'var(--gray)'
    : isStale               ? 'var(--failed-color)'
    : side === 'long'       ? 'var(--green)'
    :                         'var(--red)';

  // Mark/close price color
  const entryNum = position.entry_price ?? 0;
  const markNum  = position.mark_price  ?? 0;
  const markColor = isStale ? 'var(--failed-color)'
    : side === 'long'
      ? (markNum >= entryNum ? 'var(--green)' : 'var(--red)')
      : (markNum <= entryNum ? 'var(--green)' : 'var(--red)');

  // P&L colors — open positions use unrealized, closed use realized
  const pnlVal  = isClosed
    ? (position.realized_pnl   ?? 0)
    : (position.unrealized_pnl ?? 0);
  const pnlPct  = position.pnl_pct ?? 0;
  const fees    = position.realized_pnl_fees ?? 0;
  const pnlMain = isStale ? 'var(--failed-color)' : pnlColor(pnlVal);
  const pnlSec  = isStale
    ? 'rgba(230,152,2,.75)'
    : 'rgba(225,29,72,.7)';

  // Status pill variant
  const statusVariant = isClosed ? 'closed'
    : isStale           ? 'stale'
    :                     'open';

  // Side pill variant
  const sideVariant = isClosed ? 'closed'
    : side === 'long'  ? 'long'
    :                    'short';

  // Route
  const isAI = position.strategy_source === 'ai_engine' || position.strategy_source === 'ai';
  const source = position.strategy_type === 'tradingview' ? 'TradingView'
    : isAI                                                 ? 'AI'
    : position.strategy_type === 'internal'                ? 'Engine'
    : 'MATP';
  const exch = position.account_exchange;
  const destination = position.account_label
    || (exch ? exch.charAt(0).toUpperCase() + exch.slice(1) : '')
    || 'Exchange';

  // DataGrid rows
  const topRow = [
    {
      label: 'Entry',
      value: (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:600, color:'var(--text)', whiteSpace:'nowrap',
        }}>
          {formatPrice(symbol, position.entry_price)}
        </span>
      ),
    },
    {
      label: 'Size',
      value: position.size_divergent ? (
        <span style={{ display:'flex', flexDirection:'column', gap:'1px', whiteSpace:'nowrap' }}>
          <span style={{
            fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
            fontWeight:700, color:'var(--failed-color)',
          }}>
            {formatSize(symbol, position.size)} ⚠
          </span>
          {position.size_exchange != null && (
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
              fontWeight:600, color:'var(--dim)',
            }}>
              exch {formatSize(symbol, position.size_exchange)}
            </span>
          )}
        </span>
      ) : (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:700, color:'var(--text)', whiteSpace:'nowrap',
        }}>
          {formatSize(symbol, position.size)}
        </span>
      ),
    },
    {
      label: 'Margin',
      value: (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:600, color:'var(--text)', whiteSpace:'nowrap',
        }}>
          {position.margin != null ? Number(position.margin).toFixed(2) : '—'}
        </span>
      ),
    },
  ];

  const botRow = isClosed ? [
    {
      label: 'Close',
      value: (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:600, color:'var(--text)', whiteSpace:'nowrap',
        }}>
          {formatPrice(symbol, position.close_price)}
        </span>
      ),
    },
    {
      label: 'P&L',
      value: (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:700, color: pnlColor(pnlVal), whiteSpace:'nowrap',
        }}>
          {formatPnl(pnlVal)}
        </span>
      ),
    },
    {
      label: 'P&L %',
      value: (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:700, color: pnlColor(pnlPct), whiteSpace:'nowrap',
        }}>
          {formatPct(pnlPct)}
        </span>
      ),
    },
  ] : [
    {
      label: 'Mark',
      value: (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:600, color: markColor, whiteSpace:'nowrap',
        }}>
          {formatPrice(symbol, position.mark_price)}
        </span>
      ),
    },
    {
      label: 'Unrealized P&L',
      value: (
        <div style={{ display:'flex', alignItems:'baseline', gap:'4px',
                      flexWrap:'nowrap', overflow:'hidden' }}>
          <span style={{
            fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
            fontWeight:700, color: pnlMain, whiteSpace:'nowrap',
          }}>
            {formatPnl(pnlVal)}
          </span>
          {fees !== 0 && (
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
              fontWeight:600, color: pnlSec, whiteSpace:'nowrap',
            }}>
              (−{Math.abs(Number(fees)).toFixed(2)})
            </span>
          )}
        </div>
      ),
    },
    {
      label: 'P&L %',
      value: (
        <span style={{
          fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
          fontWeight:700, color: pnlMain, whiteSpace:'nowrap',
        }}>
          {formatPct(pnlPct)}
        </span>
      ),
    },
  ];

  // Action band buttons
  const actionButtons = isClosed ? [] :
    isStale ? [
      { label: '↺ Refresh',        color: 'blue'  as const, onClick: () => onRefresh(position.id) },
      { label: '✕ Close Position', color: 'red'   as const, onClick: () => onClose(position.id) },
    ] : [
      { label: '✕ Close Position', color: 'red' as const, onClick: () => onClose(position.id) },
    ];

  return (
    <div style={{
      background:    'var(--bg3)',
      borderRadius:  'var(--r)',
      border:        `1px solid ${isClosed ? 'var(--border-hi)' : 'var(--border)'}`,
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

      {/* Row 1 */}
      <div style={{
        display:'flex', alignItems:'center', gap:'6px',
        padding:'12px 12px 0 18px', lineHeight:1,
      }}>
        <span style={{
          fontSize:'16px', fontWeight:700, letterSpacing:'-.01em',
          color:'var(--text)', whiteSpace:'nowrap',
          flexShrink:0, marginRight:'2px',
        }}>
          {symbol}
        </span>
        <HeaderPill variant={sideVariant}>
          {side.toUpperCase()}
        </HeaderPill>
        {position.leverage && (
          <HeaderPill variant="lev">{position.leverage}x</HeaderPill>
        )}
        {position.margin_mode && (
          <HeaderPill variant="tech">{position.margin_mode}</HeaderPill>
        )}
        <div style={{ marginLeft:'auto' }}>
          <HeaderPill variant={statusVariant}>{status}</HeaderPill>
        </div>
      </div>

      {/* Strategy row */}
      {position.strategy_name && (
        <div style={{ padding:'5px 12px 0 18px' }}>
          <span style={{
            fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
            fontWeight:600, background:'var(--bg3)',
            border:'1px solid var(--border)', borderRadius:'var(--pill-r)',
            padding:'1px 6px', color:'var(--muted)',
            whiteSpace:'nowrap', lineHeight:1.25,
          }}>
            {position.strategy_name}
          </span>
        </div>
      )}

      {/* Row 1b: route + timestamps */}
      <div style={{
        display:'flex', justifyContent:'space-between', alignItems:'center',
        padding:'5px 12px 2px 18px',
      }}>
        <div style={{ display:'flex', alignItems:'center', gap:'4px' }}>
          <HeaderPill variant={isAI ? 'ai' : 'neutral'}>{source}</HeaderPill>
          <span style={{
            fontSize:'10px', color:'var(--dim)',
            fontFamily:'monospace', fontWeight:'bold',
          }}>→</span>
          <HeaderPill variant="neutral">{destination}</HeaderPill>
        </div>
        <div style={{
          display:'flex', flexDirection:'column',
          alignItems:'flex-end', gap: isClosed ? '6px' : '0px',
        }}>
          {isClosed ? (
            <>
              <span style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
                fontWeight:500, color:'var(--muted)', lineHeight:1.1,
              }}>
                Opened: {formatAbsolute(position.opened_at)}
              </span>
              <span style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
                fontWeight:500, color:'var(--muted)', lineHeight:1.1,
              }}>
                Closed: {formatAbsolute(position.closed_at)}
              </span>
            </>
          ) : (
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
              fontWeight:500, color:'var(--muted)', lineHeight:1.1,
            }}>
              Opened: {formatRelative(position.opened_at)}
            </span>
          )}
        </div>
      </div>

      {/* Data grid */}
      <DataGrid rows={[topRow, botRow]} />

      {/* Action band or closed band */}
      {isClosed ? (
        <div style={{
          background:'var(--gray-a)', borderTop:'1px solid var(--border)',
          padding:'6px 12px 6px 18px', display:'flex', alignItems:'center',
        }}>
          <span style={{
            textTransform:'uppercase', fontFamily:'JetBrains Mono, monospace',
            fontSize:'9px', letterSpacing:'.05em', fontWeight:700,
            color:'var(--gray)', marginLeft:'auto',
          }}>
            {position.close_reason || 'Closed'}
          </span>
        </div>
      ) : (
        actionButtons.length > 0 && (
          <ActionBand buttons={actionButtons} />
        )
      )}
    </div>
  );
}

export default function Positions() {
  console.log('Positions component render start');
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading]     = useState(true);

  const [filterAsset,    setFilterAssetRaw]    = useState<string>(() => sessionStorage.getItem('matp_pos_asset')    ?? 'all');
  const [filterStatus,   setFilterStatusRaw]   = useState<string>(() => sessionStorage.getItem('matp_pos_status')   ?? 'all');
  const [filterStrategy, setFilterStrategyRaw] = useState<string>(() => sessionStorage.getItem('matp_pos_strategy') ?? 'all');
  const setFilterAsset    = (v: string) => { sessionStorage.setItem('matp_pos_asset', v);    setFilterAssetRaw(v);    };
  const setFilterStatus   = (v: string) => { sessionStorage.setItem('matp_pos_status', v);   setFilterStatusRaw(v);   };
  const setFilterStrategy = (v: string) => { sessionStorage.setItem('matp_pos_strategy', v); setFilterStrategyRaw(v); };

  const fetchPositions = useCallback(async () => {
    console.log('fetchPositions starting...');
    try {
      const res  = await fetch('/api/dashboard/positions');
      console.log('fetch response status:', res.status);
      const data = await res.json();
      console.log('fetch data received, count:', Array.isArray(data) ? data.length : 'not an array');
      setPositions(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('fetchPositions error:', err);
      setPositions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const hasOpen = positions.some(p => p.status === 'open');

  useEffect(() => {
    fetchPositions();
    // Poll every 3s when live positions exist, 15s otherwise
    const interval = setInterval(fetchPositions, hasOpen ? 3000 : 15000);
    return () => clearInterval(interval);
  }, [fetchPositions, hasOpen]);

  const uniqueAssets = Array.from(
    new Set(positions.map(p => p.symbol))
  ).sort();

  const uniqueStrategies = Array.from(
    new Set(positions.map(p => p.strategy_name).filter(Boolean))
  ).sort() as string[];

  const handleClose = async (id: string) => {
    if (!confirm('Close this position?')) return;
    try {
      const res = await fetch(`/api/dashboard/positions/${id}/close`, { method: 'POST' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert(`Failed to close position: ${body.error || res.statusText}`);
        return;
      }
      await fetchPositions();
    } catch (err) {
      console.error('handleClose error:', err);
      alert('Failed to close position: network error');
    }
  };

  const handleRefresh = async (id: string) => {
    try {
      await fetch(`/api/dashboard/positions/${id}/refresh`, { method: 'POST' });
      fetchPositions();
    } catch (err) {
      console.error('handleRefresh error:', err);
    }
  };

  try {
    const filtered = positions.filter(p => {
      if (filterAsset    !== 'all' && p.symbol        !== filterAsset)    return false;
      if (filterStatus   !== 'all' && p.status        !== filterStatus)   return false;
      if (filterStrategy !== 'all' && p.strategy_name !== filterStrategy) return false;
      return true;
    });

    const live   = filtered.filter(p => p.status === 'open');
    const stale  = filtered.filter(p => p.status === 'stale');
    const closed = filtered.filter(p => p.status === 'closed');

    console.log(`Rendering positions: live=${live.length}, stale=${stale.length}, closed=${closed.length}, loading=${loading}`);

    if (loading) {
      return (
        <div style={{ padding:'24px', color:'var(--dim)' }}>
          Loading positions...
        </div>
      );
    }

    return (
      <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>

        <TopBar
          title="Positions"
          right={
            <>
              <span style={{
                background:'var(--bg3)', border:'1px solid var(--border)',
                borderRadius:'20px', padding:'4px 11px',
                fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
                color:'var(--muted)',
              }}>
                {live.length} active
              </span>
              <button
                onClick={fetchPositions}
                style={{
                  display:'flex', alignItems:'center', gap:'5px',
                  background:'var(--bg3)', border:'1px solid var(--border)',
                  borderRadius:'20px', padding:'5px 12px',
                  fontFamily:'JetBrains Mono, monospace', fontSize:'11px',
                  color:'var(--muted)', cursor:'pointer',
                }}>
                ↺
              </button>
            </>
          }
        />

        <div style={{
          display:'flex', gap:'6px', padding:'10px 14px',
          borderBottom:'1px solid var(--border)',
          overflowX:'auto', flexShrink:0, scrollbarWidth:'none',
        }}>
          <select
            value={filterAsset}
            onChange={e => setFilterAsset(e.target.value)}
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
            onChange={e => setFilterStatus(e.target.value)}
            style={{
              background: filterStatus !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
              border: `1px solid ${filterStatus !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
              borderRadius:'20px', padding:'5px 12px', fontSize:'10px',
              fontWeight:500, color: filterStatus !== 'all' ? 'var(--blue)' : 'var(--muted)',
              cursor:'pointer', outline:'none',
            }}>
            <option value="all">All Statuses</option>
            <option value="open">Live</option>
            <option value="stale">Stale</option>
            <option value="closed">Closed</option>
          </select>

          <select
            value={filterStrategy}
            onChange={e => setFilterStrategy(e.target.value)}
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

          {live.length > 0 && (
            <>
              <SectionHeader label="Live"   count={live.length}   variant="live" />
              {live.map(p => (
                <PositionCard key={p.id} position={p}
                  onClose={handleClose} onRefresh={handleRefresh} />
              ))}
            </>
          )}

          {stale.length > 0 && (
            <>
              <SectionHeader label="Stale"  count={stale.length}  variant="stale" />
              {stale.map(p => (
                <PositionCard key={p.id} position={p}
                  onClose={handleClose} onRefresh={handleRefresh} />
              ))}
            </>
          )}

          {closed.length > 0 && (
            <>
              <SectionHeader label="Closed" count={closed.length} variant="closed" />
              {closed.map(p => (
                <PositionCard key={p.id} position={p}
                  onClose={handleClose} onRefresh={handleRefresh} />
              ))}
            </>
          )}

          {positions.length === 0 && (
            <p style={{ color:'var(--dim)', textAlign:'center', padding:'40px 0' }}>
              No open positions.
            </p>
          )}

        </div>
      </div>
    );
  } catch (err) {
    console.error('Positions render error:', err);
    return (
      <div style={{ padding: '24px', color: 'red' }}>
        Render error: {String(err)}
      </div>
    );
  }
}
