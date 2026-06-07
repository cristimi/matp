import { useState, useEffect, useCallback } from 'react';
import { TopBar } from '../components/shared/TopBar';
import { formatRelative } from '../utils/datetime';

// ── Types ─────────────────────────────────────────────────────────────

interface ExecutionLog {
  oel_id:            number;
  exchange:          string;
  exchange_order_id: string | null;
  client_order_id:   string;
  oel_symbol:        string;
  oel_side:          string;
  oel_order_type:    string;
  requested_size:    string;
  oel_status:        string;
  oel_error_message: string | null;
}

interface SignalRow {
  id:           number;
  received_at:  string;
  source_ip:    string | null;
  strategy_id:  string | null;
  http_status:  number | null;
  outcome:      string | null;
  error_detail: string | null;
  raw_body:     Record<string, unknown> | null;
  duration_ms:  number | null;
  // joined execution log (nullable if no order was placed)
  oel_id:            number | null;
  exchange:          string | null;
  exchange_order_id: string | null;
  client_order_id:   string | null;
  oel_symbol:        string | null;
  oel_side:          string | null;
  requested_size:    string | null;
  oel_status:        string | null;
  oel_error_message: string | null;
}

// ── Outcome badge ─────────────────────────────────────────────────────

type Outcome =
  | 'filled'
  | 'route_failed'
  | 'auth_failed'
  | 'guard_rejected'
  | 'validation_failed'
  | 'symbol_rejected'
  | 'accepted'
  | string;

const OUTCOME_STYLE: Record<string, { bg: string; color: string; border: string }> = {
  filled:             { bg: 'var(--green-a)',        color: 'var(--green)',        border: 'var(--green-b)' },
  accepted:           { bg: 'var(--blue-a)',          color: 'var(--blue)',         border: 'var(--blue-b)' },
  guard_rejected:     { bg: 'rgba(234,88,12,.10)',    color: '#ea580c',             border: 'rgba(234,88,12,.25)' },
  validation_failed:  { bg: 'rgba(234,88,12,.10)',    color: '#ea580c',             border: 'rgba(234,88,12,.25)' },
  symbol_rejected:    { bg: 'rgba(234,88,12,.10)',    color: '#ea580c',             border: 'rgba(234,88,12,.25)' },
  auth_failed:        { bg: 'var(--red-a)',            color: 'var(--red)',          border: 'var(--red-b)' },
  route_failed:       { bg: 'var(--bg3)',              color: 'var(--muted)',        border: 'var(--border)' },
};

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  const key = outcome ?? 'pending';
  const style = OUTCOME_STYLE[key] ?? { bg: 'var(--bg3)', color: 'var(--muted)', border: 'var(--border)' };
  return (
    <span style={{
      fontFamily:    'JetBrains Mono, monospace',
      fontSize:      '10px',
      fontWeight:    700,
      letterSpacing: '.04em',
      textTransform: 'uppercase',
      borderRadius:  'var(--pill-r)',
      padding:       '2px 7px',
      border:        '1px solid ' + style.border,
      background:    style.bg,
      color:         style.color,
      whiteSpace:    'nowrap',
      flexShrink:    0,
    }}>
      {key.replace(/_/g, ' ')}
    </span>
  );
}

// ── Signal row card ───────────────────────────────────────────────────

function SignalCard({ row }: { row: SignalRow }) {
  const [expanded, setExpanded] = useState(false);

  const symbol = row.oel_symbol
    ?? (row.raw_body?.base_asset && row.raw_body?.quote_asset
       ? `${row.raw_body.base_asset}-${row.raw_body.quote_asset}`
       : null);

  const side    = row.oel_side    ?? (row.raw_body?.side    as string | null);
  const size    = row.requested_size ?? (row.raw_body?.size as string | null);

  const sideColor =
    side === 'buy'  ? 'var(--green)' :
    side === 'sell' ? 'var(--red)'   : 'var(--muted)';

  return (
    <div
      style={{
        background:    'var(--bg3)',
        border:        '1px solid var(--border)',
        borderRadius:  'var(--r)',
        marginBottom:  '8px',
        overflow:      'hidden',
      }}
    >
      {/* ── Main row ── */}
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          display:    'flex',
          alignItems: 'center',
          gap:        '8px',
          padding:    '10px 14px',
          cursor:     'pointer',
          userSelect: 'none',
        }}
      >
        {/* Timestamp */}
        <span style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize:   '11px',
          color:      'var(--muted)',
          flexShrink: 0,
          minWidth:   '110px',
        }}>
          {formatRelative(row.received_at)}
        </span>

        {/* Strategy */}
        <span style={{
          fontFamily:  'JetBrains Mono, monospace',
          fontSize:    '11px',
          fontWeight:  600,
          color:       'var(--text)',
          flexShrink:  0,
          maxWidth:    '140px',
          overflow:    'hidden',
          textOverflow:'ellipsis',
          whiteSpace:  'nowrap',
        }}>
          {row.strategy_id ?? '—'}
        </span>

        {/* Outcome badge */}
        <OutcomeBadge outcome={row.outcome} />

        {/* Symbol */}
        <span style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize:   '12px',
          fontWeight: 700,
          color:      'var(--text)',
          flexShrink: 0,
        }}>
          {symbol ?? '—'}
        </span>

        {/* Side */}
        {side && (
          <span style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize:   '10px',
            fontWeight: 700,
            color:      sideColor,
            flexShrink: 0,
          }}>
            {side.toUpperCase()}
          </span>
        )}

        {/* Size */}
        {size && (
          <span style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize:   '11px',
            color:      'var(--muted)',
            flexShrink: 0,
          }}>
            {size}
          </span>
        )}

        {/* Duration */}
        <span style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize:   '10px',
          color:      'var(--dim)',
          marginLeft: 'auto',
          flexShrink: 0,
        }}>
          {row.duration_ms != null ? `${row.duration_ms}ms` : '—'}
        </span>

        {/* Chevron */}
        <span style={{
          fontSize:   '10px',
          color:      'var(--dim)',
          flexShrink: 0,
          transform:  expanded ? 'rotate(180deg)' : 'none',
          transition: 'transform .15s',
        }}>
          ▾
        </span>
      </div>

      {/* ── Expanded detail ── */}
      {expanded && (
        <div style={{
          borderTop:  '1px solid var(--border)',
          padding:    '14px',
          display:    'flex',
          flexDirection: 'column',
          gap:        '12px',
        }}>
          {/* Error detail */}
          {row.error_detail && (
            <div>
              <div style={{ fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                            letterSpacing:'.1em', color:'var(--red)', marginBottom:'4px' }}>
                Error
              </div>
              <div style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'11px',
                color:'var(--red)', wordBreak:'break-word',
              }}>
                {row.error_detail}
              </div>
            </div>
          )}

          {/* Execution log */}
          {row.oel_id && (
            <div>
              <div style={{ fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                            letterSpacing:'.1em', color:'var(--muted)', marginBottom:'6px' }}>
                Execution Log
              </div>
              <div style={{
                display:'grid', gridTemplateColumns:'repeat(2,1fr)',
                gap:'6px',
              }}>
                {[
                  { label:'Exchange',   value: row.exchange },
                  { label:'Status',     value: row.oel_status },
                  { label:'Symbol',     value: row.oel_symbol },
                  { label:'Side',       value: row.oel_side },
                  { label:'Size',       value: row.requested_size },
                  { label:'Order ID',   value: row.exchange_order_id },
                  { label:'Client ID',  value: row.client_order_id ? row.client_order_id.slice(0, 8) + '…' : null },
                  { label:'Exec Error', value: row.oel_error_message },
                ].filter(c => c.value).map(cell => (
                  <div key={cell.label} style={{
                    background:'var(--bg2)', borderRadius:'6px',
                    padding:'6px 10px', border:'1px solid var(--border)',
                  }}>
                    <div style={{ fontSize:'9px', fontWeight:600, textTransform:'uppercase',
                                  letterSpacing:'.1em', color:'var(--dim)', marginBottom:'2px' }}>
                      {cell.label}
                    </div>
                    <div style={{
                      fontFamily:'JetBrains Mono, monospace', fontSize:'11px',
                      color:'var(--text)', wordBreak:'break-all',
                    }}>
                      {cell.value}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Raw body */}
          {row.raw_body && (
            <div>
              <div style={{ fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                            letterSpacing:'.1em', color:'var(--muted)', marginBottom:'4px' }}>
                Raw Payload
              </div>
              <pre style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
                color:'var(--muted)', background:'var(--bg2)',
                border:'1px solid var(--border)', borderRadius:'6px',
                padding:'10px', margin:0, overflowX:'auto',
                whiteSpace:'pre-wrap', wordBreak:'break-word',
              }}>
                {JSON.stringify(row.raw_body, null, 2)}
              </pre>
            </div>
          )}

          {/* Meta */}
          <div style={{ display:'flex', gap:'8px', flexWrap:'wrap' }}>
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
              color:'var(--dim)',
            }}>
              HTTP {row.http_status ?? '—'}
            </span>
            {row.source_ip && (
              <span style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
                color:'var(--dim)',
              }}>
                from {row.source_ip}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Filter bar select ─────────────────────────────────────────────────

function FilterSelect({
  value, onChange, active, children,
}: {
  value: string;
  onChange: (v: string) => void;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{
        background:   active ? 'var(--blue-a)' : 'var(--bg2)',
        border:       `1px solid ${active ? 'var(--blue)' : 'var(--border)'}`,
        borderRadius: '20px',
        padding:      '5px 12px',
        fontSize:     '10px',
        fontWeight:   500,
        color:        active ? 'var(--blue)' : 'var(--muted)',
        cursor:       'pointer',
        outline:      'none',
      }}
    >
      {children}
    </select>
  );
}

// ── Page ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

const OUTCOMES = [
  'filled', 'route_failed', 'auth_failed',
  'guard_rejected', 'validation_failed', 'symbol_rejected', 'accepted',
];

export default function SignalLog() {
  const [rows,    setRows]    = useState<SignalRow[]>([]);
  const [total,   setTotal]   = useState(0);
  const [loading, setLoading] = useState(true);
  const [page,    setPage]    = useState(1);

  const [filterStrategy, setFilterStrategy] = useState('all');
  const [filterOutcome,  setFilterOutcome]  = useState('all');
  const [strategies,     setStrategies]     = useState<string[]>([]);

  // Fetch distinct strategies for the filter dropdown
  useEffect(() => {
    fetch('/api/dashboard/signals/strategies')
      .then(r => r.json())
      .then(setStrategies)
      .catch(() => {});
  }, []);

  const fetchRows = useCallback(async (pageNum: number, append: boolean) => {
    try {
      const params = new URLSearchParams({
        page:  String(pageNum),
        limit: String(PAGE_SIZE),
      });
      if (filterStrategy !== 'all') params.set('strategy_id', filterStrategy);
      if (filterOutcome  !== 'all') params.set('outcome',     filterOutcome);

      const res  = await fetch(`/api/dashboard/signals?${params}`);
      const data = await res.json();
      setTotal(data.total ?? 0);
      setRows(prev => append ? [...prev, ...data.items] : data.items);
    } catch {
      if (!append) setRows([]);
    } finally {
      setLoading(false);
    }
  }, [filterStrategy, filterOutcome]);

  useEffect(() => {
    setLoading(true);
    setPage(1);
    fetchRows(1, false);
    const id = setInterval(() => fetchRows(1, false), 15000);
    return () => clearInterval(id);
  }, [fetchRows]);

  const handleLoadMore = () => {
    const next = page + 1;
    setPage(next);
    fetchRows(next, true);
  };

  const clearFilters = () => {
    setFilterStrategy('all');
    setFilterOutcome('all');
  };

  const anyFilter = filterStrategy !== 'all' || filterOutcome !== 'all';

  if (loading) {
    return <div style={{ padding:'24px', color:'var(--dim)' }}>Loading signal log…</div>;
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>

      <TopBar
        title="Signal Log"
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

      {/* ── Filter bar ── */}
      <div style={{
        display:'flex', gap:'6px', padding:'10px 14px',
        borderBottom:'1px solid var(--border)',
        overflowX:'auto', flexShrink:0, scrollbarWidth:'none',
      }}>
        <FilterSelect
          value={filterStrategy}
          onChange={v => { setFilterStrategy(v); setPage(1); }}
          active={filterStrategy !== 'all'}
        >
          <option value="all">All Strategies</option>
          {strategies.map(s => <option key={s} value={s}>{s}</option>)}
        </FilterSelect>

        <FilterSelect
          value={filterOutcome}
          onChange={v => { setFilterOutcome(v); setPage(1); }}
          active={filterOutcome !== 'all'}
        >
          <option value="all">All Outcomes</option>
          {OUTCOMES.map(o => (
            <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>
          ))}
        </FilterSelect>

        {anyFilter && (
          <span
            onClick={clearFilters}
            style={{
              whiteSpace:'nowrap', background:'var(--bg2)',
              border:'1px solid var(--border)', borderRadius:'20px',
              padding:'5px 12px', fontSize:'10px', fontWeight:500,
              color:'var(--red)', cursor:'pointer',
            }}
          >
            ✕ Clear
          </span>
        )}
      </div>

      {/* ── List ── */}
      <div style={{
        flex:1, overflowY:'auto', padding:'10px 14px 80px',
        scrollbarWidth:'none',
      }}>
        {rows.length === 0 ? (
          <p style={{ color:'var(--dim)', textAlign:'center', padding:'40px 0' }}>
            No signal log entries found.
          </p>
        ) : (
          rows.map(row => <SignalCard key={row.id} row={row} />)
        )}

        {total > rows.length && (
          <div style={{ textAlign:'center', padding:'16px 0 24px' }}>
            <button
              onClick={handleLoadMore}
              style={{
                padding:'8px 20px',
                border:'1px solid var(--border)', borderRadius:'20px',
                background:'var(--bg2)', fontSize:'12px', fontWeight:600,
                color:'var(--blue)', cursor:'pointer',
              }}
            >
              Load {Math.min(PAGE_SIZE, total - rows.length)} more
              &nbsp;({rows.length} / {total})
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
