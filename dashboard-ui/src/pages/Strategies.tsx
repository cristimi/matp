import { useState, useEffect, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
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
  closed_positions_count?: number;
  realized_pnl?:          number;
  pnl_total:            string;
  open_positions?:      number;
  win_positions?:       number;
  loss_positions?:      number;
  win_rate?:            number;
  allocated?:           number;
  pnl_fees?:            number;
  total_return?:        number;
  capital_allocation?:  number;
  initial_allocation?:  number;
  allocation_peak?:     number;
  margin_per_trade?:    number;
  max_drawdown_pct?:    number;
  uptime_label?:        string;
  last_signal_at?:      string;
  stopped_at?:          string;
  // Computed position breakdown
  closed_long_count?:        number;
  closed_short_count?:       number;
  // AI strategy fields
  strategy_source?:          'tradingview' | 'ai_engine' | 'social' | 'internal' | 'manual';
  ai_dry_run?:               boolean;
  ai_llm_model?:             string;
  ai_llm_provider?:          string;
  ai_interval_no_position?:  string;
  ai_interval_position_open?: string;
  ai_interval_at_risk?:      string;
  ai_at_risk_threshold_pct?: number;
  ai_last_cycle_at?:         string;
}

function formatRelativeDate(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime());
  yesterday.setDate(yesterday.getDate() - 1);

  const hhmm = date.toTimeString().slice(0, 5);
  if (date >= today)     return `Today ${hhmm}`;
  if (date >= yesterday) return `Yesterday ${hhmm}`;
  return date.toLocaleDateString('en-GB', {
    day:'2-digit', month:'2-digit', year:'2-digit'
  });
}

type PillVariant = 'lev' | 'tech' | 'open' | 'closed' | 'neutral' | 'ai' | 'dryrun';
function Pill({ variant, children }: {
  variant: PillVariant; children: React.ReactNode
}) {
  const styles: Record<PillVariant, React.CSSProperties> = {
    lev:    { background:'var(--blue-a)',   color:'var(--blue)',  borderColor:'var(--blue-b)',  textTransform:'lowercase' },
    tech:   { background:'var(--blue-a)',   color:'var(--blue)',  borderColor:'var(--blue-b)' },
    open:   { background:'var(--green-a)',  color:'var(--green)', borderColor:'var(--green-b)' },
    closed: { background:'var(--gray-a)',   color:'var(--gray)',  borderColor:'var(--gray-b)' },
    neutral:{ background:'var(--bg2)',      color:'var(--muted)', borderColor:'var(--border)', textTransform:'none' as const },
    ai:     { background:'rgba(83,74,183,.10)', color:'#534AB7', borderColor:'rgba(83,74,183,.25)' },
    dryrun: { background:'var(--failed-color-a)', color:'var(--failed-color)', borderColor:'var(--failed-color-b)' },
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
  onStop,
  onStart,
  onEdit,
  onDelete,
}: {
  strategy: Strategy;
  onStop: (s: Strategy) => void;
  onStart: (id: string) => void;
  onEdit: (s: Strategy) => void;
  onDelete: (id: string) => void;
}) {
  const isActive = strategy.enabled;
  const isAI = strategy.strategy_source === 'ai_engine';
  const barColor = isActive ? 'var(--green)' : 'var(--gray)';

  const closedLong  = strategy.closed_long_count  ?? 0;
  const closedShort = strategy.closed_short_count ?? 0;
  const closedCount = strategy.closed_positions_count ?? 0;
  const winRate     = strategy.win_rate    ?? 0;
  const allocated   = Number(strategy.capital_allocation ?? 0);
  const committed   = Number(strategy.initial_allocation ?? strategy.capital_allocation ?? 0);
  const realizedPnl = Number(strategy.realized_pnl ?? 0);
  const pnlFees     = strategy.pnl_fees    ?? 0;
  const totalReturn = strategy.total_return ?? 0;

  const pnlColor    = realizedPnl >= 0 ? 'var(--green)' : 'var(--red)';
  const returnColor = totalReturn >= 0 ? 'var(--green)' : 'var(--red)';
  const pnlSign     = realizedPnl >= 0 ? '+' : '';
  const returnSign  = totalReturn >= 0 ? '+' : '';

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
      background:    'var(--bg3)',
      borderRadius:  'var(--r)',
      border:        `1px solid ${isActive ? 'var(--border)' : 'var(--border-hi)'}`,
      marginBottom:  '10px',
      position:      'relative',
      display:       'flex',
      flexDirection: 'column',
      overflow:      'hidden',
    }}>
      {/* Left bar */}
      <div style={{
        position:'absolute', left:0, top:0, bottom:0,
        width:'4px', background:barColor, zIndex:1,
      }} />

      {/* Row 1: symbol + pills */}
      <div style={{
        display:'flex', alignItems:'center', gap:'6px',
        padding:'12px 12px 0 18px', lineHeight:1,
      }}>
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
        <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:'4px' }}>
          {isAI && (
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
              fontWeight:600,
              borderRadius:'var(--pill-r)', padding:'2px 6px',
              border:'1px solid var(--blue-b)',
              background:'var(--blue-a)', color:'var(--blue)',
              display:'inline-block', lineHeight:1, flexShrink:0, letterSpacing:'.04em',
            }}>
              {(strategy.open_positions_count ?? 0) > 0
                ? strategy.ai_interval_position_open ?? '15m'
                : strategy.ai_interval_no_position ?? '4h'}
            </span>
          )}
          {isAI ? (
            <Pill variant={strategy.ai_dry_run ? 'dryrun' : 'open'}>
              {strategy.ai_dry_run ? 'dry run' : 'live'}
            </Pill>
          ) : (
            <Pill variant={isActive ? 'open' : 'closed'}>
              {isActive ? 'active' : 'inactive'}
            </Pill>
          )}
        </div>
      </div>

      {/* Strat row: name + ID */}
      <div style={{
        padding:'5px 12px 0 18px',
        display:'flex', gap:'6px', alignItems:'center',
      }}>
        {isAI && <Pill variant="ai">AI</Pill>}
        <span style={{
          fontFamily:   'JetBrains Mono, monospace',
          fontSize:     '12px', fontWeight:700, letterSpacing:'.04em',
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
      </div>

      {/* Row 1b: route + time */}
      <div style={{
        display:'flex', justifyContent:'space-between', alignItems:'center',
        padding:'5px 12px 4px 18px',
      }}>
        <div style={{ display:'flex', alignItems:'center', gap:'4px' }}>
          {isAI ? (
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
              fontWeight:600, textTransform:'none' as const,
              borderRadius:'var(--pill-r)', padding:'2px 6px',
              border:'1px solid var(--blue-b)', display:'inline-block',
              lineHeight:1, flexShrink:0, letterSpacing:'.04em',
              background:'var(--blue-a)', color:'var(--blue)',
            }}>
              {strategy.ai_llm_model ?? 'ai'}
            </span>
          ) : (
            <Pill variant="neutral">TradingView</Pill>
          )}
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
          {isAI ? (
            <>
              <span style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
                fontWeight:500, color:'var(--muted)', lineHeight:1.1,
              }}>
                Last cycle:{' '}
                {strategy.ai_last_cycle_at
                  ? formatRelativeDate(strategy.ai_last_cycle_at)
                  : '—'}
              </span>
            </>
          ) : (
            <>
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
            </>
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
              {closedCount}
              {(closedLong > 0 || closedShort > 0) && (
                <span style={{ fontSize:'11px', fontWeight:500, color:'var(--dim)' }}>
                  {' '}(<span style={{ color:'var(--green)' }}>{closedLong}</span>/<span style={{ color:'var(--red)' }}>{closedShort}</span>)
                </span>
              )}
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
          <GridCell label="Allocation" last>
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
          <GridCell label="Committed">
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
              fontWeight:600, color:'var(--text)',
            }}>
              {committed.toFixed(1)}
            </span>
          </GridCell>
          <GridCell label="P&L (Realized)">
            <div style={{ display:'flex', alignItems:'baseline', gap:'4px' }}>
              <span style={{
                fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
                fontWeight:700, color:pnlColor,
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
              fontWeight:700, color:returnColor,
            }}>
              {returnSign}{totalReturn.toFixed(2)}%
            </span>
          </GridCell>
        </div>
      </div>

      {/* Action band */}
      <div style={{
        borderTop:'1px solid var(--border)', background:'var(--bg2)',
        display:'flex',
      }}>
        {isActive ? (
          <>
            <button
              onClick={() => onStop(strategy)}
              style={{
                flex:1, background:'transparent', border:'none',
                color:'var(--red)', fontSize:'11px', fontWeight:700,
                letterSpacing:'.06em', textTransform:'uppercase',
                padding:'10px', cursor:'pointer', textAlign:'center',
                borderRight:'1px solid var(--border)',
              }}>
              ⏹ Stop
            </button>
            <button
              onClick={() => onEdit(strategy)}
              style={{
                flex:1, background:'transparent', border:'none',
                color:'var(--blue)', fontSize:'11px', fontWeight:700,
                letterSpacing:'.06em', textTransform:'uppercase',
                padding:'10px', cursor:'pointer', textAlign:'center',
              }}>
              ✎ Edit
            </button>
          </>
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

function SectionDivider({ label }: { label: string }) {
  return (
    <p style={{
      fontSize:'10px', fontWeight:700, letterSpacing:'.1em',
      textTransform:'uppercase', color:'var(--dim)',
      marginBottom:'12px', marginTop:'4px',
      borderBottom:'1px solid var(--border)', paddingBottom:'6px',
    }}>
      {label}
    </p>
  );
}

function StrategyCommonFields({
  form, setForm, accounts, lockSymbolAccount = false, originalCapitalAllocation,
}: {
  form: any;
  setForm: (updater: (f: any) => any) => void;
  accounts: { id: string; label: string; exchange: string; mode: string }[];
  lockSymbolAccount?: boolean;
  originalCapitalAllocation?: number;
}) {
  const lockStyle: React.CSSProperties = lockSymbolAccount
    ? { ...inputStyle, opacity: 0.5 }
    : inputStyle;
  return (
    <>
      <SectionDivider label="Identity" />
      <FieldRow label="Name">
        <input value={form.name}
          onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
          placeholder="e.g. BTC Trend"
          style={inputStyle} />
      </FieldRow>
      <FieldRow label="Symbol">
        <input value={form.symbol}
          onChange={e => setForm(f => ({ ...f, symbol: e.target.value }))}
          placeholder="e.g. BTC-USDT"
          disabled={lockSymbolAccount}
          style={{ ...lockStyle, fontFamily:'JetBrains Mono, monospace' }} />
        <p style={{ fontSize:'11px', color:'var(--dim)', marginTop:'4px' }}>
          Use dash format: BTC-USDT
        </p>
      </FieldRow>
      <FieldRow label="Account">
        <select value={form.account_id}
          onChange={e => setForm(f => ({ ...f, account_id: e.target.value }))}
          disabled={lockSymbolAccount}
          style={lockStyle}>
          <option value="">— Select account —</option>
          {accounts.map(a => (
            <option key={a.id} value={a.id}>{a.label} ({a.exchange} / {a.mode})</option>
          ))}
        </select>
      </FieldRow>

      <SectionDivider label="Capital & Risk" />
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px', marginBottom:'14px' }}>
        <FieldRow label="Default Leverage">
          <input type="number" value={form.default_leverage}
            onChange={e => setForm(f => ({ ...f, default_leverage: e.target.value }))}
            style={inputStyle} />
        </FieldRow>
        <FieldRow label="Max Leverage">
          <input type="number" value={form.max_leverage}
            onChange={e => setForm(f => ({ ...f, max_leverage: e.target.value }))}
            style={inputStyle} />
        </FieldRow>
      </div>
      {'margin_mode' in form && (
        <FieldRow label="Margin Mode">
          <select value={form.margin_mode}
            onChange={e => setForm(f => ({ ...f, margin_mode: e.target.value }))}
            style={inputStyle}>
            <option value="isolated">Isolated</option>
            <option value="cross">Cross</option>
          </select>
        </FieldRow>
      )}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:'10px', marginBottom:'14px' }}>
        <div>
          {originalCapitalAllocation !== undefined ? (
            <>
              <label style={labelStyle}>Deposit / Withdraw ($)</label>
              <input type="number" step="0.01"
                value={form.allocation_delta ?? '0'}
                onChange={e => setForm(f => ({ ...f, allocation_delta: e.target.value }))}
                placeholder="0"
                style={inputStyle} />
              {parseFloat(form.allocation_delta ?? '0') !== 0 && (
                <p style={{ fontSize:'10px', color:'var(--blue)', marginTop:'4px' }}>
                  New allocation: ${(originalCapitalAllocation + parseFloat(form.allocation_delta ?? '0')).toFixed(2)}
                </p>
              )}
              <p style={{ fontSize:'10px', color:'var(--dim)', marginTop:'4px' }}>
                Deposits/withdrawals shift the high-water mark by the same amount; they do not reset the drawdown.
              </p>
            </>
          ) : (
            <>
              <label style={labelStyle}>Capital ($)</label>
              <input type="number" step="0.01" min="0"
                value={form.capital_allocation}
                onChange={e => setForm(f => ({ ...f, capital_allocation: e.target.value }))}
                style={inputStyle} />
            </>
          )}
        </div>
        <div>
          <label style={labelStyle}>Margin / Trade ($)</label>
          <input type="number" step="0.01" min="0"
            value={form.margin_per_trade}
            onChange={e => setForm(f => ({ ...f, margin_per_trade: e.target.value }))}
            style={inputStyle} />
        </div>
        <div>
          <label style={labelStyle}>Max Drawdown %</label>
          <input type="number" step="0.1" min="0"
            value={form.max_drawdown_pct}
            onChange={e => setForm(f => ({ ...f, max_drawdown_pct: e.target.value }))}
            style={inputStyle} />
        </div>
      </div>
      {parseFloat(form.margin_per_trade) > 0 && parseFloat(form.default_leverage) > 0 && (
        <p style={{ fontSize:'11px', color:'var(--dim)', marginBottom:'14px' }}>
          Max order size: <strong style={{ color:'var(--text)', fontFamily:'JetBrains Mono, monospace' }}>
            ${(parseFloat(form.margin_per_trade) * parseFloat(form.default_leverage)).toFixed(2)}
          </strong> (margin × leverage)
        </p>
      )}
      <div style={{
        display:'flex', gap:'20px', marginBottom:'20px',
        padding:'12px 14px',
        background:'var(--bg3)', borderRadius:'8px',
        border:'1px solid var(--border)',
      }}>
        <label style={{ display:'flex', alignItems:'center', gap:'7px', cursor:'pointer', userSelect:'none' }}>
          <input type="checkbox" checked={form.allow_quote_variants}
            onChange={e => setForm(f => ({ ...f, allow_quote_variants: e.target.checked }))} />
          <span style={{ fontSize:'11px', fontWeight:600, letterSpacing:'.06em',
                          textTransform:'uppercase', color:'var(--dim)' }}>
            Quote Variants
          </span>
        </label>
        <label style={{ display:'flex', alignItems:'center', gap:'7px', cursor:'pointer', userSelect:'none' }}>
          <input type="checkbox" checked={form.allow_cross_charting}
            onChange={e => setForm(f => ({ ...f, allow_cross_charting: e.target.checked }))} />
          <span style={{
            fontSize:'11px', fontWeight:600, letterSpacing:'.06em', textTransform:'uppercase',
            color: form.allow_cross_charting ? 'var(--failed-color)' : 'var(--dim)',
          }}>
            Cross-Charting {form.allow_cross_charting ? '⚠' : ''}
          </span>
        </label>
      </div>
    </>
  );
}

interface AiModel { id: string; display_name: string; verified?: boolean; }

const PROVIDERS = [
  { value: 'google',     label: 'Google Gemini' },
  { value: 'openai',    label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'groq',      label: 'Groq' },
];

const DATA_SOURCES: { key: keyof AiFormState; label: string }[] = [
  { key:'use_technical',           label:'Technical Indicators' },
  { key:'use_fear_greed',          label:'Fear & Greed Index' },
  { key:'use_funding_rate',        label:'Funding Rate & OI' },
  { key:'use_news',                label:'Crypto News' },
  { key:'use_btc_dominance',       label:'BTC Dominance' },
  { key:'use_macro',               label:'Macro (DXY, US10Y)' },
  { key:'use_geometry',            label:'Geometric Pattern Detection' },
  { key:'use_mtf_structure',       label:'Multi-Timeframe Structure' },
  { key:'use_volatility_regime',   label:'Volatility Regime' },
  { key:'use_momentum_divergence', label:'Momentum Divergence' },
  { key:'use_volume_profile',      label:'Volume Profile (HVN/LVN)' },
  { key:'use_orderbook',           label:'Order Book Depth' },
  { key:'use_cvd',                 label:'Order Flow (CVD)' },
  { key:'use_funding_history',     label:'Funding History' },
  { key:'use_economic_calendar',   label:'Economic Calendar (provider paid-tier — dormant)' },
  { key:'use_liquidations',        label:'Liquidations (stream aggregate)' },
  { key:'use_limit_orders',        label:'Resting Limit Orders (place/amend/cancel)' },
];

// Per-template data-source consumption (docs/design/ai_prompts/1*.md headers).
// Selecting a template presets these so strategies don't drift from what their
// prompt's rules actually read — the toggles stay editable afterwards.
const TEMPLATE_DATA_SOURCES: Record<string, Array<keyof AiFormState>> = {
  trend_following: ['use_mtf_structure', 'use_momentum_divergence', 'use_cvd', 'use_volatility_regime'],
  mean_reversion:  ['use_momentum_divergence', 'use_funding_history', 'use_volume_profile', 'use_volatility_regime', 'use_limit_orders'],
  breakout:        ['use_volatility_regime', 'use_volume_profile', 'use_orderbook', 'use_cvd', 'use_mtf_structure'],
  scalper:         ['use_orderbook', 'use_cvd', 'use_economic_calendar', 'use_liquidations', 'use_funding_history'],
  conservative:    ['use_mtf_structure', 'use_economic_calendar', 'use_funding_history', 'use_momentum_divergence'],
  range_rotation:  ['use_volume_profile', 'use_orderbook', 'use_economic_calendar', 'use_funding_history', 'use_limit_orders'],
  geometric_range: ['use_volume_profile', 'use_orderbook', 'use_economic_calendar', 'use_cvd', 'use_mtf_structure', 'use_geometry', 'use_limit_orders'],
  regime_router:   ['use_mtf_structure', 'use_volatility_regime', 'use_momentum_divergence', 'use_volume_profile',
                    'use_orderbook', 'use_cvd', 'use_funding_history', 'use_economic_calendar', 'use_geometry', 'use_limit_orders'],
};

const TEMPLATE_PRESET_KEYS: Array<keyof AiFormState> = [
  'use_geometry', 'use_mtf_structure', 'use_volatility_regime', 'use_momentum_divergence',
  'use_volume_profile', 'use_orderbook', 'use_cvd', 'use_funding_history',
  'use_economic_calendar', 'use_liquidations', 'use_limit_orders',
];

function templateDataSourcePresets(templateId: string): Partial<AiFormState> {
  const consumed = TEMPLATE_DATA_SOURCES[templateId];
  if (!consumed) return {};
  const presets: any = {};
  for (const key of TEMPLATE_PRESET_KEYS) presets[key] = consumed.includes(key);
  return presets;
}

function TemplatePreview({
  tmpl,
  form,
}: {
  tmpl: { description: string; system_prompt: string } | undefined;
  form: AiFormState;
}) {
  if (!tmpl) return null;

  const activeSources = DATA_SOURCES.filter(s => form[s.key]).map(s => s.label);

  return (
    <div style={{ marginTop: '8px' }}>
      <p style={{ fontSize: '11px', color: 'var(--dim)', marginBottom: '8px' }}>
        {tmpl.description}
      </p>

      <p style={{
        fontSize: '10px', fontWeight: 700, textTransform: 'uppercase',
        letterSpacing: '.1em', color: 'var(--dim)', marginBottom: '4px',
      }}>
        Strategy Instructions
      </p>
      <pre style={{
        padding: '10px 12px', background: 'var(--bg2)',
        border: '1px solid var(--border)', borderRadius: '6px',
        fontSize: '10px', fontFamily: 'JetBrains Mono, monospace',
        color: 'var(--text)', overflowX: 'auto', margin: 0,
        whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '220px',
        overflowY: 'auto',
      }}>
        {tmpl.system_prompt}
      </pre>

      <p style={{
        fontSize: '10px', fontWeight: 700, textTransform: 'uppercase',
        letterSpacing: '.1em', color: 'var(--dim)', margin: '10px 0 4px',
      }}>
        Active Data Sources
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
        {activeSources.length === 0 ? (
          <span style={{ fontSize: '11px', color: 'var(--dim)' }}>None</span>
        ) : (
          activeSources.map(label => (
            <span key={label} style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: '10px',
              fontWeight: 600, color: 'var(--blue)',
              background: 'var(--blue-a)', border: '1px solid var(--blue-b)',
              borderRadius: '6px', padding: '2px 6px',
            }}>
              {label}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

interface AiFormState {
  interval_no_position:   string;
  interval_position_open: string;
  interval_at_risk:       string;
  use_technical:          boolean;
  use_fear_greed:         boolean;
  use_funding_rate:       boolean;
  use_open_interest:      boolean;
  use_news:               boolean;
  use_btc_dominance:      boolean;
  use_macro:              boolean;
  use_geometry:           boolean;
  use_mtf_structure:       boolean;
  use_volatility_regime:   boolean;
  use_momentum_divergence: boolean;
  use_volume_profile:      boolean;
  use_orderbook:           boolean;
  use_cvd:                 boolean;
  use_funding_history:     boolean;
  use_economic_calendar:   boolean;
  use_liquidations:        boolean;
  use_limit_orders:        boolean;
  confidence_threshold:   string;
  cooldown_entry_minutes: string;
  llm_provider:           string;
  llm_model:              string;
  template_id:            string;
  custom_instructions:    string;
  dry_run:                boolean;
}

const AI_FORM_DEFAULTS: AiFormState = {
  interval_no_position:   '4h',
  interval_position_open: '15m',
  interval_at_risk:       '5m',
  use_technical:          true,
  use_fear_greed:         true,
  use_funding_rate:       true,
  use_open_interest:      true,
  use_news:               true,
  use_btc_dominance:      false,
  use_macro:              false,
  use_geometry:           false,
  use_mtf_structure:       false,
  use_volatility_regime:   false,
  use_momentum_divergence: false,
  use_volume_profile:      false,
  use_orderbook:           false,
  use_cvd:                 false,
  use_funding_history:     false,
  use_economic_calendar:   false,
  use_liquidations:        false,
  use_limit_orders:        false,
  confidence_threshold:   '0.72',
  cooldown_entry_minutes: '240',
  llm_provider:           'google',
  llm_model:              '',
  template_id:            '',
  custom_instructions:    '',
  dry_run:                true,
};

const TV_FORM_DEFAULTS = {
  name: '', symbol: '', account_id: '', interval: '1h',
  default_leverage: '1',
  max_leverage: '10',
  capital_allocation: '100', margin_per_trade: '5', max_drawdown_pct: '50',
  allow_quote_variants: false, allow_cross_charting: false,
};

export default function Strategies() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading]       = useState(true);
  const location = useLocation();
  const navigate = useNavigate();
  const [autoEditId] = useState<string | null>(() => (location.state as any)?.editId ?? null);
  const [autoAdd]    = useState<boolean>(() => (location.state as any)?.openAdd ?? false);

  const [filterPair,   setFilterPairRaw]   = useState<string>(() => sessionStorage.getItem('matp_strat_pair')   ?? 'all');
  const [filterStatus, setFilterStatusRaw] = useState<string>(() => sessionStorage.getItem('matp_strat_status') ?? 'all');
  const [filterSource, setFilterSourceRaw] = useState<string>(() => sessionStorage.getItem('matp_strat_source') ?? 'all');
  const setFilterPair   = (v: string) => { sessionStorage.setItem('matp_strat_pair',   v); setFilterPairRaw(v);   };
  const setFilterStatus = (v: string) => { sessionStorage.setItem('matp_strat_status', v); setFilterStatusRaw(v); };
  const setFilterSource = (v: string) => { sessionStorage.setItem('matp_strat_source', v); setFilterSourceRaw(v); };

  const [showAdd, setShowAdd] = useState(false);
  const [addType, setAddType] = useState<'tradingview' | 'ai'>('tradingview');
  const [addForm, setAddForm] = useState({ ...TV_FORM_DEFAULTS });
  const [aiForm,  setAiForm]  = useState<AiFormState>({ ...AI_FORM_DEFAULTS });
  const [aiTemplates, setAiTemplates] = useState<{id:string; name:string; description:string; system_prompt:string}[]>([]);
  const [aiModels,    setAiModels]    = useState<AiModel[]>([]);

  const [accounts,  setAccounts]  = useState<{id:string; label:string; exchange:string; mode:string}[]>([]);
  const [addError,  setAddError]  = useState<string | null>(null);
  const [addLoading,setAddLoading]= useState(false);
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

  const fetchAITemplates = useCallback(async () => {
    try {
      const res  = await fetch('/api/ai/templates');
      const data = await res.json();
      if (Array.isArray(data)) {
        setAiTemplates(data);
        if (data.length > 0) {
          setAiForm(f => ({ ...f, template_id: data[0].id }));
        }
      }
    } catch {}
  }, []);

  const fetchAIModels = useCallback(async (provider: string) => {
    try {
      const res  = await fetch(`/api/ai/models?provider=${provider}`);
      const data = await res.json();
      const models: AiModel[] = Array.isArray(data.models)
        ? data.models.map((m: any) => typeof m === 'string' ? { id: m, display_name: m } : m)
        : [];
      setAiModels(models);
      const firstVerified = models.find(m => m.verified !== false)?.id ?? models[0]?.id ?? '';
      setAiForm(f => ({ ...f, llm_model: firstVerified }));
    } catch {
      setAiModels([]);
    }
  }, []);

  const fetchEditAiModels = useCallback(async (provider: string) => {
    try {
      const res    = await fetch(`/api/ai/models?provider=${provider}`);
      const data   = await res.json();
      const models: AiModel[] = Array.isArray(data.models)
        ? data.models.map((m: any) => typeof m === 'string' ? { id: m, display_name: m } : m)
        : [];
      setEditAiModels(models);
      const firstVerified = models.find(m => m.verified !== false)?.id ?? models[0]?.id ?? '';
      setAiEditForm(f => ({ ...f, llm_model: f.llm_model || firstVerified }));
    } catch {
      setEditAiModels([]);
    }
  }, []);

  useEffect(() => {
    fetchStrategies();
    fetchAccounts();
    const iv = setInterval(fetchStrategies, 30000);
    return () => clearInterval(iv);
  }, [fetchStrategies, fetchAccounts]);


  const handleOpenAdd = () => {
    setShowAdd(true);
    setAddType('tradingview');
    setAddError(null);
    fetchAITemplates();
    fetchAIModels('google');
  };

  const resetAddModal = () => {
    setShowAdd(false);
    setAddType('tradingview');
    setAddError(null);
    setAddForm({ ...TV_FORM_DEFAULTS });
    setAiForm({ ...AI_FORM_DEFAULTS });
  };

  const uniquePairs = Array.from(new Set(strategies.map(s => s.symbol))).sort();

  const [stopTarget, setStopTarget] = useState<Strategy | null>(null);
  const [stopping,   setStopping]   = useState(false);
  const [stopError,  setStopError]  = useState<string | null>(null);

  const handleStop = (strategy: Strategy) => {
    setStopTarget(strategy);
    setStopError(null);
  };

  const confirmStop = async () => {
    if (!stopTarget) return;
    setStopping(true);
    setStopError(null);
    try {
      const res = await fetch(`/api/dashboard/strategies/${stopTarget.id}/stop`, { method: 'POST' });
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

  const [toast, setToast] = useState<string | null>(null);

  const [editTarget,  setEditTarget]  = useState<Strategy | null>(null);
  const [editForm,    setEditForm]    = useState<any>({});
  const [editLoading, setEditLoading] = useState(false);
  const [editError,   setEditError]   = useState<string | null>(null);
  const [webhookInfo, setWebhookInfo] = useState<any>(null);
  const [aiEditForm,   setAiEditForm]   = useState<AiFormState>({ ...AI_FORM_DEFAULTS });
  const [editAiModels, setEditAiModels] = useState<AiModel[]>([]);

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
      interval:                   String(strategy.interval ?? '1h'),
      capital_allocation:         String(strategy.capital_allocation ?? 100),
      allocation_delta:           '0',
      margin_per_trade:           String(strategy.margin_per_trade ?? 5),
      max_drawdown_pct:           String(strategy.max_drawdown_pct ?? 50),
      allow_quote_variants:       strategy.allow_quote_variants ?? false,
      allow_cross_charting:       strategy.allow_cross_charting ?? false,
    });

    if (strategy.strategy_source === 'ai_engine') {
      try {
        const configRes = await fetch(`/api/ai/strategies/${strategy.id}/config`);
        const config = await configRes.json();
        setAiEditForm({
          interval_no_position:   config.interval_no_position   ?? '4h',
          interval_position_open: config.interval_position_open ?? '15m',
          interval_at_risk:       config.interval_at_risk       ?? '5m',
          use_technical:          config.use_technical          ?? true,
          use_fear_greed:         config.use_fear_greed         ?? true,
          use_funding_rate:       config.use_funding_rate       ?? true,
          use_open_interest:      config.use_open_interest      ?? true,
          use_news:               config.use_news               ?? true,
          use_btc_dominance:      config.use_btc_dominance      ?? false,
          use_macro:              config.use_macro              ?? false,
          use_geometry:           config.use_geometry           ?? false,
          use_mtf_structure:       config.use_mtf_structure       ?? false,
          use_volatility_regime:   config.use_volatility_regime   ?? false,
          use_momentum_divergence: config.use_momentum_divergence ?? false,
          use_volume_profile:      config.use_volume_profile      ?? false,
          use_orderbook:           config.use_orderbook           ?? false,
          use_cvd:                 config.use_cvd                 ?? false,
          use_funding_history:     config.use_funding_history     ?? false,
          use_economic_calendar:   config.use_economic_calendar   ?? false,
          use_liquidations:        config.use_liquidations        ?? false,
          use_limit_orders:        config.use_limit_orders        ?? false,
          confidence_threshold:   String(config.confidence_threshold   ?? '0.72'),
          cooldown_entry_minutes: String(config.cooldown_entry_minutes ?? '240'),
          llm_provider:           config.llm_provider           ?? 'google',
          llm_model:              config.llm_model              ?? '',
          template_id:            String(config.template_id     ?? ''),
          custom_instructions:    config.custom_instructions    ?? '',
          dry_run:                config.dry_run                ?? true,
        });
        fetchEditAiModels(config.llm_provider ?? 'google');
        if (aiTemplates.length === 0) fetchAITemplates();
      } catch {
        setAiEditForm({ ...AI_FORM_DEFAULTS });
      }
    } else {
      try {
        const res  = await fetch(`/api/dashboard/strategies/${strategy.id}/webhook-info`);
        const data = await res.json();
        setWebhookInfo(data);
      } catch {
        setWebhookInfo(null);
      }
    }
  };

  // Auto-open edit modal when navigated from tree page with state.editId
  useEffect(() => {
    if (!autoEditId || strategies.length === 0 || editTarget) return;
    const target = strategies.find(s => s.id === autoEditId);
    if (target) {
      window.history.replaceState({}, '', window.location.pathname);
      handleEdit(target);
    }
  }, [strategies, autoEditId]);

  // Auto-open add modal when navigated from tree page with state.openAdd
  useEffect(() => {
    if (!autoAdd) return;
    window.history.replaceState({}, '', window.location.pathname);
    handleOpenAdd();
  }, [autoAdd]);

  const handleEditSubmit = async () => {
    if (!editTarget) return;
    if (parseFloat(editForm.margin_per_trade ?? '0') <= 0) {
      setEditError('Margin per trade must be greater than 0');
      return;
    }
    setEditLoading(true);
    setEditError(null);
    try {
      if (editTarget.strategy_source === 'ai_engine') {
        const s1 = await fetch(`/api/dashboard/strategies/${editTarget.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name:                 editForm.name,
            symbol:               editForm.symbol,
            account_id:           editForm.account_id,
            margin_mode:          editForm.margin_mode,
            default_leverage:     parseInt(editForm.default_leverage),
            max_leverage:         parseInt(editForm.max_leverage),
            allocation_delta:     parseFloat(editForm.allocation_delta ?? '0'),
            margin_per_trade:     parseFloat(editForm.margin_per_trade),
            max_drawdown_pct:     parseFloat(editForm.max_drawdown_pct),
            allow_quote_variants: editForm.allow_quote_variants,
            allow_cross_charting: editForm.allow_cross_charting,
          }),
        });
        if (!s1.ok) { setEditError((await s1.json()).error || 'Failed to update strategy'); return; }

        const s2 = await fetch(`/api/ai/strategies/${editTarget.id}/config`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            interval_no_position:   aiEditForm.interval_no_position,
            interval_position_open: aiEditForm.interval_position_open,
            interval_at_risk:       aiEditForm.interval_at_risk,
            use_technical:          aiEditForm.use_technical,
            use_fear_greed:         aiEditForm.use_fear_greed,
            use_funding_rate:       aiEditForm.use_funding_rate,
            use_open_interest:      aiEditForm.use_open_interest,
            use_news:               aiEditForm.use_news,
            use_btc_dominance:      aiEditForm.use_btc_dominance,
            use_macro:              aiEditForm.use_macro,
            use_geometry:           aiEditForm.use_geometry,
            use_mtf_structure:       aiEditForm.use_mtf_structure,
            use_volatility_regime:   aiEditForm.use_volatility_regime,
            use_momentum_divergence: aiEditForm.use_momentum_divergence,
            use_volume_profile:      aiEditForm.use_volume_profile,
            use_orderbook:           aiEditForm.use_orderbook,
            use_cvd:                 aiEditForm.use_cvd,
            use_funding_history:     aiEditForm.use_funding_history,
            use_economic_calendar:   aiEditForm.use_economic_calendar,
            use_liquidations:        aiEditForm.use_liquidations,
            use_limit_orders:        aiEditForm.use_limit_orders,
            confidence_threshold:   parseFloat(aiEditForm.confidence_threshold),
            cooldown_entry_minutes: parseInt(aiEditForm.cooldown_entry_minutes),
            llm_provider:           aiEditForm.llm_provider,
            llm_model:              aiEditForm.llm_model,
            template_id:            aiEditForm.template_id || null,
            custom_instructions:    aiEditForm.custom_instructions || null,
            dry_run:                aiEditForm.dry_run,
          }),
        });
        if (!s2.ok) { setEditError((await s2.json()).error || 'Failed to update AI config'); return; }

        setEditTarget(null);
        setWebhookInfo(null);
        navigate('/tree');
      } else {
        const res = await fetch(`/api/dashboard/strategies/${editTarget.id}`, {
          method:  'PUT',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            ...editForm,
            default_leverage:   parseInt(editForm.default_leverage),
            max_leverage:       parseInt(editForm.max_leverage),
            allocation_delta:   parseFloat(editForm.allocation_delta ?? '0'),
            margin_per_trade:   parseFloat(editForm.margin_per_trade),
            max_drawdown_pct:   parseFloat(editForm.max_drawdown_pct),
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          setEditError(data.error || 'Failed to update strategy');
          return;
        }
        setEditTarget(null);
        setWebhookInfo(null);
        navigate('/tree');
      }
    } catch (e: any) {
      setEditError(e.message);
    } finally {
      setEditLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Permanently delete this strategy? This cannot be undone.')) return;
    try {
      const res = await fetch(`/api/dashboard/strategies/${id}`, { method: 'DELETE' });
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

  const handleCouplingChange = async (id: string, field: string, value: boolean) => {
    await fetch(`/api/dashboard/strategies/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value }),
    });
    fetchStrategies();
  };

  const handleAddStrategy = async () => {
    setAddError(null);
    if (parseFloat(addForm.capital_allocation) <= 0 || parseFloat(addForm.margin_per_trade) <= 0) {
      setAddError('Capital allocation and margin per trade must be greater than 0');
      return;
    }
    setAddLoading(true);
    try {
      if (addType === 'tradingview') {
        const res = await fetch('/api/dashboard/strategies', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            ...addForm,
            strategy_source:    'tradingview',
            default_leverage:   parseInt(addForm.default_leverage),
            max_leverage:       parseInt(addForm.max_leverage),
            capital_allocation: parseFloat(addForm.capital_allocation),
            margin_per_trade:   parseFloat(addForm.margin_per_trade),
            max_drawdown_pct:   parseFloat(addForm.max_drawdown_pct),
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          setAddError(data.error || 'Failed to create strategy');
          return;
        }
        const host = window.location.host;
        setCreatedSecret({
          id:     data.id,
          secret: data.webhook_secret,
          url:    `http://${host}/api/listener/webhook/${data.id}`,
        });
        resetAddModal();
        fetchStrategies();
      } else {
        // AI strategy: three sequential calls
        const stratRes = await fetch('/api/dashboard/strategies', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            name:                 addForm.name,
            symbol:               addForm.symbol,
            account_id:           addForm.account_id,
            default_leverage:     parseInt(addForm.default_leverage),
            max_leverage:         parseInt(addForm.max_leverage),
            strategy_source:      'ai_engine',
            capital_allocation:   parseFloat(addForm.capital_allocation),
            margin_per_trade:     parseFloat(addForm.margin_per_trade),
            max_drawdown_pct:     parseFloat(addForm.max_drawdown_pct),
            allow_quote_variants: addForm.allow_quote_variants,
            allow_cross_charting: addForm.allow_cross_charting,
          }),
        });
        const stratData = await stratRes.json();
        if (!stratRes.ok) {
          setAddError(stratData.error || 'Failed to create strategy');
          return;
        }
        const stratId = stratData.id;

        const configRes = await fetch(`/api/ai/strategies/${stratId}/config`, {
          method:  'PUT',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            interval_no_position:   aiForm.interval_no_position,
            interval_position_open: aiForm.interval_position_open,
            interval_at_risk:       aiForm.interval_at_risk,
            use_technical:          aiForm.use_technical,
            use_fear_greed:         aiForm.use_fear_greed,
            use_funding_rate:       aiForm.use_funding_rate,
            use_open_interest:      aiForm.use_open_interest,
            use_news:               aiForm.use_news,
            use_btc_dominance:      aiForm.use_btc_dominance,
            use_macro:              aiForm.use_macro,
            use_geometry:           aiForm.use_geometry,
            use_mtf_structure:       aiForm.use_mtf_structure,
            use_volatility_regime:   aiForm.use_volatility_regime,
            use_momentum_divergence: aiForm.use_momentum_divergence,
            use_volume_profile:      aiForm.use_volume_profile,
            use_orderbook:           aiForm.use_orderbook,
            use_cvd:                 aiForm.use_cvd,
            use_funding_history:     aiForm.use_funding_history,
            use_economic_calendar:   aiForm.use_economic_calendar,
            use_liquidations:        aiForm.use_liquidations,
            use_limit_orders:        aiForm.use_limit_orders,
            confidence_threshold:   parseFloat(aiForm.confidence_threshold),
            cooldown_entry_minutes: parseInt(aiForm.cooldown_entry_minutes),
            llm_provider:           aiForm.llm_provider,
            llm_model:              aiForm.llm_model,
            template_id:            aiForm.template_id || null,
            custom_instructions:    aiForm.custom_instructions || null,
            dry_run:                aiForm.dry_run,
          }),
        });
        const configData = await configRes.json();
        if (!configRes.ok) {
          setAddError(configData.error || 'Failed to save AI config');
          return;
        }

        resetAddModal();
        navigate('/tree');
      }
    } catch (e: any) {
      setAddError(e.message);
    } finally {
      setAddLoading(false);
    }
  };

  const filtered = strategies.filter(s => {
    if (filterPair   !== 'all' && s.symbol !== filterPair)              return false;
    if (filterStatus !== 'all' && filterStatus === 'active'   && !s.enabled) return false;
    if (filterStatus !== 'all' && filterStatus === 'inactive' &&  s.enabled) return false;
    if (filterSource !== 'all') {
      const src = s.strategy_source ?? 'tradingview';
      if (filterSource === 'ai'          && src !== 'ai_engine')   return false;
      if (filterSource === 'tradingview' && src !== 'tradingview') return false;
      if (filterSource === 'manual'      && src !== 'manual')      return false;
      if (filterSource === 'social'      && src !== 'social')      return false;
      if (filterSource === 'internal'    && src !== 'internal')    return false;
    }
    return true;
  });
  const sortStrategies = (list: Strategy[]) =>
    [...list].sort((a, b) => {
      const posA = (a.open_positions_count ?? 0) > 0 ? 1 : 0;
      const posB = (b.open_positions_count ?? 0) > 0 ? 1 : 0;
      if (posA !== posB) return posB - posA;
      const tA = a.strategy_source === 'ai_engine' ? a.ai_last_cycle_at : a.last_signal_at;
      const tB = b.strategy_source === 'ai_engine' ? b.ai_last_cycle_at : b.last_signal_at;
      return (tB ? new Date(tB).getTime() : 0) - (tA ? new Date(tA).getTime() : 0);
    });

  const active   = sortStrategies(filtered.filter(s =>  s.enabled));
  const inactive = sortStrategies(filtered.filter(s => !s.enabled));

  if (loading) {
    return <div style={{ padding:'24px', color:'var(--dim)' }}>Loading strategies...</div>;
  }

  const isAIFilter = filterSource === 'ai';
  const anyFilterActive = filterPair !== 'all' || filterStatus !== 'all' || filterSource !== 'all';

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>

      {/* Top bar */}
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'space-between',
        padding:'18px 20px 12px', background:'var(--bg2)',
        borderBottom:'1px solid var(--border)', flexShrink:0,
      } as any}>
        <span style={{ fontSize:'23px', fontWeight:800, letterSpacing:'-.02em' }}>
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
            onClick={handleOpenAdd}
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
          {uniquePairs.map(p => <option key={p} value={p}>{p}</option>)}
        </select>

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

        <select
          value={filterSource}
          onChange={e => setFilterSource(e.target.value)}
          style={{
            background: isAIFilter ? 'rgba(83,74,183,.10)'
                        : filterSource !== 'all' ? 'var(--blue-a)' : 'var(--bg2)',
            border: `1px solid ${
              isAIFilter ? 'rgba(83,74,183,.25)'
              : filterSource !== 'all' ? 'var(--blue)' : 'var(--border)'}`,
            borderRadius:'20px', padding:'5px 12px',
            fontSize:'10px', fontWeight:500,
            color: isAIFilter ? '#534AB7'
                   : filterSource !== 'all' ? 'var(--blue)' : 'var(--muted)',
            cursor:'pointer', outline:'none',
          }}>
          <option value="all">All Sources</option>
          <option value="tradingview">TradingView</option>
          <option value="ai">AI</option>
          <option value="social">Social</option>
          <option value="internal">Internal</option>
          <option value="manual">Manual</option>
        </select>

        {anyFilterActive && (
          <span
            onClick={() => { setFilterPair('all'); setFilterStatus('all'); setFilterSource('all'); }}
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

      {/* Scroll area */}
      <div style={{
        flex:1, overflowY:'auto', padding:'14px 14px 80px',
        scrollbarWidth:'none',
      }}>
        {active.length > 0 && (
          <>
            <SectionHeader label="Active" count={active.length} variant="live" />
            {active.map(s => (
              <StrategyCard key={s.id} strategy={s}
                onStop={handleStop} onStart={handleStart}
                onEdit={handleEdit} onDelete={handleDelete} />
            ))}
          </>
        )}
        {inactive.length > 0 && (
          <>
            <SectionHeader label="Inactive" count={inactive.length} variant="closed" />
            {inactive.map(s => (
              <StrategyCard key={s.id} strategy={s}
                onStop={handleStop} onStart={handleStart}
                onEdit={handleEdit} onDelete={handleDelete} />
            ))}
          </>
        )}
        {strategies.length === 0 && (
          <p style={{ color:'var(--dim)', textAlign:'center', padding:'40px 0' }}>
            No strategies configured.
          </p>
        )}
      </div>

      {/* ── Add Strategy Modal ── */}
      {showAdd && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,.45)',
          display:'flex', alignItems:'center', justifyContent:'center',
          zIndex:1000, overflowY:'auto', padding:'20px',
        }}>
          <div style={{
            background:'var(--bg2)', borderRadius:'var(--r)',
            padding:'28px', width:'460px', maxWidth:'95vw',
            boxShadow:'0 20px 60px rgba(0,0,0,.2)',
            maxHeight:'90vh', overflowY:'auto',
          }}>
            <h2 style={{
              fontSize:'18px', fontWeight:700, color:'var(--text)',
              marginBottom:'20px',
            }}>
              Add Strategy
            </h2>

            {/* Source type toggle */}
            <div style={{
              display:'flex', gap:'4px', marginBottom:'20px',
              padding:'4px', background:'var(--bg3)',
              borderRadius:'10px', border:'1px solid var(--border)',
            }}>
              {(['tradingview', 'ai'] as const).map(type => (
                <button
                  key={type}
                  onClick={() => setAddType(type)}
                  style={{
                    flex:1, padding:'7px 10px',
                    border:'none', borderRadius:'7px',
                    fontSize:'11px', fontWeight:700,
                    letterSpacing:'.05em', textTransform:'uppercase',
                    cursor:'pointer',
                    background: addType === type
                      ? (type === 'ai' ? '#534AB7' : 'var(--blue)')
                      : 'transparent',
                    color: addType === type ? '#fff' : 'var(--muted)',
                    transition:'all .12s',
                  }}>
                  {type === 'tradingview' ? 'TradingView' : 'AI Autonomous'}
                </button>
              ))}
            </div>

            {addError && (
              <p style={{ color:'var(--red)', fontSize:'13px', marginBottom:'12px' }}>
                {addError}
              </p>
            )}

            {/* ── Common fields (Identity + Capital & Risk) ── */}
            <StrategyCommonFields form={addForm} setForm={setAddForm} accounts={accounts} />

            {/* ── TradingView tail ── */}
            {addType === 'tradingview' && (
              <>
                <SectionDivider label="Signal Source" />
                <FieldRow label="Interval">
                  <select value={addForm.interval}
                    onChange={e => setAddForm(f => ({ ...f, interval: e.target.value }))}
                    style={inputStyle}>
                    {['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d'].map(i => (
                      <option key={i} value={i}>{i}</option>
                    ))}
                  </select>
                </FieldRow>
              </>
            )}

            {/* ── AI Autonomous tail ── */}
            {addType === 'ai' && (
              <>
                {/* Section 2: Operational Parameters */}
                <SectionDivider label="Operational Parameters" />
                <p style={{ fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                             letterSpacing:'.08em', color:'var(--dim)', marginBottom:'8px' }}>
                  Analysis Intervals
                </p>
                <div style={{
                  display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:'8px',
                  marginBottom:'14px',
                }}>
                  {([
                    { key:'interval_no_position'   as const, label:'No Position', opts:['1h','2h','4h','8h','1d'] },
                    { key:'interval_position_open' as const, label:'Position Open', opts:['5m','10m','15m','30m'] },
                    { key:'interval_at_risk'       as const, label:'At Risk', opts:['1m','5m','10m'] },
                  ]).map(f => (
                    <div key={f.key}>
                      <label style={{ ...labelStyle, fontSize:'10px' }}>{f.label}</label>
                      <select value={aiForm[f.key] as string}
                        onChange={e => setAiForm(af => ({ ...af, [f.key]: e.target.value }))}
                        style={{ ...inputStyle, fontSize:'12px' }}>
                        {f.opts.map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </div>
                  ))}
                </div>

                <p style={{ fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                             letterSpacing:'.08em', color:'var(--dim)', marginBottom:'8px' }}>
                  Data Sources
                </p>
                <div style={{
                  display:'grid', gridTemplateColumns:'1fr 1fr', gap:'6px',
                  marginBottom:'14px', padding:'10px 12px',
                  background:'var(--bg3)', borderRadius:'8px',
                  border:'1px solid var(--border)',
                }}>
                  {DATA_SOURCES.map(f => (
                    <label key={f.key} style={{
                      display:'flex', alignItems:'center', gap:'6px',
                      cursor:'pointer', userSelect:'none',
                    }}>
                      <input type="checkbox"
                        checked={aiForm[f.key] as boolean}
                        onChange={e => setAiForm(af => ({ ...af, [f.key]: e.target.checked }))} />
                      <span style={{ fontSize:'11px', color:'var(--muted)' }}>{f.label}</span>
                    </label>
                  ))}
                </div>

                <div style={{
                  display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px',
                  marginBottom:'14px',
                }}>
                  <div>
                    <label style={labelStyle}>Confidence Threshold</label>
                    <input type="number" step="0.01" min="0.5" max="0.95"
                      value={aiForm.confidence_threshold}
                      onChange={e => setAiForm(f => ({ ...f, confidence_threshold: e.target.value }))}
                      style={inputStyle} />
                  </div>
                  <div>
                    <label style={labelStyle}>Entry Cooldown (min)</label>
                    <input type="number"
                      value={aiForm.cooldown_entry_minutes}
                      onChange={e => setAiForm(f => ({ ...f, cooldown_entry_minutes: e.target.value }))}
                      style={inputStyle} />
                  </div>
                </div>

                {/* Section 3: LLM Configuration */}
                <SectionDivider label="LLM Configuration" />
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Provider</label>
                  <select value={aiForm.llm_provider}
                    onChange={e => {
                      const p = e.target.value;
                      setAiForm(f => ({ ...f, llm_provider: p, llm_model: '' }));
                      fetchAIModels(p);
                    }}
                    style={inputStyle}>
                    {PROVIDERS.map(p => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </div>
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Model</label>
                  <select value={aiForm.llm_model}
                    onChange={e => setAiForm(f => ({ ...f, llm_model: e.target.value }))}
                    style={inputStyle}>
                    {aiModels.length === 0
                      ? <option value="">Loading models...</option>
                      : aiModels.map(m => (
                          <option key={m.id} value={m.id}>
                            {m.verified === false ? `⚠ ${m.display_name} (unverified)` : m.display_name}
                          </option>
                        ))
                    }
                  </select>
                </div>

                {/* Section 4: Strategy Prompt */}
                <SectionDivider label="Strategy Prompt" />
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Base Template</label>
                  <select value={aiForm.template_id}
                    onChange={e => setAiForm(f => ({ ...f, template_id: e.target.value, ...templateDataSourcePresets(e.target.value) }))}
                    style={inputStyle}>
                    <option value="">— No template —</option>
                    {aiTemplates.map(t => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </select>
                  {aiForm.template_id && (
                    <TemplatePreview
                      tmpl={aiTemplates.find(t => t.id === aiForm.template_id)}
                      form={aiForm}
                    />
                  )}
                </div>
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Custom Instructions (optional)</label>
                  <textarea
                    value={aiForm.custom_instructions}
                    onChange={e => setAiForm(f => ({ ...f, custom_instructions: e.target.value }))}
                    placeholder="Additional instructions for the LLM..."
                    rows={3}
                    style={{ ...inputStyle, resize:'vertical', fontFamily:'inherit' }}
                  />
                </div>

                {/* Section 5: Dry-Run */}
                <SectionDivider label="Dry-Run" />
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Dry Run Mode</label>
                  <div style={{
                    display:'flex', gap:'12px', padding:'9px 12px',
                    border:'1px solid var(--border)', borderRadius:'8px',
                    background:'var(--bg3)',
                  }}>
                    {([true, false] as const).map(v => (
                      <label key={String(v)} style={{
                        display:'flex', alignItems:'center', gap:'5px', cursor:'pointer',
                      }}>
                        <input type="radio"
                          checked={aiForm.dry_run === v}
                          onChange={() => setAiForm(f => ({ ...f, dry_run: v }))} />
                        <span style={{
                          fontSize:'11px', fontWeight:600,
                          color: v ? 'var(--failed-color)' : 'var(--green)',
                        }}>
                          {v ? 'ON' : 'OFF'}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              </>
            )}

            {/* Buttons */}
            <div style={{ display:'flex', gap:'10px', marginTop:'4px' }}>
              <button
                onClick={resetAddModal}
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
                disabled={
                  addLoading ||
                  !addForm.name || !addForm.symbol || !addForm.account_id ||
                  (addType === 'ai' && !aiForm.llm_model)
                }
                style={{
                  flex:1, padding:'10px', border:'none', borderRadius:'8px',
                  background: addType === 'ai' ? '#534AB7' : 'var(--blue)',
                  fontSize:'13px', fontWeight:600,
                  cursor:'pointer', color:'#fff',
                  opacity: addLoading ? 0.7 : 1,
                }}>
                {addLoading ? 'Creating...' : 'Create Strategy'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Edit Strategy Modal ── */}
      {editTarget && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,.5)',
          display:'flex', alignItems:'center', justifyContent:'center',
          zIndex:1000, overflowY:'auto', padding:'20px',
        }}>
          <div style={{
            background:'var(--bg2)', borderRadius:'var(--r)',
            padding:'28px', width:'460px', maxWidth:'95vw',
            boxShadow:'0 20px 60px rgba(0,0,0,.25)',
            maxHeight:'90vh', overflowY:'auto',
          }}>
            <h2 style={{ fontSize:'18px', fontWeight:700,
                         color:'var(--text)', marginBottom:'20px' }}>
              Edit Strategy
            </h2>

            {editError && (
              <p style={{ color:'var(--red)', fontSize:'13px',
                          marginBottom:'12px' }}>{editError}</p>
            )}

            {(editTarget.open_positions_count ?? 0) > 0 && (
              <div style={{
                background:'var(--failed-color-a)', border:'1px solid var(--failed-color-b)',
                borderRadius:'8px', padding:'10px 14px', marginBottom:'16px',
              }}>
                <p style={{ fontSize:'12px', color:'var(--failed-color)', fontWeight:600, margin:0 }}>
                  ⚠ {editTarget.open_positions_count} open position(s) — Symbol and Account are locked.
                  Close positions first to change them.
                </p>
              </div>
            )}

            {editTarget.strategy_source === 'ai_engine' ? (
              <>
                <StrategyCommonFields
                  form={editForm}
                  setForm={setEditForm}
                  accounts={accounts}
                  lockSymbolAccount={(editTarget.open_positions_count ?? 0) > 0}
                  originalCapitalAllocation={Number(editTarget.capital_allocation ?? 0)}
                />

                {/* Section 2: Operational Parameters */}
                <SectionDivider label="Operational Parameters" />
                <p style={{ fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                             letterSpacing:'.08em', color:'var(--dim)', marginBottom:'8px' }}>
                  Analysis Intervals
                </p>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:'8px', marginBottom:'14px' }}>
                  {([
                    { key:'interval_no_position'   as const, label:'No Position', opts:['1h','2h','4h','8h','1d'] },
                    { key:'interval_position_open' as const, label:'Position Open', opts:['5m','10m','15m','30m'] },
                    { key:'interval_at_risk'       as const, label:'At Risk', opts:['1m','5m','10m'] },
                  ]).map(f => (
                    <div key={f.key}>
                      <label style={{ ...labelStyle, fontSize:'10px' }}>{f.label}</label>
                      <select value={aiEditForm[f.key] as string}
                        onChange={e => setAiEditForm(af => ({ ...af, [f.key]: e.target.value }))}
                        style={{ ...inputStyle, fontSize:'12px' }}>
                        {f.opts.map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </div>
                  ))}
                </div>

                <p style={{ fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                             letterSpacing:'.08em', color:'var(--dim)', marginBottom:'8px' }}>
                  Data Sources
                </p>
                <div style={{
                  display:'grid', gridTemplateColumns:'1fr 1fr', gap:'6px',
                  marginBottom:'14px', padding:'10px 12px',
                  background:'var(--bg3)', borderRadius:'8px', border:'1px solid var(--border)',
                }}>
                  {DATA_SOURCES.map(f => (
                    <label key={f.key} style={{
                      display:'flex', alignItems:'center', gap:'6px',
                      cursor:'pointer', userSelect:'none',
                    }}>
                      <input type="checkbox"
                        checked={aiEditForm[f.key] as boolean}
                        onChange={e => setAiEditForm(af => ({ ...af, [f.key]: e.target.checked }))} />
                      <span style={{ fontSize:'11px', color:'var(--muted)' }}>{f.label}</span>
                    </label>
                  ))}
                </div>

                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px', marginBottom:'14px' }}>
                  <div>
                    <label style={labelStyle}>Confidence Threshold</label>
                    <input type="number" step="0.01" min="0.5" max="0.95"
                      value={aiEditForm.confidence_threshold}
                      onChange={e => setAiEditForm(f => ({ ...f, confidence_threshold: e.target.value }))}
                      style={inputStyle} />
                  </div>
                  <div>
                    <label style={labelStyle}>Entry Cooldown (min)</label>
                    <input type="number"
                      value={aiEditForm.cooldown_entry_minutes}
                      onChange={e => setAiEditForm(f => ({ ...f, cooldown_entry_minutes: e.target.value }))}
                      style={inputStyle} />
                  </div>
                </div>

                {/* Section 3: LLM Configuration */}
                <SectionDivider label="LLM Configuration" />
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Provider</label>
                  <select value={aiEditForm.llm_provider}
                    onChange={e => {
                      const p = e.target.value;
                      setAiEditForm(f => ({ ...f, llm_provider: p, llm_model: '' }));
                      fetchEditAiModels(p);
                    }}
                    style={inputStyle}>
                    {PROVIDERS.map(p => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </div>
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Model</label>
                  <select value={aiEditForm.llm_model}
                    onChange={e => setAiEditForm(f => ({ ...f, llm_model: e.target.value }))}
                    style={inputStyle}>
                    {editAiModels.length === 0
                      ? <option value="">Loading models...</option>
                      : editAiModels.map(m => (
                          <option key={m.id} value={m.id}>
                            {m.verified === false ? `⚠ ${m.display_name} (unverified)` : m.display_name}
                          </option>
                        ))
                    }
                  </select>
                </div>

                {/* Section 4: Strategy Prompt */}
                <SectionDivider label="Strategy Prompt" />
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Base Template</label>
                  <select value={aiEditForm.template_id}
                    onChange={e => setAiEditForm(f => ({ ...f, template_id: e.target.value, ...templateDataSourcePresets(e.target.value) }))}
                    style={inputStyle}>
                    <option value="">— No template —</option>
                    {aiTemplates.map(t => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </select>
                  {aiEditForm.template_id && (
                    <TemplatePreview
                      tmpl={aiTemplates.find(t => t.id === aiEditForm.template_id)}
                      form={aiEditForm}
                    />
                  )}
                </div>
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Custom Instructions (optional)</label>
                  <textarea
                    value={aiEditForm.custom_instructions}
                    onChange={e => setAiEditForm(f => ({ ...f, custom_instructions: e.target.value }))}
                    placeholder="Additional instructions for the LLM..."
                    rows={3}
                    style={{ ...inputStyle, resize:'vertical', fontFamily:'inherit' }}
                  />
                </div>

                {/* Section 5: Dry-Run */}
                <SectionDivider label="Dry-Run" />
                <div style={{ marginBottom:'14px' }}>
                  <label style={labelStyle}>Dry Run Mode</label>
                  <div style={{
                    display:'flex', gap:'12px', padding:'9px 12px',
                    border:'1px solid var(--border)', borderRadius:'8px',
                    background:'var(--bg3)',
                  }}>
                    {([true, false] as const).map(v => {
                      const lockedOn = v === true && !aiEditForm.dry_run && (editTarget.open_positions_count ?? 0) > 0;
                      return (
                        <label key={String(v)} style={{
                          display:'flex', alignItems:'center', gap:'5px',
                          cursor: lockedOn ? 'not-allowed' : 'pointer',
                          opacity: lockedOn ? 0.4 : 1,
                        }}>
                          <input type="radio"
                            checked={aiEditForm.dry_run === v}
                            disabled={lockedOn}
                            onChange={() => setAiEditForm(f => ({ ...f, dry_run: v }))} />
                          <span style={{
                            fontSize:'11px', fontWeight:600,
                            color: v ? 'var(--failed-color)' : 'var(--green)',
                          }}>
                            {v ? 'ON' : 'OFF'}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              </>
            ) : (
              <>
                <StrategyCommonFields
                  form={editForm}
                  setForm={setEditForm}
                  accounts={accounts}
                  lockSymbolAccount={(editTarget.open_positions_count ?? 0) > 0}
                  originalCapitalAllocation={Number(editTarget.capital_allocation ?? 0)}
                />

                <SectionDivider label="Signal Source" />
                <FieldRow label="Interval">
                  <select value={editForm.interval}
                    onChange={e => setEditForm((f:any) => ({ ...f, interval: e.target.value }))}
                    style={inputStyle}>
                    {['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d'].map(i => (
                      <option key={i} value={i}>{i}</option>
                    ))}
                  </select>
                </FieldRow>
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
                    <p style={{ fontSize:'10px', color:'var(--dim)',
                                 marginBottom:'4px', fontWeight:600 }}>Webhook URL</p>
                    <div style={{ display:'flex', gap:'6px', marginBottom:'10px' }}>
                      <code style={{
                        flex:1, padding:'6px 10px', background:'var(--bg2)',
                        border:'1px solid var(--border)', borderRadius:'6px',
                        fontSize:'11px', fontFamily:'JetBrains Mono, monospace',
                        color:'var(--text)', wordBreak:'break-all',
                      }}>
                        {webhookInfo.webhook_url}
                      </code>
                      <button onClick={() => navigator.clipboard.writeText(webhookInfo.webhook_url)}
                        style={{
                          padding:'6px 10px', border:'1px solid var(--border)',
                          borderRadius:'6px', background:'var(--bg2)',
                          fontSize:'11px', color:'var(--blue)',
                          fontWeight:600, cursor:'pointer', whiteSpace:'nowrap',
                        }}>Copy</button>
                    </div>
                    <p style={{ fontSize:'10px', color:'var(--dim)',
                                 marginBottom:'4px', fontWeight:600 }}>Token (webhook secret)</p>
                    <div style={{ display:'flex', gap:'6px', marginBottom:'10px' }}>
                      <code style={{
                        flex:1, padding:'6px 10px', background:'var(--bg2)',
                        border:'1px solid var(--border)', borderRadius:'6px',
                        fontSize:'11px', fontFamily:'JetBrains Mono, monospace',
                        color:'var(--text)', wordBreak:'break-all',
                      }}>
                        {webhookInfo.webhook_secret}
                      </code>
                      <button onClick={() => navigator.clipboard.writeText(webhookInfo.webhook_secret)}
                        style={{
                          padding:'6px 10px', border:'1px solid var(--border)',
                          borderRadius:'6px', background:'var(--bg2)',
                          fontSize:'11px', color:'var(--blue)',
                          fontWeight:600, cursor:'pointer', whiteSpace:'nowrap',
                        }}>Copy</button>
                    </div>
                    <p style={{ fontSize:'10px', color:'var(--dim)',
                                 marginBottom:'4px', fontWeight:600 }}>Alert Message (paste into TradingView)</p>
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
                        }}>Copy JSON</button>
                    </div>
                  </div>
                )}
              </>
            )}

            <div style={{ display:'flex', gap:'10px', marginTop:'4px' }}>
              <button
                onClick={() => { setEditTarget(null); setWebhookInfo(null); }}
                style={{
                  flex:1, padding:'10px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  background:'var(--bg3)', fontSize:'13px', fontWeight:600,
                  cursor:'pointer', color:'var(--muted)',
                }}>Cancel</button>
              <button
                onClick={handleEditSubmit}
                disabled={editLoading}
                style={{
                  flex:1, padding:'10px', border:'none', borderRadius:'8px',
                  background: editTarget.strategy_source === 'ai_engine' ? '#534AB7' : 'var(--blue)',
                  color:'#fff', fontSize:'13px', fontWeight:700, cursor:'pointer',
                  opacity: editLoading ? 0.7 : 1,
                }}>
                {editLoading ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Stop Strategy Confirmation Modal ── */}
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

            {(stopTarget.open_positions_count ?? 0) > 0 && (
              <div style={{
                background:'var(--failed-color-a)',
                border:'1px solid var(--failed-color-b)',
                borderRadius:'8px', padding:'12px 14px', marginBottom:'16px',
              }}>
                <p style={{ fontSize:'13px', color:'var(--failed-color)',
                             fontWeight:600, margin:0 }}>
                  ⚠ This strategy has {stopTarget.open_positions_count} open position(s).
                  Stopping will close them at market price.
                </p>
              </div>
            )}
            <p style={{ fontSize:'13px', color:'var(--dim)', marginBottom:'20px' }}>
              Stop <strong style={{ color:'var(--text)' }}>{stopTarget.name}</strong>?
            </p>
            {stopError && (
              <p style={{ color:'var(--red)', fontSize:'13px',
                          marginBottom:'12px' }}>{stopError}</p>
            )}
            <div style={{ display:'flex', gap:'10px' }}>
              <button onClick={() => setStopTarget(null)} disabled={stopping}
                style={{
                  flex:1, padding:'10px',
                  border:'1px solid var(--border)', borderRadius:'8px',
                  background:'var(--bg3)', fontSize:'13px', fontWeight:600,
                  cursor:'pointer', color:'var(--muted)',
                }}>Cancel</button>
              <button onClick={confirmStop} disabled={stopping}
                style={{
                  flex:1, padding:'10px', border:'none', borderRadius:'8px',
                  background:'var(--red)', color:'#fff',
                  fontSize:'13px', fontWeight:700, cursor:'pointer',
                  opacity: stopping ? 0.7 : 1,
                }}>
                {stopping
                  ? ((stopTarget.open_positions_count ?? 0) > 0 ? 'Closing & Stopping…' : 'Stopping…')
                  : 'Stop Strategy'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Webhook Secret Display — shown once after TV strategy creation ── */}
      {toast && (
        <div style={{
          position:'fixed', bottom:'24px', right:'24px',
          background:'#1a1a1a', border:'1px solid #d97706',
          borderRadius:'8px', padding:'12px 16px',
          fontSize:'13px', color:'#d97706', fontWeight:600,
          boxShadow:'0 4px 16px rgba(0,0,0,.4)', zIndex:1100,
          maxWidth:'360px',
        }}>
          {toast}
        </div>
      )}

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
            <p style={{ fontSize:'13px', color:'var(--dim)', marginBottom:'16px' }}>
              Save these credentials now. The webhook secret will not be shown again.
            </p>

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
                <button onClick={() => navigator.clipboard.writeText(createdSecret.secret)}
                  style={{
                    padding:'8px 12px', background:'var(--bg2)',
                    border:'1px solid var(--border)', borderRadius:'8px',
                    fontSize:'12px', cursor:'pointer', color:'var(--blue)',
                  }}>Copy</button>
              </div>
            </div>

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
                <button onClick={() => navigator.clipboard.writeText(createdSecret.url)}
                  style={{
                    padding:'8px 12px', background:'var(--bg2)',
                    border:'1px solid var(--border)', borderRadius:'8px',
                    fontSize:'12px', cursor:'pointer', color:'var(--blue)',
                  }}>Copy</button>
              </div>
            </div>

            <button
              onClick={() => { setCreatedSecret(null); navigate('/tree'); }}
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
