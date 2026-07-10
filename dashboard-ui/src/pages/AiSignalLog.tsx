import { useState, useEffect, useCallback, useRef } from 'react';
import { TopBar } from '../components/shared/TopBar';
import { formatRelative } from '../utils/datetime';

// ── Types ─────────────────────────────────────────────────────────────────────

interface AiSignalRow {
  id:                    number;
  strategy_id:           string;
  triggered_at:          string;
  trigger_reason:        string;
  cycle_interval:        string | null;
  prompt_template:       string | null;
  data_sources_used:     string[] | null;
  context_tokens:        number | null;
  input_tokens:          number | null;
  output_tokens:         number | null;
  total_tokens:          number | null;
  proposed_action:       string | null;
  confidence:            number | null;
  reasoning:             string | null;
  gate_passed:           boolean;
  gate_rejection_reason: string | null;
  webhook_fired:         boolean;
  webhook_status:        number | null;
  dry_run:               boolean;
  llm_provider:          string | null;
  llm_model:             string | null;
  outcome_pnl:           number | null;
  outcome_pct:           number | null;
}

// ── Badge helpers ─────────────────────────────────────────────────────────────

const ACTION_STYLE: Record<string, { bg: string; color: string; border: string }> = {
  open_long:   { bg: 'rgba(34,197,94,.12)',  color: '#22c55e', border: 'rgba(34,197,94,.3)' },
  open_short:  { bg: 'var(--red-a)',          color: 'var(--red)', border: 'var(--red-b)' },
  close_long:  { bg: 'rgba(34,197,94,.07)',  color: '#86efac', border: 'rgba(34,197,94,.2)' },
  close_short: { bg: 'rgba(239,68,68,.07)',  color: '#fca5a5', border: 'rgba(239,68,68,.2)' },
  hold:        { bg: 'var(--bg3)',            color: 'var(--muted)', border: 'var(--border)' },
};

function ActionBadge({ action }: { action: string | null }) {
  const key = action ?? 'hold';
  const s = ACTION_STYLE[key] ?? ACTION_STYLE.hold;
  return (
    <span style={{
      fontFamily: 'JetBrains Mono, monospace', fontSize: '10px',
      fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase',
      borderRadius: 'var(--pill-r)', padding: '2px 7px',
      border: '1px solid ' + s.border, background: s.bg, color: s.color,
      whiteSpace: 'nowrap', flexShrink: 0,
    }}>
      {key.replace(/_/g, ' ')}
    </span>
  );
}

function GateBadge({ passed, reason }: { passed: boolean; reason: string | null }) {
  const s = passed
    ? { bg: 'rgba(34,197,94,.12)', color: '#22c55e', border: 'rgba(34,197,94,.3)', label: 'PASSED' }
    : reason === 'llm_failed'
      ? { bg: 'var(--red-a)',    color: 'var(--red)',    border: 'var(--red-b)',    label: 'LLM FAILED' }
      : { bg: 'var(--yellow-a)', color: 'var(--yellow)', border: 'var(--yellow-b)', label: 'BLOCKED' };
  return (
    <span title={reason ?? undefined} style={{
      fontFamily: 'JetBrains Mono, monospace', fontSize: '10px',
      fontWeight: 700, letterSpacing: '.04em',
      borderRadius: 'var(--pill-r)', padding: '2px 7px',
      border: '1px solid ' + s.border, background: s.bg, color: s.color,
      whiteSpace: 'nowrap', flexShrink: 0, cursor: reason ? 'help' : 'default',
    }}>
      {s.label}
    </span>
  );
}

function LlmChip({ provider, model }: { provider: string | null; model: string | null }) {
  if (!provider && !model) return null;
  const text = provider && model ? `${provider} / ${model}` : (model ?? provider ?? '');
  return (
    <span style={{
      fontFamily: 'JetBrains Mono, monospace', fontSize: '10px', fontWeight: 500,
      borderRadius: 'var(--pill-r)', padding: '2px 7px',
      border: '1px solid rgba(83,74,183,.25)', color: '#534AB7', background: 'rgba(83,74,183,.10)',
      whiteSpace: 'nowrap', flexShrink: 1, overflow: 'hidden', textOverflow: 'ellipsis',
      maxWidth: '100%',
    }}>
      {text}
    </span>
  );
}

function ConfidenceBar({ value }: { value: number | null }) {
  if (value == null) return <span style={{ color: 'var(--dim)', fontSize: '11px' }}>—</span>;
  const pct = Math.round(value * 100);
  const color = pct >= 85 ? '#22c55e' : pct >= 70 ? '#f59e0b' : 'var(--red)';
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
      <span style={{
        width: '48px', height: '4px', borderRadius: '2px',
        background: 'var(--border)', overflow: 'hidden', display: 'inline-block',
      }}>
        <span style={{
          display: 'block', height: '100%', width: `${pct}%`,
          background: color, borderRadius: '2px',
        }} />
      </span>
      <span style={{
        fontFamily: 'JetBrains Mono, monospace', fontSize: '11px',
        color, fontWeight: 600, minWidth: '32px',
      }}>
        {pct}%
      </span>
    </span>
  );
}

// ── Row card ──────────────────────────────────────────────────────────────────

function AiSignalCard({ row }: { row: AiSignalRow }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{
      background: 'var(--bg3)', border: '1px solid var(--border)',
      borderRadius: 'var(--r)', marginBottom: '6px', overflow: 'hidden',
    }}>
      {/* ── Summary row (2 lines) ── */}
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'flex', flexDirection: 'column', gap: '5px',
          padding: '9px 14px', cursor: 'pointer', userSelect: 'none',
        }}
      >
        {/* Line 1: strategy + action — timestamp + chevron */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: '11px',
            fontWeight: 600, color: 'var(--text)', flexShrink: 1, minWidth: 0,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {row.strategy_id}
          </span>

          <ActionBadge action={row.proposed_action} />

          <span style={{
            marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace', fontSize: '10.5px',
            color: 'var(--muted)', flexShrink: 0,
          }}>
            {formatRelative(row.triggered_at)}
          </span>

          <span style={{
            fontSize: '10px', color: 'var(--dim)', flexShrink: 0,
            transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform .15s',
          }}>
            ▾
          </span>
        </div>

        {/* Line 2: gate + confidence + LLM + webhook/trigger */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
          <GateBadge passed={row.gate_passed} reason={row.gate_rejection_reason} />

          <ConfidenceBar value={row.confidence} />

          <LlmChip provider={row.llm_provider} model={row.llm_model} />

          <span style={{
            marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace',
            fontSize: '10px', color: 'var(--dim)', flexShrink: 0,
            display: 'flex', alignItems: 'center', gap: '6px',
          }}>
            {row.gate_passed && (
              <span style={{ color: row.webhook_fired ? '#22c55e' : 'var(--dim)' }}>
                {row.webhook_fired ? `webhook ${row.webhook_status ?? ''}` : row.dry_run ? 'dry run' : 'no webhook'}
              </span>
            )}
            <span>
              {row.trigger_reason?.replace(/_/g, ' ')}
              {row.cycle_interval ? ` · ${row.cycle_interval}` : ''}
            </span>
          </span>
        </div>
      </div>

      {/* ── Expanded detail ── */}
      {expanded && (
        <div style={{
          borderTop: '1px solid var(--border)', padding: '14px',
          display: 'flex', flexDirection: 'column', gap: '12px',
        }}>

          {/* Reasoning */}
          {row.reasoning && (
            <div>
              <div style={{
                fontSize: '10px', fontWeight: 600, textTransform: 'uppercase',
                letterSpacing: '.1em', color: 'var(--muted)', marginBottom: '6px',
              }}>
                LLM Reasoning
              </div>
              <div style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: '11px',
                color: 'var(--text)', lineHeight: 1.6, background: 'var(--bg2)',
                border: '1px solid var(--border)', borderRadius: '6px',
                padding: '10px', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {row.reasoning}
              </div>
            </div>
          )}

          {/* Gate rejection */}
          {!row.gate_passed && row.gate_rejection_reason && (
            <div>
              <div style={{
                fontSize: '10px', fontWeight: 600, textTransform: 'uppercase',
                letterSpacing: '.1em', color: 'var(--red)', marginBottom: '4px',
              }}>
                Gate Rejection
              </div>
              <div style={{
                fontFamily: 'JetBrains Mono, monospace', fontSize: '11px',
                color: 'var(--red)', wordBreak: 'break-word',
              }}>
                {row.gate_rejection_reason.replace(/_/g, ' ')}
              </div>
            </div>
          )}

          {/* Meta grid */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '6px',
          }}>
            {[
              { label: 'LLM',           value: row.llm_model ?? row.llm_provider },
              { label: 'Template',      value: row.prompt_template },
              { label: 'Context tokens',value: row.context_tokens != null ? String(row.context_tokens) : null },
              { label: 'Tokens (actual)',
                value: row.total_tokens != null
                  ? `${row.total_tokens} (in ${row.input_tokens ?? '?'} / out ${row.output_tokens ?? '?'})`
                  : null },
              { label: 'Cycle interval',value: row.cycle_interval },
              { label: 'Trigger',       value: row.trigger_reason?.replace(/_/g, ' ') },
              { label: 'Dry run',       value: row.dry_run ? 'yes' : 'no' },
              { label: 'Webhook status',value: row.webhook_status != null ? String(row.webhook_status) : null },
              { label: 'Outcome PnL',   value: row.outcome_pnl != null ? row.outcome_pnl.toFixed(4) : null },
              { label: 'Outcome %',     value: row.outcome_pct != null ? row.outcome_pct.toFixed(2) + '%' : null },
            ].filter(c => c.value != null).map(cell => (
              <div key={cell.label} style={{
                background: 'var(--bg2)', borderRadius: '6px',
                padding: '6px 10px', border: '1px solid var(--border)',
              }}>
                <div style={{
                  fontSize: '9px', fontWeight: 600, textTransform: 'uppercase',
                  letterSpacing: '.1em', color: 'var(--dim)', marginBottom: '2px',
                }}>
                  {cell.label}
                </div>
                <div style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: '11px', color: 'var(--text)',
                }}>
                  {cell.value}
                </div>
              </div>
            ))}
          </div>

          {/* Data sources */}
          {row.data_sources_used && row.data_sources_used.length > 0 && (
            <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
              {row.data_sources_used.map(src => (
                <span key={src} style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: '9px',
                  fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.04em',
                  background: 'var(--blue-a)', color: 'var(--blue)',
                  border: '1px solid var(--blue-b)', borderRadius: 'var(--pill-r)',
                  padding: '2px 6px',
                }}>
                  {src}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Filter select ─────────────────────────────────────────────────────────────

const BAR_ITEM_HEIGHT = 28;

function barChip(active: boolean, clear = false): React.CSSProperties {
  return {
    whiteSpace: 'nowrap',
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

function FilterSelect({
  value, onChange, active, children,
}: {
  value: string; onChange: (v: string) => void; active: boolean; children: React.ReactNode;
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{
        background: active ? 'var(--blue-a)' : 'var(--bg2)',
        border: `1px solid ${active ? 'var(--blue)' : 'var(--border)'}`,
        borderRadius: '20px', padding: '5px 12px',
        fontSize: '10px', fontWeight: 500,
        color: active ? 'var(--blue)' : 'var(--muted)',
        cursor: 'pointer', outline: 'none',
      }}
    >
      {children}
    </select>
  );
}

// ── Filters dropdown (mirrors the Tree page's grouped Filters/Sort buttons) ──

function FilterDropdown({ label, active, children }: { label: string; active: boolean; children: React.ReactNode }) {
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
      <span onClick={() => setOpen(o => !o)} style={barChip(active)}>
        {label}{open ? ' ▴' : ' ▾'}
      </span>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 20,
          background: 'var(--bg2)', border: '1px solid var(--border)',
          borderRadius: 10, padding: 8,
          boxShadow: '0 4px 16px rgba(0,0,0,.14)',
          display: 'flex', flexDirection: 'column', gap: 6,
          minWidth: 170,
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

// ── Token usage rollup (30d, from /api/ai/usage) ─────────────────────────────

interface UsageTotals   { tracked_calls: number; llm_calls: number; input_tokens: number; output_tokens: number; total_tokens: number; }
interface UsageStrategy { strategy_id: string; tracked_calls: number; input_tokens: number; output_tokens: number; total_tokens: number; }
interface UsageModel    { llm_provider: string | null; llm_model: string | null; tracked_calls: number; input_tokens: number; output_tokens: number; total_tokens: number; }

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}

function UsagePanel() {
  const [usage, setUsage] = useState<{ total: UsageTotals; per_strategy: UsageStrategy[]; per_model: UsageModel[] } | null>(null);

  useEffect(() => {
    const from = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);
    const load = () =>
      fetch(`/api/ai/usage?from=${from}`).then(r => r.json()).then(setUsage).catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (!usage || !usage.total || usage.total.tracked_calls === 0) return null;
  const t = usage.total;

  const pill: React.CSSProperties = {
    fontFamily: 'JetBrains Mono, monospace', fontSize: '10px', fontWeight: 600,
    background: 'var(--bg2)', border: '1px solid var(--border)',
    borderRadius: 'var(--pill-r)', padding: '3px 8px', whiteSpace: 'nowrap',
  };

  const providerPill: React.CSSProperties = {
    ...pill, color: '#534AB7', background: 'rgba(83,74,183,.10)', border: '1px solid rgba(83,74,183,.25)',
  };

  // Aggregate the API's (provider, model) rows up to provider-only totals.
  const byProvider = new Map<string, { calls: number; tokens: number }>();
  for (const m of usage.per_model ?? []) {
    const key = m.llm_provider ?? 'unknown';
    const cur = byProvider.get(key) ?? { calls: 0, tokens: 0 };
    cur.calls  += m.tracked_calls;
    cur.tokens += m.total_tokens;
    byProvider.set(key, cur);
  }
  const providerRows = [...byProvider.entries()]
    .filter(([, v]) => v.tokens > 0)
    .sort((a, b) => b[1].tokens - a[1].tokens);

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: '6px',
      padding: '8px 14px', borderBottom: '1px solid var(--border)', flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
        <span style={{
          fontSize: '9px', fontWeight: 700, textTransform: 'uppercase',
          letterSpacing: '.08em', color: 'var(--dim)',
        }}>
          Tokens (30d)
        </span>
        <span style={{ ...pill, color: 'var(--blue)', background: 'var(--blue-a)', border: '1px solid var(--blue-b)' }}>
          {fmtTokens(t.total_tokens)} total · in {fmtTokens(t.input_tokens)} / out {fmtTokens(t.output_tokens)} · {t.tracked_calls} calls
        </span>
        {usage.per_strategy.filter(s => s.total_tokens > 0).map(s => (
          <span key={s.strategy_id} style={{ ...pill, color: 'var(--muted)' }}>
            {s.strategy_id}: {fmtTokens(s.total_tokens)}
          </span>
        ))}
        <span style={{ fontSize: '9px', color: 'var(--dim)' }}>
          actuals since 2026-07-07 — earlier calls untracked
        </span>
      </div>

      {providerRows.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
          <span style={{
            fontSize: '9px', fontWeight: 700, textTransform: 'uppercase',
            letterSpacing: '.08em', color: 'var(--dim)',
          }}>
            By provider
          </span>
          {providerRows.map(([provider, v]) => (
            <span key={provider} style={providerPill}>
              {provider}: {fmtTokens(v.tokens)} · {v.calls} calls
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

const ACTIONS = ['open_long', 'open_short', 'close_long', 'close_short', 'hold'];

export default function AiSignalLog() {
  const [rows,    setRows]    = useState<AiSignalRow[]>([]);
  const [total,   setTotal]   = useState(0);
  const [loading, setLoading] = useState(true);
  const [page,    setPage]    = useState(1);

  const [filterStrategy,   setFilterStrategy]   = useState('all');
  const [filterAction,     setFilterAction]     = useState('all');
  const [filterGate,       setFilterGate]       = useState('all');
  const [filterWebhook,    setFilterWebhook]    = useState('all');
  const [strategies,       setStrategies]       = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    fetch('/api/dashboard/strategies')
      .then(r => r.json())
      .then((data: any[]) => setStrategies(data.map(s => ({ id: s.id, name: s.name }))))
      .catch(() => {});
  }, []);

  const fetchRows = useCallback(async (pageNum: number, append: boolean) => {
    try {
      const params = new URLSearchParams({
        limit:  String(PAGE_SIZE),
        offset: String((pageNum - 1) * PAGE_SIZE),
      });
      if (filterStrategy !== 'all') params.set('strategy_id', filterStrategy);
      if (filterAction   !== 'all') params.set('action',      filterAction);
      if (filterGate     !== 'all') params.set('gate',         filterGate);
      if (filterWebhook  !== 'all') params.set('webhook_fired', filterWebhook);

      const res  = await fetch(`/api/dashboard/ai/signals?${params}`);
      const data = await res.json();
      setTotal(data.total ?? 0);
      setRows(prev => append ? [...prev, ...data.signals] : data.signals);
    } catch {
      if (!append) setRows([]);
    } finally {
      setLoading(false);
    }
  }, [filterStrategy, filterAction, filterGate, filterWebhook]);

  useEffect(() => {
    setLoading(true);
    setPage(1);
    fetchRows(1, false);
    const id = setInterval(() => fetchRows(1, false), 30_000);
    return () => clearInterval(id);
  }, [fetchRows]);

  const handleLoadMore = () => {
    const next = page + 1;
    setPage(next);
    fetchRows(next, true);
  };

  const clearFilters = () => {
    setFilterStrategy('all');
    setFilterAction('all');
    setFilterGate('all');
    setFilterWebhook('all');
  };

  const anyFilter =
    filterStrategy !== 'all' || filterAction !== 'all' ||
    filterGate !== 'all' || filterWebhook !== 'all';

  if (loading) {
    return <div style={{ padding: '24px', color: 'var(--dim)' }}>Loading AI signal log…</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <TopBar
        title="AI Signal Log"
        right={
          <span style={{
            background: 'var(--bg3)', border: '1px solid var(--border)',
            borderRadius: '20px', padding: '4px 11px',
            fontFamily: 'JetBrains Mono, monospace', fontSize: '12px',
            color: 'var(--muted)',
          }}>
            {total} total
          </span>
        }
      />

      {/* ── Filters ── */}
      <div style={{
        display: 'flex', gap: '6px', padding: '10px 14px', alignItems: 'center',
        borderBottom: '1px solid var(--border)',
        flexWrap: 'wrap', flexShrink: 0,
      }}>
        <FilterDropdown label="Filters" active={anyFilter}>
          <FilterSelect value={filterStrategy} onChange={v => { setFilterStrategy(v); setPage(1); }} active={filterStrategy !== 'all'}>
            <option value="all">All Strategies</option>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </FilterSelect>

          <FilterSelect value={filterAction} onChange={v => { setFilterAction(v); setPage(1); }} active={filterAction !== 'all'}>
            <option value="all">All Actions</option>
            {ACTIONS.map(a => <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>)}
          </FilterSelect>

          <FilterSelect value={filterGate} onChange={v => { setFilterGate(v); setPage(1); }} active={filterGate !== 'all'}>
            <option value="all">Gate: All</option>
            <option value="passed">Gate: Passed</option>
            <option value="blocked">Gate: Blocked</option>
            <option value="llm_failed">Gate: LLM Failed</option>
          </FilterSelect>

          <FilterSelect value={filterWebhook} onChange={v => { setFilterWebhook(v); setPage(1); }} active={filterWebhook !== 'all'}>
            <option value="all">Webhook: All</option>
            <option value="true">Webhook: Fired</option>
            <option value="false">Webhook: Not fired</option>
          </FilterSelect>
        </FilterDropdown>

        {anyFilter && (
          <span onClick={clearFilters} style={barChip(false, true)}>
            ✕ Clear
          </span>
        )}
      </div>

      {/* ── Token usage rollup ── */}
      <UsagePanel />

      {/* ── List ── */}
      <div style={{
        flex: 1, overflowY: 'auto', padding: '10px 14px 80px',
        scrollbarWidth: 'none',
      }}>
        {rows.length === 0 ? (
          <p style={{ color: 'var(--dim)', textAlign: 'center', padding: '40px 0' }}>
            No AI signal log entries found.
          </p>
        ) : (
          rows.map(row => <AiSignalCard key={row.id} row={row} />)
        )}

        {total > rows.length && (
          <div style={{ textAlign: 'center', padding: '16px 0 24px' }}>
            <button
              onClick={handleLoadMore}
              style={{
                padding: '8px 20px',
                border: '1px solid var(--border)', borderRadius: '20px',
                background: 'var(--bg2)', fontSize: '12px', fontWeight: 600,
                color: 'var(--blue)', cursor: 'pointer',
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
