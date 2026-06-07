import { useState, useEffect, useCallback } from 'react';
import { SectionHeader } from '../components/shared';

interface Strategy {
  id:                   string;
  name:                 string;
  symbol:               string;
  interval:             string;
  account_id:           string;
  account_label:        string;
  account_exchange:     string;
  account_mode:         string;
  enabled:              boolean;
  allow_quote_variants: boolean;
  allow_cross_charting: boolean;
  default_leverage?:      number;
  margin_mode?:           'isolated' | 'cross';
  max_leverage?:          number;
  open_positions_count?:  number;
  max_position_size?:          number;
  max_daily_signals?:          number;
  max_daily_drawdown_percent?: number;
  signals_today:        number;
  pnl_total:            string;
  // From strategy_stats if available — default to 0/null if missing
  open_positions?:      number;
  win_positions?:       number;
  loss_positions?:      number;
  win_rate?:            number;
  allocated?:           number;
  realized_pnl?:        number;
  pnl_fees?:            number;
  total_return?:        number;
  uptime_label?:        string;
  last_signal_at?:      string;
  stopped_at?:          string;
}

// Relative date formatter
function formatRelativeDate(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime());
  yesterday.setDate(yesterday.getDate() - 1);

  const hhmm = date.toTimeString().slice(0, 5);
  if (date >= today)       return `Today ${hhmm}`;
  if (date >= yesterday)   return `Yesterday ${hhmm}`;
  return date.toLocaleDateString('en-GB', {
    day:'2-digit', month:'2-digit', year:'2-digit'
  });
}

// Pill component
type PillVariant = 'lev' | 'tech' | 'open' | 'closed' | 'neutral';
function Pill({ variant, children }: {
  variant: PillVariant; children: React.ReactNode
}) {
  const styles: Record<PillVariant, React.CSSProperties> = {
    lev:     { background:'var(--blue-a)',   color:'var(--blue)',  borderColor:'var(--blue-b)',  textTransform:'lowercase' },
    tech:    { background:'var(--blue-a)',   color:'var(--blue)',  borderColor:'var(--blue-b)' },
    open:    { background:'var(--green-a)',  color:'var(--green)', borderColor:'var(--green-b)' },
    closed:  { background:'var(--gray-a)',   color:'var(--gray)',  borderColor:'var(--gray-b)' },
    neutral: { background:'var(--bg2)',      color:'var(--muted)', borderColor:'var(--border)', textTransform:'none' as const },
  };
  return (
    <span style={{
      fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
      fontWeight:600, textTransform:'uppercase',
      borderRadius:'var(--pill-r)', padding:'2px 6px',
      border:'1px solid', display:'inline-block',
      lineHeight:1, flexShrink:0, letterSpacing:'.04em',
      ...styles[variant],
    }}>
      {children}
    </span>
  );
}

// Grid cell component
function GridCell({ label, children, last }: {
  label: string; children: React.ReactNode; last?: boolean
}) {
  return (
    <div style={{
      flex:1, padding:'6px 10px', display:'flex',
      flexDirection:'column', gap:'1px',
      borderRight: last ? 'none' : '1px solid var(--border)',
    }}>
      <span style={{
        fontSize:'9px', fontWeight:600, letterSpacing:'.11em',
        textTransform:'uppercase', color:'var(--dim)', marginBottom:'2px',
      }}>
        {label}
      </span>
      {children}
    </div>
  );
}

// Coupling toggle
function CouplingToggle({ label, checked, onChange, warn }: {
  label: string; checked: boolean;
  onChange: (v: boolean) => void; warn?: boolean;
}) {
  return (
    <label style={{
      display:'flex', alignItems:'center', gap:'6px',
      cursor:'pointer', userSelect:'none',
    }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ accentColor: warn && checked ? 'var(--failed-color)' : 'var(--blue)' }}
      />
      <span style={{
        fontSize:'10px', fontWeight:600, letterSpacing:'.06em',
        textTransform:'uppercase',
        color: warn && checked ? 'var(--failed-color)' : 'var(--dim)',
      }}>
        {label}
      </span>
    </label>
  );
}

function StrategyCard({
  strategy,
  onCouplingChange,
  onStop,
  onStart,
  onEdit,
  onDelete,
}: {
  strategy: Strategy;
  onCouplingChange: (id: string, field: string, value: boolean) => void;
  onStop: (s: Strategy) => void;
  onStart: (id: string) => void;
  onEdit: (s: Strategy) => void;
  onDelete: (id: string) => void;
}) {
  const isActive = strategy.enabled;
  const barColor = isActive ? 'var(--green)' : 'var(--gray)';

  const wins  = strategy.win_positions  ?? 0;
  const losses = strategy.loss_positions ?? 0;
  const total  = strategy.open_positions_count ?? 0;
  const winRate = strategy.win_rate ?? 0;
  const allocated = strategy.allocated ?? 0;
  const realizedPnl = parseFloat(strategy.pnl_total || '0');
  const pnlFees = strategy.pnl_fees ?? 0;
  const totalReturn = strategy.total_return ?? 0;

  const pnlColor = realizedPnl >= 0 ? 'var(--green)' : 'var(--red)';
  const returnColor = totalReturn >= 0 ? 'var(--green)' : 'var(--red)';
  const pnlSign = realizedPnl >= 0 ? '+' : '';
  const returnSign = totalReturn >= 0 ? '+' : '';

  // Source → destination from account info
  const source = 'TradingView';
  const destination = strategy.account_exchange
    ? `${strategy.account_exchange}${strategy.account_mode === 'demo' ? '(demo)' : ''}`
    : (strategy.account_id || '—');

  const uptimeLabel = isActive
    ? (strategy.uptime_label || 'Active')
    : (strategy.stopped_at
        ? `Stopped: ${formatRelativeDate(strategy.stopped_at)}`
        : 'Inactive');

  const lastSignal = strategy.last_signal_at
    ? `Last signal: ${formatRelativeDate(strategy.last_signal_at)}`
    : null;

  return (
    <div style={{
      background:   'var(--bg3)',
      borderRadius: 'var(--r)',
      border:       `1px solid ${isActive ? 'var(--border)' : 'var(--border-hi)'}`,
      marginBottom: '10px',
      position:     'relative',
      display:      'flex',
      flexDirection:'column',
      overflow:     'hidden',
    }}>
      {/* Left bar */}
      <div style={{
        position:  'absolute', left:0, top:0, bottom:0,
        width:     '4px', background: barColor, zIndex: 1,
      }} />

      {/* Row 1: symbol + pills */}
      <div style={{
        display:'flex', alignItems:'center', gap:'6px',
        padding:'12px 12px 0 18px', lineHeight:1,
      }}>
        {/* Active dot — only when positions are open */}
        {(strategy.open_positions_count ?? 0) > 0 && (
          <span style={{
            width:'8px', height:'8px', borderRadius:'50%',
            background:'var(--green)', flexShrink:0,
            display:'inline-block', marginRight:'2px',
          }} />
        )}
        <span style={{
          fontSize:'16px', fontWeight:700, letterSpacing:'-.01em',
          color:'var(--text)', whiteSpace:'nowrap', flexShrink:0,
          marginRight:'2px',
        }}>
          {strategy.symbol}
        </span>
        <Pill variant="lev">
          {strategy.default_leverage ?? 1}x / {strategy.max_leverage ?? 10}x
        </Pill>
        <Pill variant="tech">Cross</Pill>
        <div style={{ marginLeft:'auto' }}>
          <Pill variant={isActive ? 'open' : 'closed'}>
            {isActive ? 'active' : 'inactive'}
          </Pill>
        </div>
      </div>

      {/* Strat row: name + ID */}
      <div style={{
        padding:'5px 12px 0 18px',
        display:'flex', gap:'6px', alignItems:'center',
      }}>
        <span style={{
          fontFamily:   'JetBrains Mono, monospace',
          fontSize:     '10px', fontWeight:700, letterSpacing:'.04em',
          background:   'var(--bg3)', border:'1px solid var(--border)',
          borderRadius: 'var(--pill-r)', padding:'2px 6px',
          color:'var(--muted)', textTransform:'uppercase',
        }}>
          {strategy.name}
        </span>
        <span style={{
          fontFamily:   'JetBrains Mono, monospace',
          fontSize:     '10px', fontWeight:500, letterSpacing:'.04em',
          background:   'transparent',
          border:       '1px dashed var(--border-hi)',
          borderRadius: 'var(--pill-r)', padding:'2px 6px',
          color:'var(--dim)',
        }}>
          ID: {strategy.id.slice(0, 8)}
        </span>
        {/* Cross-charting warning badge */}
        {strategy.allow_cross_charting && (
          <span style={{
            fontFamily:   'JetBrains Mono, monospace',
            fontSize:     '9px', fontWeight:700, letterSpacing:'.06em',
            background:   'var(--failed-color-a)',
            border:       '1px solid var(--failed-color-b)',
            borderRadius: 'var(--pill-r)', padding:'2px 6px',
            color:        'var(--failed-color)', textTransform:'uppercase',
          }}>
            ⚠ Cross-Chart
          </span>
        )}
      </div>

      {/* Row 1b: route + time */}
      <div style={{
        display:'flex', justifyContent:'space-between', alignItems:'center',
        padding:'5px 12px 4px 18px',
      }}>
        <div style={{ display:'flex', alignItems:'center', gap:'4px' }}>
          <Pill variant="neutral">{source}</Pill>
          <span style={{
            fontSize:'10px', color:'var(--dim)',
            fontFamily:'monospace', fontWeight:'bold',
          }}>→</span>
          <Pill variant="neutral">{destination}</Pill>
        </div>
        <div style={{
          display:'flex', flexDirection:'column',
          alignItems:'flex-end', gap:'2px',
        }}>
          <span style={{
            fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
            fontWeight:500, color:'var(--muted)', lineHeight:1.1,
          }}>
            {uptimeLabel}
          </span>
          {lastSignal && (
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
              fontWeight:500, color:'var(--muted)', lineHeight:1.1,
            }}>
              {lastSignal}
            </span>
          )}
        </div>
      </div>

      {/* Data grid */}
      <div style={{
        display:'flex', flexDirection:'column',
        margin:'8px 12px 8px 18px',
        borderRadius:'var(--pill-r)', overflow:'hidden',
        border:'1px solid var(--border)',
        background:'rgba(226,232,240,.4)',
      }}>
        {/* Top row */}
        <div style={{ display:'flex', width:'100%',
                      borderBottom:'1px solid var(--border)' }}>
          <GridCell label="Positions">
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
              fontWeight:700, color:'var(--text)',
            }}>
              {total} (
              <span style={{ color:'var(--green)' }}>{wins}</span>/
              <span style={{ color:'var(--red)' }}>{losses}</span>
              )
            </span>
          </GridCell>
          <GridCell label="Win Rate">
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
              fontWeight:700, color:'var(--text)',
            }}>
              {winRate.toFixed(1)}%
            </span>
          </GridCell>
          <GridCell label="Allocated" last>
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
              fontWeight:600, color:'var(--text)',
            }}>
              {allocated.toFixed(1)}
            </span>
          </GridCell>
        </div>
        {/* Bottom row */}
        <div style={{ display:'flex', width:'100%' }}>
          <GridCell label="Spare">
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
              fontWeight:600, color:'var(--text)',
            }}>—</span>
          </GridCell>
          <GridCell label="P&L (Realized)">
            <div style={{ display:'flex', alignItems:'baseline', gap:'4px' }}>
              <span style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
                fontWeight:700, color: pnlColor,
              }}>
                {pnlSign}{realizedPnl.toFixed(1)}
              </span>
              {pnlFees !== 0 && (
                <span style={{
                  fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
                  fontWeight:600, color:'var(--red)', opacity:.7,
                }}>
                  (−{Math.abs(pnlFees).toFixed(1)})
                </span>
              )}
            </div>
          </GridCell>
          <GridCell label="Total Return" last>
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
              fontWeight:700, color: returnColor,
            }}>
              {returnSign}{totalReturn.toFixed(2)}%
            </span>
          </GridCell>
        </div>
      </div>

      {/* Symbol Coupling toggles */}
      <div style={{
        display:'flex', gap:'16px', padding:'6px 12px 8px 18px',
        borderTop:'1px solid var(--border)',
        background:'var(--bg2)',
      }}>
        <CouplingToggle
          label="Quote Variants"
          checked={strategy.allow_quote_variants}
          onChange={(v) => onCouplingChange(strategy.id, 'allow_quote_variants', v)}
        />
        <CouplingToggle
          label="Cross-Charting"
          checked={strategy.allow_cross_charting}
          onChange={(v) => onCouplingChange(strategy.id, 'allow_cross_charting', v)}
          warn={strategy.allow_cross_charting}
        />
      </div>

      {/* Action band */}
      <div style={{
        borderTop:'1px solid var(--border)', background:'var(--bg2)',
        display:'flex',
      }}>
        {isActive ? (
          <button
            onClick={() => onStop(strategy)}
            style={{
              flex:1, background:'transparent', border:'none',
              color:'var(--red)', fontSize:'11px', fontWeight:700,
              letterSpacing:'.06em', textTransform:'uppercase',
              padding:'10px', cursor:'pointer', textAlign:'center',
            }}>
            ⏹ Stop Strategy
          </button>
        ) : (
          <>
            <button
              onClick={() => onStart(strategy.id)}
              style={{
                flex:1, background:'transparent', border:'none',
                color:'var(--green)', fontSize:'11px', fontWeight:700,
                letterSpacing:'.06em', textTransform:'uppercase',
                padding:'10px', cursor:'pointer', textAlign:'center',
                borderRight:'1px solid var(--border)',
              }}>
              ▶ Start
            </button>
            <button
              onClick={() => onEdit(strategy)}
              style={{
                flex:1, background:'transparent', border:'none',
                color:'var(--blue)', fontSize:'11px', fontWeight:700,
                letterSpacing:'.06em', textTransform:'uppercase',
                padding:'10px', cursor:'pointer', textAlign:'center',
                borderRight:'1px solid var(--border)',
              }}>
              ✎ Edit
            </button>
            {(strategy.open_positions_count ?? 0) === 0 && (
              <button
                onClick={() => onDelete(strategy.id)}
                style={{
                  flex:1, background:'transparent', border:'none',
                  color:'var(--red)', fontSize:'11px', fontWeight:700,
                  letterSpacing:'.06em', textTransform:'uppercase',
                  padding:'10px', cursor:'pointer', textAlign:'center',
                }}>
                ✕ Delete
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width:'100%', padding:'8px 12px',
  border:'1px solid var(--border)', borderRadius:'8px',
  fontSize:'13px', background:'var(--bg3)',
  color:'var(--text)', outline:'none', boxSizing:'border-box',
};

const labelStyle: React.CSSProperties = {
  display:'block', fontSize:'11px', fontWeight:600,
  textTransform:'uppercase', letterSpacing:'.08em',
  color:'var(--dim)', marginBottom:'4px',
};

function FieldRow({ label, children }: {
  label: string; children: React.ReactNode
}) {
  return (
    <div style={{ marginBottom:'14px' }}>
      <label style={labelStyle}>{label}</label>
      {children}
    </div>
  );
}

export default function Strategies() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading]       = useState(true);

  const [filterPair,   setFilterPair]   = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');

  const [showAdd, setShowAdd]         = useState(false);
  const [addForm, setAddForm]         = useState({
    name:                       '',
    symbol:                     '',
    account_id:                 '',
    interval:                   '1h',
    default_leverage:           '1',
    max_position_size:          '1.0',
    max_leverage:               '10',
    max_daily_signals:          '500',
    max_daily_drawdown_percent: '20',
    allow_quote_variants:       false,
    allow_cross_charting:       false,
  });
  const [accounts, setAccounts]       = useState<{id:string; label:string; exchange:string; mode:string}[]>([]);
  const [addError, setAddError]       = useState<string | null>(null);
  const [addLoading, setAddLoading]   = useState(false);
  const [createdSecret, setCreatedSecret] = useState<{id:string; secret:string; url:string} | null>(null);

  const fetchStrategies = useCallback(async () => {
    try {
      const res  = await fetch('/api/dashboard/strategies');
      const data = await res.json();
      setStrategies(Array.isArray(data) ? data : []);
    } catch {
      setStrategies([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAccounts = useCallback(async () => {
    try {
      const res  = await fetch('/api/dashboard/accounts');
      const data = await res.json();
      setAccounts(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  useEffect(() => {
    fetchStrategies();
    fetchAccounts();
    const interval = setInterval(fetchStrategies, 30000);
    return () => clearInterval(interval);
  }, [fetchStrategies, fetchAccounts]);

  const uniquePairs = Array.from(
    new Set(strategies.map(s => s.symbol))
  ).sort();

  const [stopTarget, setStopTarget]   = useState<Strategy | null>(null);
  const [stopping, setStopping]       = useState(false);
  const [stopError, setStopError]     = useState<string | null>(null);

  const handleStop = (strategy: Strategy) => {
    setStopTarget(strategy);
    setStopError(null);
  };

  const confirmStop = async (closePositions: boolean) => {
    if (!stopTarget) return;
    setStopping(true);
    setStopError(null);

    try {
      // If user chose to close positions first
      if (closePositions && (stopTarget.open_positions_count ?? 0) > 0) {
        const closeRes = await fetch(
          `/api/dashboard/positions?strategy_id=${stopTarget.id}`
        );
        const closeData = await closeRes.json();
        const openPositions = (closeData.items ?? closeData ?? [])
          .filter((p: any) => p.status === 'open');

        for (const pos of openPositions) {
          await fetch(`/api/dashboard/positions/${pos.id}/close`,
            { method: 'POST' });
        }
      }

      // Stop the strategy
      const res = await fetch(
        `/api/dashboard/strategies/${stopTarget.id}/stop`,
        { method: 'POST' }
      );
      if (!res.ok) {
        const err = await res.json();
        setStopError(err.error || 'Failed to stop strategy');
        return;
      }

      setStopTarget(null);
      fetchStrategies();
    } catch (e: any) {
      setStopError(e.message);
    } finally {
      setStopping(false);
    }
  };

  const [editTarget, setEditTarget]   = useState<Strategy | null>(null);
  const [editForm, setEditForm]       = useState<any>({});
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError]     = useState<string | null>(null);
  const [webhookInfo, setWebhookInfo] = useState<any>(null);

  const handleEdit = async (strategy: Strategy) => {
    setEditTarget(strategy);
    setEditError(null);
    setEditForm({
      name:                       strategy.name,
      symbol:                     strategy.symbol,
      account_id:                 strategy.account_id,
      margin_mode:                strategy.margin_mode ?? 'isolated',
      default_leverage:           String(strategy.default_leverage ?? 1),
      max_leverage:               String(strategy.max_leverage ?? 10),
      max_position_size:          String(strategy.max_position_size ?? 1),
      max_daily_signals:          String(strategy.max_daily_signals ?? 500),
      max_daily_drawdown_percent: String(strategy.max_daily_drawdown_percent ?? 20),
      allow_quote_variants:       strategy.allow_quote_variants ?? false,
      allow_cross_charting:       strategy.allow_cross_charting ?? false,
    });
    // Fetch webhook info for display
    try {
      const res  = await fetch(`/api/dashboard/strategies/${strategy.id}/webhook-info`);
      const data = await res.json();
      setWebhookInfo(data);
    } catch {
      setWebhookInfo(null);
    }
  };

  const handleEditSubmit = async () => {
    if (!editTarget) return;
    setEditLoading(true);
    setEditError(null);
    try {
      const res = await fetch(`/api/dashboard/strategies/${editTarget.id}`, {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          ...editForm,
          default_leverage:           parseInt(editForm.default_leverage),
          max_leverage:               parseInt(editForm.max_leverage),
          max_position_size:          parseFloat(editForm.max_position_size),
          max_daily_signals:          parseInt(editForm.max_daily_signals),
          max_daily_drawdown_percent: parseFloat(editForm.max_daily_drawdown_percent),
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        setEditError(err.error || 'Failed to update strategy');
        return;
      }
      setEditTarget(null);
      setWebhookInfo(null);
      fetchStrategies();
    } catch (e: any) {
      setEditError(e.message);
    } finally {
      setEditLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Permanently delete this strategy? This cannot be undone.')) return;
    try {
      const res = await fetch(`/api/dashboard/strategies/${id}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const err = await res.json();
        alert(err.error || 'Delete failed');
        return;
      }
      fetchStrategies();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleStart = async (id: string) => {
    await fetch(`/api/dashboard/strategies/${id}/start`, { method: 'POST' });
    fetchStrategies();
  };

  const handleToggle = async (id: string, enable: boolean) => {
    const endpoint = enable ? 'enable' : 'disable';
    await fetch(`/api/dashboard/strategies/${id}/${endpoint}`, { method: 'POST' });
    fetchStrategies();
  };

  const handleCouplingChange = async (
    id: string, field: string, value: boolean
  ) => {
    await fetch(`/api/dashboard/strategies/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value }),
    });
    fetchStrategies();
  };

  const handleAddStrategy = async () => {
    setAddError(null);
    setAddLoading(true);
    try {
      const res = await fetch('/api/dashboard/strategies', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          ...addForm,
          default_leverage:           parseInt(addForm.default_leverage),
          max_position_size:          parseFloat(addForm.max_position_size),
          max_leverage:               parseInt(addForm.max_leverage),
          max_daily_signals:          parseInt(addForm.max_daily_signals),
          max_daily_drawdown_percent: parseFloat(addForm.max_daily_drawdown_percent),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setAddError(data.error || 'Failed to create strategy');
        return;
      }
      // Show the webhook secret — it will not be shown again
      const host = window.location.host;
      setCreatedSecret({
        id:     data.id,
        secret: data.webhook_secret,
        url:    `http://${host}/api/listener/webhook/${data.id}`,
      });
      setShowAdd(false);
      setAddForm({
        name: '', symbol: '', account_id: '', interval: '1h',
        default_leverage: '1',
        max_position_size: '1.0', max_leverage: '10',
        max_daily_signals: '500', max_daily_drawdown_percent: '20',
        allow_quote_variants: false, allow_cross_charting: false,
      });
      fetchStrategies();
    } catch (e: any) {
      setAddError(e.message);
    } finally {
      setAddLoading(false);
    }
  };

  const filtered = strategies.filter(s => {
    if (filterPair   !== 'all' && s.symbol  !== filterPair)   return false;
    if (filterStatus !== 'all' && filterStatus === 'active'   && !s.enabled) return false;
    if (filterStatus !== 'all' && filterStatus === 'inactive' &&  s.enabled) return false;
    return true;
  });
  const active   = filtered.filter(s =>  s.enabled);
  const inactive = filtered.filter(s => !s.enabled);

  if (loading) {
    return (
      <div style={{ padding:'24px', color:'var(--dim)' }}>
        Loading strategies...
      </div>
    );
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>

      {/* Top bar */}
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'space-between',
        padding:'18px 20px 12px', background:'var(--bg2)',
        borderBottom:'1px solid var(--border)', flexShrink:0,
      } as any}>
        <span style={{
          fontSize:'23px', fontWeight:800, letterSpacing:'-.02em',
        }}>
          Strategies
        </span>
        <div style={{ display:'flex', alignItems:'center', gap:'6px' }}>
          <span style={{
            background:'var(--bg3)', border:'1px solid var(--border)',
            borderRadius:'20px', padding:'4px 11px',
            fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
            color:'var(--muted)',
          }}>
            {active.length} Active
          </span>
          <button
            onClick={() => setShowAdd(true)}
            style={{
              display:'flex', alignItems:'center', gap:'5px',
              background:'var(--bg3)', border:'1px solid var(--border)',
              borderRadius:'20px', padding:'5px 12px',
              fontFamily:'JetBrains Mono, monospace', fontSize:'11px',
              color:'var(--muted)', cursor:'pointer',
            }}>
            ＋
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div style={{
        display:'flex', gap:'6px', padding:'10px 14px',
        borderBottom:'1px solid var(--border)',
        overflowX:'auto', flexShrink:0, scrollbarWidth:'none',
      }}>
        {/* Pair filter */}
        <select
          value={filterPair}
          onChange={e => setFilterPair(e.target.value)}
          style={{
            background: filterPair !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
            border: `1px solid ${filterPair !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
            borderRadius:'20px', padding:'5px 12px',
            fontSize:'10px', fontWeight:500,
            color: filterPair !== 'all' ? 'var(--blue)' : 'var(--muted)',
            cursor:'pointer', outline:'none',
          }}>
          <option value="all">All Pairs</option>
          {uniquePairs.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>

        {/* Status filter */}
        <select
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value)}
          style={{
            background: filterStatus !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
            border: `1px solid ${filterStatus !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
            borderRadius:'20px', padding:'5px 12px',
            fontSize:'10px', fontWeight:500,
            color: filterStatus !== 'all' ? 'var(--blue)' : 'var(--muted)',
            cursor:'pointer', outline:'none',
          }}>
          <option value="all">All Statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>

        {/* Clear button — only show when a filter is active */}
        {(filterPair !== 'all' || filterStatus !== 'all') && (
          <span
            onClick={() => { setFilterPair('all'); setFilterStatus('all'); }}
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

      {/* Summary bar */}
      <div style={{
        display:'flex', background:'var(--bg2)',
        borderBottom:'1px solid var(--border)', flexShrink:0,
      }}>
        {[
          { count: active.length,   label: 'Active',   variant: 'live'   },
          { count: inactive.length, label: 'Inactive', variant: 'closed' },
        ].map((cell, i, arr) => (
          <div key={cell.label} style={{
            flex:1, display:'flex', flexDirection:'column',
            alignItems:'center', padding:'10px 0 9px',
            borderRight: i < arr.length - 1 ? '1px solid var(--border)' : 'none',
            gap:'3px', position:'relative',
          }}>
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'24px',
              fontWeight:700, letterSpacing:'-.02em', lineHeight:1,
              color: cell.variant === 'live' ? 'var(--green)' : 'var(--gray)',
            }}>
              {cell.count}
            </span>
            <span style={{
              fontSize:'10px', fontWeight:600, letterSpacing:'.08em',
              textTransform:'uppercase', color:'var(--dim)',
            }}>
              {cell.label}
            </span>
            {/* Bottom accent line */}
            <div style={{
              position:'absolute', bottom:0, left:'18%', right:'18%',
              height:'2px', borderRadius:'2px',
              background: cell.variant === 'live' ? 'var(--green)' : 'var(--gray)',
            }} />
          </div>
        ))}
      </div>

      {/* Scroll area */}
      <div style={{
        flex:1, overflowY:'auto', padding:'14px 14px 80px',
        scrollbarWidth:'none',
      }}>

        {/* Active section */}
        {active.length > 0 && (
          <>
            <SectionHeader label="Active" count={active.length} variant="live" />
            {active.map(s => (
              <StrategyCard
                key={s.id}
                strategy={s}
                onCouplingChange={handleCouplingChange}
                onStop={handleStop}
                onStart={handleStart}
                onEdit={handleEdit}
                onDelete={handleDelete}
              />
            ))}
          </>
        )}

        {/* Inactive section */}
        {inactive.length > 0 && (
          <>
            <SectionHeader label="Inactive" count={inactive.length} variant="closed" />
            {inactive.map(s => (
              <StrategyCard
                key={s.id}
                strategy={s}
                onCouplingChange={handleCouplingChange}
                onStop={handleStop}
                onStart={handleStart}
                onEdit={handleEdit}
                onDelete={handleDelete}
              />
            ))}
          </>
        )}

        {strategies.length === 0 && (
          <p style={{ color:'var(--dim)', textAlign:'center',
                      padding:'40px 0' }}>
            No strategies configured.
          </p>
        )}
      </div>

      {/* Add Strategy Modal */}
      {showAdd && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,.45)',
          display:'flex', alignItems:'center', justifyContent:'center',
          zIndex:1000, overflowY:'auto', padding:'20px',
        }}>
          <div style={{
            background:'var(--bg2)', borderRadius:'var(--r)',
            padding:'28px', width:'420px', maxWidth:'95vw',
            boxShadow:'0 20px 60px rgba(0,0,0,.2)',
          }}>
            <h2 style={{
              fontSize:'18px', fontWeight:700, color:'var(--text)',
              marginBottom:'20px',
            }}>
              Add Strategy
            </h2>

            {addError && (
              <p style={{ color:'var(--red)', fontSize:'13px',
                          marginBottom:'12px' }}>{addError}</p>
            )}

            {/* Name */}
            <div style={{ marginBottom:'14px' }}>
              <label style={{
                display:'block', fontSize:'11px', fontWeight:600,
                textTransform:'uppercase', letterSpacing:'.08em',
                color:'var(--dim)', marginBottom:'4px',
              }}>Strategy Name *</label>
              <input
                value={addForm.name}
                onChange={e => setAddForm(f => ({ ...f, name: e.target.value }))}
                placeholder="e.g. BTC RSI 5m"
                style={{
                  width:'100%', padding:'8px 12px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  fontSize:'13px', background:'var(--bg3)',
                  color:'var(--text)', outline:'none', boxSizing:'border-box',
                }}
              />
            </div>

            {/* Symbol */}
            <div style={{ marginBottom:'14px' }}>
              <label style={{
                display:'block', fontSize:'11px', fontWeight:600,
                textTransform:'uppercase', letterSpacing:'.08em',
                color:'var(--dim)', marginBottom:'4px',
              }}>Execution Symbol *</label>
              <input
                value={addForm.symbol}
                onChange={e => setAddForm(f => ({ ...f, symbol: e.target.value }))}
                placeholder="e.g. BTC-USDT"
                style={{
                  width:'100%', padding:'8px 12px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  fontSize:'13px', fontFamily:'JetBrains Mono, monospace',
                  background:'var(--bg3)', color:'var(--text)',
                  outline:'none', boxSizing:'border-box',
                }}
              />
              <p style={{ fontSize:'11px', color:'var(--dim)', marginTop:'4px' }}>
                The symbol used on the exchange. Use dash format: BTC-USDT
              </p>
            </div>

            {/* Account selector */}
            <div style={{ marginBottom:'14px' }}>
              <label style={{
                display:'block', fontSize:'11px', fontWeight:600,
                textTransform:'uppercase', letterSpacing:'.08em',
                color:'var(--dim)', marginBottom:'4px',
              }}>Exchange Account *</label>
              <select
                value={addForm.account_id}
                onChange={e => setAddForm(f => ({ ...f, account_id: e.target.value }))}
                style={{
                  width:'100%', padding:'8px 12px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  fontSize:'13px', background:'var(--bg3)',
                  color:'var(--text)', outline:'none', boxSizing:'border-box',
                }}>
                <option value="">— Select account —</option>
                {accounts.map(a => (
                  <option key={a.id} value={a.id}>
                    {a.label} ({a.exchange} / {a.mode})
                  </option>
                ))}
              </select>
            </div>

            {/* Interval */}
            <div style={{ marginBottom:'14px' }}>
              <label style={{
                display:'block', fontSize:'11px', fontWeight:600,
                textTransform:'uppercase', letterSpacing:'.08em',
                color:'var(--dim)', marginBottom:'4px',
              }}>Interval</label>
              <select
                value={addForm.interval}
                onChange={e => setAddForm(f => ({ ...f, interval: e.target.value }))}
                style={{
                  width:'100%', padding:'8px 12px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  fontSize:'13px', background:'var(--bg3)',
                  color:'var(--text)', outline:'none', boxSizing:'border-box',
                }}>
                {['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d'].map(i => (
                  <option key={i} value={i}>{i}</option>
                ))}
              </select>
            </div>

            {/* Default Leverage */}
            <div style={{ marginBottom:'14px' }}>
              <label style={{
                display:'block', fontSize:'11px', fontWeight:600,
                textTransform:'uppercase', letterSpacing:'.08em',
                color:'var(--dim)', marginBottom:'4px',
              }}>Default Leverage</label>
              <input
                type="number"
                value={addForm.default_leverage}
                onChange={e => setAddForm(f => ({ ...f, default_leverage: e.target.value }))}
                style={{
                  width:'100%', padding:'8px 12px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  fontSize:'13px', background:'var(--bg3)',
                  color:'var(--text)', outline:'none', boxSizing:'border-box',
                }}
              />
            </div>

            {/* Risk fields — compact 2-column grid */}
            <div style={{
              display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px',
              marginBottom:'14px',
            }}>
              {[
                { key:'max_position_size',          label:'Max Size',       placeholder:'1.0' },
                { key:'max_leverage',               label:'Max Leverage',   placeholder:'10' },
                { key:'max_daily_signals',          label:'Daily Signals',  placeholder:'500' },
                { key:'max_daily_drawdown_percent', label:'Drawdown %',     placeholder:'20' },
              ].map(field => (
                <div key={field.key}>
                  <label style={{
                    display:'block', fontSize:'10px', fontWeight:600,
                    textTransform:'uppercase', letterSpacing:'.07em',
                    color:'var(--dim)', marginBottom:'3px',
                  }}>{field.label}</label>
                  <input
                    type="number"
                    value={(addForm as any)[field.key]}
                    onChange={e => setAddForm(f => ({ ...f, [field.key]: e.target.value }))}
                    placeholder={field.placeholder}
                    style={{
                      width:'100%', padding:'7px 10px',
                      border:'1px solid var(--border)', borderRadius:'8px',
                      fontSize:'12px', fontFamily:'JetBrains Mono, monospace',
                      background:'var(--bg3)', color:'var(--text)',
                      outline:'none', boxSizing:'border-box',
                    }}
                  />
                </div>
              ))}
            </div>

            {/* Symbol coupling toggles */}
            <div style={{
              display:'flex', gap:'20px', marginBottom:'20px',
              padding:'12px 14px',
              background:'var(--bg3)', borderRadius:'8px',
              border:'1px solid var(--border)',
            }}>
              <label style={{ display:'flex', alignItems:'center', gap:'7px',
                               cursor:'pointer', userSelect:'none' }}>
                <input
                  type="checkbox"
                  checked={addForm.allow_quote_variants}
                  onChange={e => setAddForm(f =>
                    ({ ...f, allow_quote_variants: e.target.checked }))}
                />
                <span style={{ fontSize:'11px', fontWeight:600,
                                letterSpacing:'.06em', textTransform:'uppercase',
                                color:'var(--dim)' }}>
                  Quote Variants
                </span>
              </label>
              <label style={{ display:'flex', alignItems:'center', gap:'7px',
                               cursor:'pointer', userSelect:'none' }}>
                <input
                  type="checkbox"
                  checked={addForm.allow_cross_charting}
                  onChange={e => setAddForm(f =>
                    ({ ...f, allow_cross_charting: e.target.checked }))}
                />
                <span style={{
                  fontSize:'11px', fontWeight:600,
                  letterSpacing:'.06em', textTransform:'uppercase',
                  color: addForm.allow_cross_charting
                    ? 'var(--failed-color)' : 'var(--dim)',
                }}>
                  Cross-Charting {addForm.allow_cross_charting ? '⚠' : ''}
                </span>
              </label>
            </div>

            {/* Buttons */}
            <div style={{ display:'flex', gap:'10px' }}>
              <button
                onClick={() => { setShowAdd(false); setAddError(null); }}
                style={{
                  flex:1, padding:'10px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  background:'var(--bg3)', fontSize:'13px', fontWeight:600,
                  cursor:'pointer', color:'var(--muted)',
                }}>
                Cancel
              </button>
              <button
                onClick={handleAddStrategy}
                disabled={addLoading || !addForm.name || !addForm.symbol || !addForm.account_id}
                style={{
                  flex:1, padding:'10px', border:'none', borderRadius:'8px',
                  background:'var(--blue)', fontSize:'13px', fontWeight:600,
                  cursor:'pointer', color:'#fff',
                  opacity: addLoading ? 0.7 : 1,
                }}>
                {addLoading ? 'Creating...' : 'Create Strategy'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Strategy Modal */}
      {editTarget && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,.5)',
          display:'flex', alignItems:'center', justifyContent:'center',
          zIndex:1000, overflowY:'auto', padding:'20px',
        }}>
          <div style={{
            background:'var(--bg2)', borderRadius:'var(--r)',
            padding:'28px', width:'480px', maxWidth:'95vw',
            boxShadow:'0 20px 60px rgba(0,0,0,.25)',
          }}>
            <h2 style={{ fontSize:'18px', fontWeight:700,
                         color:'var(--text)', marginBottom:'20px' }}>
              Edit Strategy
            </h2>

            {editError && (
              <p style={{ color:'var(--red)', fontSize:'13px',
                          marginBottom:'12px' }}>{editError}</p>
            )}

            {/* Name */}
            <FieldRow label="Name">
              <input value={editForm.name}
                onChange={e => setEditForm((f:any) => ({ ...f, name: e.target.value }))}
                style={inputStyle} />
            </FieldRow>

            {/* Symbol */}
            <FieldRow label="Symbol">
              <input value={editForm.symbol}
                onChange={e => setEditForm((f:any) => ({ ...f, symbol: e.target.value }))}
                style={{ ...inputStyle, fontFamily:'JetBrains Mono, monospace' }} />
            </FieldRow>

            {/* Account */}
            <FieldRow label="Account">
              <select value={editForm.account_id}
                onChange={e => setEditForm((f:any) => ({ ...f, account_id: e.target.value }))}
                style={inputStyle}>
                {accounts.map(a => (
                  <option key={a.id} value={a.id}>
                    {a.label} ({a.exchange} / {a.mode})
                  </option>
                ))}
              </select>
            </FieldRow>

            {/* Leverage + Margin Mode */}
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr',
                          gap:'10px', marginBottom:'14px' }}>
              <FieldRow label="Default Leverage">
                <input type="number" value={editForm.default_leverage}
                  onChange={e => setEditForm((f:any) =>
                    ({ ...f, default_leverage: e.target.value }))}
                  style={inputStyle} />
              </FieldRow>
              <FieldRow label="Max Leverage">
                <input type="number" value={editForm.max_leverage}
                  onChange={e => setEditForm((f:any) =>
                    ({ ...f, max_leverage: e.target.value }))}
                  style={inputStyle} />
              </FieldRow>
              <FieldRow label="Margin Mode">
                <select value={editForm.margin_mode}
                  onChange={e => setEditForm((f:any) =>
                    ({ ...f, margin_mode: e.target.value }))}
                  style={inputStyle}>
                  <option value="isolated">Isolated</option>
                  <option value="cross">Cross</option>
                </select>
              </FieldRow>
            </div>

            {/* Risk fields */}
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr',
                          gap:'10px', marginBottom:'14px' }}>
              {[
                { key:'max_position_size',          label:'Max Size' },
                { key:'max_daily_signals',          label:'Daily Signals' },
                { key:'max_daily_drawdown_percent', label:'Drawdown %' },
              ].map(field => (
                <div key={field.key}>
                  <label style={labelStyle}>{field.label}</label>
                  <input type="number"
                    value={editForm[field.key]}
                    onChange={e => setEditForm((f:any) =>
                      ({ ...f, [field.key]: e.target.value }))}
                    style={inputStyle} />
                </div>
              ))}
            </div>

            {/* Coupling toggles */}
            <div style={{
              display:'flex', gap:'20px', marginBottom:'20px',
              padding:'10px 14px', background:'var(--bg3)',
              borderRadius:'8px', border:'1px solid var(--border)',
            }}>
              <label style={{ display:'flex', alignItems:'center',
                               gap:'7px', cursor:'pointer' }}>
                <input type="checkbox"
                  checked={editForm.allow_quote_variants}
                  onChange={e => setEditForm((f:any) =>
                    ({ ...f, allow_quote_variants: e.target.checked }))} />
                <span style={{ fontSize:'11px', fontWeight:600,
                                textTransform:'uppercase', letterSpacing:'.06em',
                                color:'var(--dim)' }}>Quote Variants</span>
              </label>
              <label style={{ display:'flex', alignItems:'center',
                               gap:'7px', cursor:'pointer' }}>
                <input type="checkbox"
                  checked={editForm.allow_cross_charting}
                  onChange={e => setEditForm((f:any) =>
                    ({ ...f, allow_cross_charting: e.target.checked }))} />
                <span style={{
                  fontSize:'11px', fontWeight:600,
                  textTransform:'uppercase', letterSpacing:'.06em',
                  color: editForm.allow_cross_charting
                    ? 'var(--failed-color)' : 'var(--dim)',
                }}>
                  Cross-Charting {editForm.allow_cross_charting ? '⚠' : ''}
                </span>
              </label>
            </div>

            {/* Webhook info section */}
            {webhookInfo && (
              <div style={{
                background:'var(--bg3)', borderRadius:'8px',
                border:'1px solid var(--border)',
                padding:'14px', marginBottom:'20px',
              }}>
                <p style={{ fontSize:'10px', fontWeight:700,
                             textTransform:'uppercase', letterSpacing:'.1em',
                             color:'var(--dim)', marginBottom:'10px' }}>
                  TradingView Webhook Configuration
                </p>

                {/* URL */}
                <p style={{ fontSize:'10px', color:'var(--dim)',
                             marginBottom:'4px', fontWeight:600 }}>
                  Webhook URL
                </p>
                <div style={{ display:'flex', gap:'6px', marginBottom:'10px' }}>
                  <code style={{
                    flex:1, padding:'6px 10px', background:'var(--bg2)',
                    border:'1px solid var(--border)', borderRadius:'6px',
                    fontSize:'11px', fontFamily:'JetBrains Mono, monospace',
                    color:'var(--text)', wordBreak:'break-all',
                  }}>
                    {webhookInfo.webhook_url}
                  </code>
                  <button
                    onClick={() => navigator.clipboard.writeText(webhookInfo.webhook_url)}
                    style={{
                      padding:'6px 10px', border:'1px solid var(--border)',
                      borderRadius:'6px', background:'var(--bg2)',
                      fontSize:'11px', color:'var(--blue)',
                      fontWeight:600, cursor:'pointer', whiteSpace:'nowrap',
                    }}>
                    Copy
                  </button>
                </div>

                {/* Secret */}
                <p style={{ fontSize:'10px', color:'var(--dim)',
                             marginBottom:'4px', fontWeight:600 }}>
                  Token (webhook secret)
                </p>
                <div style={{ display:'flex', gap:'6px', marginBottom:'10px' }}>
                  <code style={{
                    flex:1, padding:'6px 10px', background:'var(--bg2)',
                    border:'1px solid var(--border)', borderRadius:'6px',
                    fontSize:'11px', fontFamily:'JetBrains Mono, monospace',
                    color:'var(--text)', wordBreak:'break-all',
                  }}>
                    {webhookInfo.webhook_secret}
                  </code>
                  <button
                    onClick={() => navigator.clipboard.writeText(webhookInfo.webhook_secret)}
                    style={{
                      padding:'6px 10px', border:'1px solid var(--border)',
                      borderRadius:'6px', background:'var(--bg2)',
                      fontSize:'11px', color:'var(--blue)',
                      fontWeight:600, cursor:'pointer', whiteSpace:'nowrap',
                    }}>
                    Copy
                  </button>
                </div>

                {/* TradingView JSON template */}
                <p style={{ fontSize:'10px', color:'var(--dim)',
                             marginBottom:'4px', fontWeight:600 }}>
                  Alert Message (paste into TradingView)
                </p>
                <div style={{ position:'relative' }}>
                  <pre style={{
                    padding:'10px 12px', background:'var(--bg2)',
                    border:'1px solid var(--border)', borderRadius:'6px',
                    fontSize:'10px', fontFamily:'JetBrains Mono, monospace',
                    color:'var(--text)', overflowX:'auto', margin:0,
                    whiteSpace:'pre',
                  }}>
{`{
  "base_asset":  "{{syminfo.basecurrency}}",
  "quote_asset": "{{syminfo.currency}}",
  "side":        "{{strategy.order.action}}",
  "signal":      "open_long",
  "order_type":  "market",
  "size":        "{{strategy.order.contracts}}",
  "timestamp":   "{{timenow}}",
  "token":       "${webhookInfo.webhook_secret}"
}`}
                  </pre>
                  <button
                    onClick={() => navigator.clipboard.writeText(
                      `{\n  "base_asset":  "{{syminfo.basecurrency}}",\n  "quote_asset": "{{syminfo.currency}}",\n  "side":        "{{strategy.order.action}}",\n  "signal":      "open_long",\n  "order_type":  "market",\n  "size":        "{{strategy.order.contracts}}",\n  "timestamp":   "{{timenow}}",\n  "token":       "${webhookInfo.webhook_secret}"\n}`
                    )}
                    style={{
                      position:'absolute', top:'8px', right:'8px',
                      padding:'4px 8px', border:'1px solid var(--border)',
                      borderRadius:'4px', background:'var(--bg3)',
                      fontSize:'10px', color:'var(--blue)',
                      fontWeight:600, cursor:'pointer',
                    }}>
                    Copy JSON
                  </button>
                </div>
              </div>
            )}

            {/* Buttons */}
            <div style={{ display:'flex', gap:'10px' }}>
              <button
                onClick={() => { setEditTarget(null); setWebhookInfo(null); }}
                style={{
                  flex:1, padding:'10px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  background:'var(--bg3)', fontSize:'13px', fontWeight:600,
                  cursor:'pointer', color:'var(--muted)',
                }}>
                Cancel
              </button>
              <button
                onClick={handleEditSubmit}
                disabled={editLoading}
                style={{
                  flex:1, padding:'10px', border:'none', borderRadius:'8px',
                  background:'var(--blue)', color:'#fff',
                  fontSize:'13px', fontWeight:700, cursor:'pointer',
                  opacity: editLoading ? 0.7 : 1,
                }}>
                {editLoading ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Stop Strategy Confirmation Modal */}
      {stopTarget && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,.5)',
          display:'flex', alignItems:'center', justifyContent:'center',
          zIndex:1000,
        }}>
          <div style={{
            background:'var(--bg2)', borderRadius:'var(--r)',
            padding:'28px', width:'400px', maxWidth:'95vw',
            boxShadow:'0 20px 60px rgba(0,0,0,.25)',
          }}>
            <h2 style={{ fontSize:'18px', fontWeight:700,
                         color:'var(--text)', marginBottom:'12px' }}>
              Stop Strategy
            </h2>

            {(stopTarget.open_positions_count ?? 0) > 0 ? (
              <>
                <div style={{
                  background:'var(--failed-color-a)',
                  border:'1px solid var(--failed-color-b)',
                  borderRadius:'8px', padding:'12px 14px', marginBottom:'16px',
                }}>
                  <p style={{ fontSize:'13px', color:'var(--failed-color)',
                               fontWeight:600, margin:0 }}>
                    ⚠ This strategy has {stopTarget.open_positions_count} open
                    position(s).
                  </p>
                </div>
                <p style={{ fontSize:'13px', color:'var(--dim)',
                            marginBottom:'20px' }}>
                  Do you want to close the open positions before stopping?
                </p>
                {stopError && (
                  <p style={{ color:'var(--red)', fontSize:'13px',
                              marginBottom:'12px' }}>{stopError}</p>
                )}
                <div style={{ display:'flex', flexDirection:'column', gap:'8px' }}>
                  <button
                    onClick={() => confirmStop(true)}
                    disabled={stopping}
                    style={{
                      padding:'10px', border:'none', borderRadius:'8px',
                      background:'var(--red)', color:'#fff',
                      fontSize:'13px', fontWeight:700, cursor:'pointer',
                      opacity: stopping ? 0.7 : 1,
                    }}>
                    {stopping ? 'Closing & Stopping...' : 'Close Positions & Stop'}
                  </button>
                  <button
                    onClick={() => confirmStop(false)}
                    disabled={stopping}
                    style={{
                      padding:'10px', border:'1px solid var(--border)',
                      borderRadius:'8px', background:'var(--bg3)',
                      color:'var(--muted)', fontSize:'13px', fontWeight:600,
                      cursor:'pointer',
                    }}>
                    Stop Without Closing
                  </button>
                  <button
                    onClick={() => setStopTarget(null)}
                    disabled={stopping}
                    style={{
                      padding:'10px', border:'1px solid var(--border)',
                      borderRadius:'8px', background:'var(--bg3)',
                      color:'var(--muted)', fontSize:'13px', fontWeight:600,
                      cursor:'pointer',
                    }}>
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <>
                <p style={{ fontSize:'13px', color:'var(--dim)',
                            marginBottom:'20px' }}>
                  Stop <strong style={{ color:'var(--text)' }}>
                    {stopTarget.name}
                  </strong>? No open positions will be affected.
                </p>
                {stopError && (
                  <p style={{ color:'var(--red)', fontSize:'13px',
                              marginBottom:'12px' }}>{stopError}</p>
                )}
                <div style={{ display:'flex', gap:'10px' }}>
                  <button
                    onClick={() => setStopTarget(null)}
                    style={{
                      flex:1, padding:'10px',
                      border:'1px solid var(--border)', borderRadius:'8px',
                      background:'var(--bg3)', fontSize:'13px', fontWeight:600,
                      cursor:'pointer', color:'var(--muted)',
                    }}>
                    Cancel
                  </button>
                  <button
                    onClick={() => confirmStop(false)}
                    disabled={stopping}
                    style={{
                      flex:1, padding:'10px', border:'none', borderRadius:'8px',
                      background:'var(--red)', color:'#fff',
                      fontSize:'13px', fontWeight:700, cursor:'pointer',
                      opacity: stopping ? 0.7 : 1,
                    }}>
                    {stopping ? 'Stopping...' : 'Stop Strategy'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Webhook Secret Display — shown once after creation */}
      {createdSecret && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,.55)',
          display:'flex', alignItems:'center', justifyContent:'center',
          zIndex:1001,
        }}>
          <div style={{
            background:'var(--bg2)', borderRadius:'var(--r)',
            padding:'28px', width:'460px', maxWidth:'95vw',
            boxShadow:'0 20px 60px rgba(0,0,0,.25)',
          }}>
            <h2 style={{ fontSize:'18px', fontWeight:700,
                         color:'var(--text)', marginBottom:'8px' }}>
              Strategy Created ✓
            </h2>
            <p style={{ fontSize:'13px', color:'var(--dim)',
                        marginBottom:'16px' }}>
              Save these credentials now. The webhook secret will not be
              shown again.
            </p>

            {/* Secret */}
            <div style={{ marginBottom:'14px' }}>
              <label style={{
                display:'block', fontSize:'10px', fontWeight:600,
                textTransform:'uppercase', letterSpacing:'.08em',
                color:'var(--dim)', marginBottom:'4px',
              }}>Webhook Secret (token field in TradingView)</label>
              <div style={{ display:'flex', gap:'8px' }}>
                <code style={{
                  flex:1, padding:'8px 12px',
                  background:'var(--bg3)', border:'1px solid var(--border)',
                  borderRadius:'8px', fontSize:'12px',
                  fontFamily:'JetBrains Mono, monospace',
                  color:'var(--text)', wordBreak:'break-all',
                }}>
                  {createdSecret.secret}
                </code>
                <button
                  onClick={() => navigator.clipboard.writeText(createdSecret.secret)}
                  style={{
                    padding:'8px 12px', background:'var(--bg2)',
                    border:'1px solid var(--border)', borderRadius:'8px',
                    fontSize:'12px', cursor:'pointer', color:'var(--blue)',
                  }}>
                  Copy
                </button>
              </div>
            </div>

            {/* Webhook URL */}
            <div style={{ marginBottom:'24px' }}>
              <label style={{
                display:'block', fontSize:'10px', fontWeight:600,
                textTransform:'uppercase', letterSpacing:'.08em',
                color:'var(--dim)', marginBottom:'4px',
              }}>Webhook URL</label>
              <div style={{ display:'flex', gap:'8px' }}>
                <code style={{
                  flex:1, padding:'8px 12px',
                  background:'var(--bg3)', border:'1px solid var(--border)',
                  borderRadius:'8px', fontSize:'11px',
                  fontFamily:'JetBrains Mono, monospace',
                  color:'var(--text)', wordBreak:'break-all',
                }}>
                  {createdSecret.url}
                </code>
                <button
                  onClick={() => navigator.clipboard.writeText(createdSecret.url)}
                  style={{
                    padding:'8px 12px', background:'var(--bg2)',
                    border:'1px solid var(--border)', borderRadius:'8px',
                    fontSize:'12px', cursor:'pointer', color:'var(--blue)',
                  }}>
                  Copy
                </button>
              </div>
            </div>

            <button
              onClick={() => setCreatedSecret(null)}
              style={{
                width:'100%', padding:'12px', border:'none',
                borderRadius:'8px', background:'var(--blue)',
                fontSize:'14px', fontWeight:700, cursor:'pointer',
                color:'#fff',
              }}>
              I've saved it
            </button>
          </div>
        </div>
      )}

    </div>
  );
}
