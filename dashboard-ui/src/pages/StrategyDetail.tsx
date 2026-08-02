import { useEffect, useState, useCallback, CSSProperties } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, Cell,
} from 'recharts';
import {
  fetchStrategyHistory, fetchTreePositions,
  type StrategyHistory, type SideSummary, type TreePosition,
} from '../api';
import { HeaderPill } from '../components/shared';
import { formatPnl, pnlColor } from '../utils/pnl';
import { formatPrice, formatSize } from '../utils/precision';
import { formatRelative } from '../utils/datetime';

const MONO = '"JetBrains Mono", monospace';
const PERIODS = [
  { key: 'today', label: '24h' },
  { key: '7d',    label: '7d'  },
  { key: '30d',   label: '30d' },
  { key: 'all',   label: 'All' },
] as const;
const TRADE_PAGE = 50;

// ── formatting helpers ──────────────────────────────────────────────────────

function money(v: number | null | undefined, dp = 2): string {
  if (v == null || isNaN(Number(v))) return '—';
  const n = Number(v);
  return `${n >= 0 ? '+' : '−'}$${Math.abs(n).toFixed(dp)}`;
}

function plain(v: number | null | undefined, dp = 2): string {
  if (v == null || isNaN(Number(v))) return '—';
  return `$${Number(v).toFixed(dp)}`;
}

function pct(v: number | null | undefined, dp = 1): string {
  if (v == null || isNaN(Number(v))) return '—';
  return `${Number(v).toFixed(dp)}%`;
}

function duration(secs: number | null | undefined): string {
  if (secs == null || isNaN(Number(secs))) return '—';
  const s = Math.round(Number(secs));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

function reasonLabel(r: string | null): string {
  if (!r) return 'Unknown';
  const map: Record<string, string> = {
    signal_close:       'Closed by signal',
    signal_flat:        'Flattened by signal',
    flip_close:         'Flipped direction',
    manual_close:       'Closed by hand',
    flatten_on_disable: 'Strategy paused',
    unknown:            'Unknown',
  };
  return map[r] ?? r;
}

// ── small building blocks ───────────────────────────────────────────────────

function Card({ title, sub, children, style }: {
  title?: string; sub?: string; children: React.ReactNode; style?: CSSProperties;
}) {
  return (
    <div style={{
      background: 'var(--bg2)', border: '1px solid var(--border)',
      borderRadius: 11, padding: '13px 14px', marginBottom: 11,
      boxShadow: '0 1px 2px rgba(20,30,50,.04)', ...style,
    }}>
      {title && (
        <div style={{ marginBottom: 10 }}>
          <div style={{
            fontSize: 11, fontWeight: 800, letterSpacing: '.07em',
            textTransform: 'uppercase', color: 'var(--dim)',
          }}>{title}</div>
          {sub && <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>{sub}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

function Stat({ label, value, color, sub }: {
  label: string; value: string; color?: string; sub?: React.ReactNode;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
      <span style={{
        fontSize: 9, fontWeight: 600, letterSpacing: '.10em',
        textTransform: 'uppercase', color: 'var(--dim)',
      }}>{label}</span>
      <span style={{
        fontFamily: MONO, fontSize: 15, fontWeight: 700, lineHeight: 1.1,
        color: color ?? 'var(--text)', wordBreak: 'break-all',
      }}>{value}</span>
      {sub && <span style={{ fontSize: 10.5, color: 'var(--dim)' }}>{sub}</span>}
    </div>
  );
}

function StatGrid({ children, cols = 3 }: { children: React.ReactNode; cols?: number }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
      gap: '13px 10px',
    }}>{children}</div>
  );
}

function KV({ k, v, vColor }: { k: string; v: string; vColor?: string }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', gap: 12,
      padding: '4px 0', fontSize: 12.5,
    }}>
      <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{k}</span>
      <span style={{ fontFamily: MONO, textAlign: 'right', color: vColor ?? 'var(--text)' }}>{v}</span>
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 10.5, color: 'var(--dim)', marginTop: 8, lineHeight: 1.45,
      display: 'flex', gap: 5,
    }}>
      <span style={{ flexShrink: 0 }}>ⓘ</span><span>{children}</span>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div style={{ fontSize: 12, color: 'var(--dim)', padding: '10px 2px' }}>{text}</div>;
}

// ── page ────────────────────────────────────────────────────────────────────

export default function StrategyDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [period, setPeriod]   = useState<string>('all');
  const [data, setData]       = useState<StrategyHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const [open, setOpen]         = useState<TreePosition[]>([]);
  const [trades, setTrades]     = useState<TreePosition[]>([]);
  const [moreTrades, setMore]   = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchStrategyHistory(id, period)
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id, period]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    Promise.all([
      fetchTreePositions(id, 'open'),
      fetchTreePositions(id, 'closed', { limit: TRADE_PAGE, offset: 0 }),
    ])
      .then(([o, c]) => {
        if (cancelled) return;
        setOpen(o);
        setTrades(c);
        setMore(c.length === TRADE_PAGE);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [id]);

  const loadMore = useCallback(async () => {
    if (!id || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = await fetchTreePositions(id, 'closed', { limit: TRADE_PAGE, offset: trades.length });
      setTrades(prev => [...prev, ...next]);
      setMore(next.length === TRADE_PAGE);
    } catch {
      setMore(false);
    } finally {
      setLoadingMore(false);
    }
  }, [id, trades.length, loadingMore]);

  if (loading && !data) {
    return <div style={{ padding: 20, color: 'var(--muted)', fontSize: 13 }}>Loading…</div>;
  }
  if (error) {
    return <div style={{ padding: 20, color: 'var(--red)', fontSize: 13 }}>{error}</div>;
  }
  if (!data) {
    return <div style={{ padding: 20, color: 'var(--muted)', fontSize: 13 }}>Strategy not found</div>;
  }

  const s = data.strategy;
  const m = data.summary;

  // daily buckets, in the viewer's own timezone
  const dailyMap = new Map<string, { pnl: number; trades: number }>();
  for (const p of data.curve) {
    const d = new Date(p.closed_at);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const e = dailyMap.get(key) ?? { pnl: 0, trades: 0 };
    e.pnl += p.pnl; e.trades += 1;
    dailyMap.set(key, e);
  }
  const daily = [...dailyMap.entries()].map(([date, v]) => ({ date, ...v }));

  const curveData = data.curve.map((p, i) => ({
    i,
    label: formatRelative(p.closed_at),
    cumulative: p.cumulative,
    drawdown: -p.drawdown,
    pnl: p.pnl,
  }));

  const netAfterFees = m.pnl_total - data.fees.total;
  const totalReturn = s.initial_allocation && s.initial_allocation > 0
    ? (m.pnl_total / s.initial_allocation) * 100
    : null;
  const drawdownBudget = s.max_drawdown_pct > 0
    ? (m.max_drawdown_pct / s.max_drawdown_pct) * 100
    : null;

  const sideRow = (name: string, x: SideSummary) => (
    <div style={{
      display: 'grid', gridTemplateColumns: '58px repeat(4, minmax(0,1fr))',
      gap: 6, alignItems: 'center', padding: '7px 0', fontSize: 12,
      borderTop: '1px solid var(--border)',
    }}>
      <HeaderPill variant={name === 'Long' ? 'long' : 'short'}>{name}</HeaderPill>
      <span style={{ fontFamily: MONO, textAlign: 'right' }}>{x.trades}</span>
      <span style={{ fontFamily: MONO, textAlign: 'right' }}>{pct(x.win_rate, 0)}</span>
      <span style={{ fontFamily: MONO, textAlign: 'right', color: pnlColor(x.pnl_total) }}>{money(x.pnl_total)}</span>
      <span style={{ fontFamily: MONO, textAlign: 'right', color: 'var(--muted)' }}>
        {x.profit_factor != null ? `${x.profit_factor.toFixed(2)}×` : '—'}
      </span>
    </div>
  );

  const tooltipStyle = {
    background: 'var(--bg2)', border: '1px solid var(--border)',
    borderRadius: 8, fontSize: 11, fontFamily: MONO, color: 'var(--text)',
  };

  return (
    <div style={{ padding: '14px 12px 90px', maxWidth: 880, margin: '0 auto' }}>

      {/* ── header ── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 12 }}>
        <button
          onClick={() => navigate(-1)}
          style={{
            background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 9,
            width: 32, height: 32, fontSize: 16, color: 'var(--muted)', cursor: 'pointer',
            flexShrink: 0, lineHeight: 1,
          }}
          aria-label="Back"
        >←</button>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{
            fontSize: 18, fontWeight: 800, letterSpacing: '-.01em',
            color: 'var(--text)', wordBreak: 'break-word',
          }}>{s.name}</div>
          <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 2 }}>
            {s.symbol} · {s.interval}
            {s.account_label && ` · ${s.account_label}`}
            {s.account_exchange && ` (${s.account_exchange})`}
          </div>
        </div>
        <HeaderPill variant={s.enabled ? 'open' : 'closed'} style={{ marginTop: 4 }}>
          {s.enabled ? 'Running' : 'Paused'}
        </HeaderPill>
      </div>

      {s.stop_reason && !s.enabled && (
        <div style={{
          background: 'var(--failed-color-a)', border: '1px solid var(--failed-color-b)',
          color: 'var(--failed-color)', borderRadius: 9, padding: '8px 11px',
          fontSize: 12, marginBottom: 11,
        }}>
          Stopped: {s.stop_reason}
        </div>
      )}

      {data.reconcile_divergent > 0 && (
        <div style={{
          background: 'var(--failed-color-a)', border: '1px solid var(--failed-color-b)',
          color: 'var(--failed-color)', borderRadius: 9, padding: '8px 11px',
          fontSize: 12, marginBottom: 11,
        }}>
          {data.reconcile_divergent} open position{data.reconcile_divergent > 1 ? 's do' : ' does'} not
          match the exchange. Check the position on the exchange.
        </div>
      )}

      {/* ── period switch ── */}
      <div style={{
        display: 'flex', gap: 3, background: 'var(--bg3)', border: '1px solid var(--border)',
        padding: 3, borderRadius: 10, width: 'max-content', marginBottom: 11,
      }}>
        {PERIODS.map(p => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            style={{
              padding: '5px 13px', borderRadius: 7, fontSize: 12, fontWeight: 600,
              border: 'none', cursor: 'pointer',
              background: period === p.key ? 'var(--bg2)' : 'transparent',
              color: period === p.key ? 'var(--text)' : 'var(--dim)',
              boxShadow: period === p.key ? '0 1px 2px rgba(20,30,50,.10)' : 'none',
            }}
          >{p.label}</button>
        ))}
      </div>

      {/* ── headline ── */}
      <Card>
        <StatGrid cols={3}>
          <Stat
            label="Realised P&L"
            value={money(m.pnl_total)}
            color={pnlColor(m.pnl_total)}
            sub={totalReturn != null ? `${pct(totalReturn)} of start capital` : undefined}
          />
          <Stat
            label="Open now"
            value={m.open_count > 0 ? money(m.open_pnl) : '—'}
            color={m.open_count > 0 ? pnlColor(m.open_pnl) : undefined}
            sub={`${m.open_count} position${m.open_count === 1 ? '' : 's'}`}
          />
          <Stat
            label="After fees"
            value={money(netAfterFees)}
            color={pnlColor(netAfterFees)}
            sub={`${plain(data.fees.total)} fees`}
          />
          <Stat
            label="Trades"
            value={String(m.trades)}
            sub={<span>
              <span style={{ color: 'var(--green)' }}>{m.wins}W</span>
              {' / '}
              <span style={{ color: 'var(--red)' }}>{m.losses}L</span>
              {m.breakeven > 0 && ` / ${m.breakeven}=`}
            </span>}
          />
          <Stat
            label="Win rate"
            value={pct(m.win_rate, 0)}
            color={m.win_rate != null ? (m.win_rate >= 50 ? 'var(--green)' : 'var(--red)') : undefined}
          />
          <Stat
            label="Profit factor"
            value={m.profit_factor != null ? `${m.profit_factor.toFixed(2)}×` : '—'}
            color={m.profit_factor != null ? (m.profit_factor >= 1 ? 'var(--green)' : 'var(--red)') : undefined}
            sub="won ÷ lost"
          />
          <Stat label="Average trade" value={money(m.avg_pnl)} color={pnlColor(m.avg_pnl)} />
          <Stat label="Average win"  value={money(m.avg_win)}  color="var(--green)" />
          <Stat label="Average loss" value={money(m.avg_loss)} color="var(--red)" />
          <Stat label="Best trade"  value={money(m.best_trade)}  color="var(--green)" />
          <Stat label="Worst trade" value={money(m.worst_trade)} color="var(--red)" />
          <Stat
            label="Max drawdown"
            value={plain(m.max_drawdown)}
            color={m.max_drawdown > 0 ? 'var(--red)' : undefined}
            sub={`${pct(m.max_drawdown_pct)}${drawdownBudget != null ? ` · ${pct(drawdownBudget, 0)} of limit` : ''}`}
          />
          <Stat
            label="Best streak"
            value={`${m.max_win_streak} W`}
            color={m.max_win_streak > 0 ? 'var(--green)' : undefined}
          />
          <Stat
            label="Worst streak"
            value={`${m.max_loss_streak} L`}
            color={m.max_loss_streak > 0 ? 'var(--red)' : undefined}
          />
          <Stat label="Avg leverage" value={m.avg_leverage != null ? `${m.avg_leverage.toFixed(1)}×` : '—'} />
        </StatGrid>
        {data.fees.coverage < 99 && (
          <Note>
            "After fees" is approximate — the exchange reported a fee for only {pct(data.fees.coverage, 0)} of
            orders in this period.
          </Note>
        )}
      </Card>

      {/* ── equity curve ── */}
      <Card title="Money over time" sub="Running total of closed-trade profit">
        {curveData.length === 0 ? <Empty text="No closed trades in this period." /> : (
          <>
            <div style={{ height: 170 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={curveData} margin={{ top: 5, right: 4, left: 4, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%"   stopColor="var(--blue)" stopOpacity={0.30} />
                      <stop offset="100%" stopColor="var(--blue)" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="i" hide />
                  <YAxis width={44} tick={{ fontSize: 10, fill: 'var(--dim)' }} tickFormatter={(v) => `$${v}`} />
                  <ReferenceLine y={0} stroke="var(--border-hi)" />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    labelFormatter={(_, p) => (p?.[0]?.payload?.label ?? '')}
                    formatter={(v: number) => [money(v), 'Total']}
                  />
                  <Area type="monotone" dataKey="cumulative" stroke="var(--blue)" strokeWidth={2} fill="url(#eq)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div style={{ height: 74, marginTop: 6 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={curveData} margin={{ top: 2, right: 4, left: 4, bottom: 0 }}>
                  <XAxis dataKey="i" hide />
                  <YAxis width={44} tick={{ fontSize: 10, fill: 'var(--dim)' }} tickFormatter={(v) => `$${v}`} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    labelFormatter={(_, p) => (p?.[0]?.payload?.label ?? '')}
                    formatter={(v: number) => [plain(Math.abs(v)), 'Below peak']}
                  />
                  <Area type="monotone" dataKey="drawdown" stroke="var(--red)" strokeWidth={1.5}
                        fill="var(--red-a)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--dim)', marginTop: 2 }}>
              Bottom line: how far below the best point the strategy was.
            </div>
          </>
        )}
      </Card>

      {/* ── per-day profit ── */}
      <Card title="Profit per day">
        {daily.length === 0 ? <Empty text="Nothing closed in this period." /> : (
          <div style={{ height: 130 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={daily} margin={{ top: 5, right: 4, left: 4, bottom: 0 }}>
                <XAxis dataKey="date" tick={{ fontSize: 9, fill: 'var(--dim)' }} tickFormatter={(d: string) => d.slice(5)} />
                <YAxis width={44} tick={{ fontSize: 10, fill: 'var(--dim)' }} tickFormatter={(v) => `$${v}`} />
                <ReferenceLine y={0} stroke="var(--border-hi)" />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v: number, _n, p: any) => [`${money(v)} · ${p.payload.trades} trades`, 'Day']}
                />
                <Bar dataKey="pnl" radius={[2, 2, 0, 0]}>
                  {daily.map((d, i) => (
                    <Cell key={i} fill={d.pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      {/* ── long vs short ── */}
      <Card title="Long vs short">
        <div style={{
          display: 'grid', gridTemplateColumns: '58px repeat(4, minmax(0,1fr))',
          gap: 6, fontSize: 9, fontWeight: 600, letterSpacing: '.08em',
          textTransform: 'uppercase', color: 'var(--dim)', paddingBottom: 5,
        }}>
          <span />
          <span style={{ textAlign: 'right' }}>Trades</span>
          <span style={{ textAlign: 'right' }}>Win %</span>
          <span style={{ textAlign: 'right' }}>P&L</span>
          <span style={{ textAlign: 'right' }}>PF</span>
        </div>
        {sideRow('Long',  data.by_side.long)}
        {sideRow('Short', data.by_side.short)}
      </Card>

      {/* ── holding time ── */}
      <Card title="How long trades stay open">
        <StatGrid cols={3}>
          <Stat label="Average" value={duration(m.avg_hold_secs)} />
          <Stat label="Shortest" value={duration(m.min_hold_secs)} />
          <Stat label="Longest"  value={duration(m.max_hold_secs)} />
        </StatGrid>
        {data.excursion && (
          <>
            <div style={{ borderTop: '1px solid var(--border)', margin: '11px 0 9px' }} />
            <StatGrid cols={2}>
              <Stat
                label="Avg best point"
                value={data.excursion.avg_mfe_r != null ? `${data.excursion.avg_mfe_r.toFixed(2)} R` : '—'}
                color="var(--green)"
                sub="how far in profit it went"
              />
              <Stat
                label="Avg worst point"
                value={data.excursion.avg_mae_r != null ? `${data.excursion.avg_mae_r.toFixed(2)} R` : '—'}
                color="var(--red)"
                sub="how far under water it went"
              />
            </StatGrid>
            <Note>
              Measured on {data.excursion.count} of {m.trades} trades ({pct(data.excursion.coverage, 0)}) —
              older trades were never tracked this way.
            </Note>
          </>
        )}
      </Card>

      {/* ── why trades ended ── */}
      <Card title="Why trades ended">
        {data.close_reasons.length === 0 ? <Empty text="No closed trades in this period." /> : (
          <>
            {data.close_reasons.map(r => {
              const share = m.trades > 0 ? (r.count / m.trades) * 100 : 0;
              return (
                <div key={r.reason} style={{ padding: '6px 0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12.5 }}>
                    <span style={{ color: 'var(--text)' }}>{reasonLabel(r.reason)}</span>
                    <span style={{ fontFamily: MONO, color: 'var(--muted)' }}>
                      {r.count} · <span style={{ color: pnlColor(r.pnl) }}>{money(r.pnl)}</span>
                    </span>
                  </div>
                  <div style={{ height: 4, background: 'var(--bg3)', borderRadius: 3, marginTop: 4 }}>
                    <div style={{
                      width: `${share}%`, height: '100%', borderRadius: 3,
                      background: r.pnl >= 0 ? 'var(--green)' : 'var(--red)', opacity: .55,
                    }} />
                  </div>
                </div>
              );
            })}
            {data.close_reasons.some(r => r.reason === 'Closed on exchange' || r.reason === 'unknown') && (
              <Note>
                "Closed on exchange" means the position was closed outside this platform, so the
                platform only noticed it afterwards.
              </Note>
            )}
          </>
        )}
      </Card>

      {/* ── signal health ── */}
      <Card title="Signals and orders" sub="Did the signals actually turn into trades?">
        <StatGrid cols={3}>
          <Stat label="Orders sent" value={String(data.orders.total)} />
          <Stat label="Filled" value={String(data.orders.filled)} color="var(--green)" />
          <Stat
            label="Not filled"
            value={String(data.orders.not_filled)}
            color={data.orders.not_filled > 0 ? 'var(--failed-color)' : undefined}
          />
        </StatGrid>
        {data.signals.length > 0 && (
          <div style={{ marginTop: 11, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
            {data.signals.map(x => (
              <KV key={x.outcome} k={x.outcome} v={String(x.count)} />
            ))}
          </div>
        )}
        <div style={{ marginTop: 11, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
          <div style={{
            fontSize: 9, fontWeight: 600, letterSpacing: '.10em', textTransform: 'uppercase',
            color: 'var(--dim)', marginBottom: 5,
          }}>Last signals</div>
          {data.recent_signals.length === 0 ? <Empty text="No signals recorded yet." /> : (
            data.recent_signals.map((sig, i) => (
              <div key={i} style={{ padding: '5px 0', borderTop: i === 0 ? 'none' : '1px solid var(--border)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12 }}>
                  <span style={{ fontFamily: MONO, color: 'var(--muted)' }}>{formatRelative(sig.received_at)}</span>
                  <span style={{
                    fontFamily: MONO,
                    color: sig.outcome === 'filled' ? 'var(--green)'
                      : sig.outcome === 'pending' ? 'var(--muted)' : 'var(--failed-color)',
                  }}>{sig.outcome ?? '—'}</span>
                </div>
                {sig.error_detail && (
                  <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2, wordBreak: 'break-word' }}>
                    {sig.error_detail.slice(0, 180)}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </Card>

      {/* ── open positions ── */}
      {open.length > 0 && (
        <Card title={`Open now (${open.length})`}>
          {open.map(p => {
            const sym = `${p.base_asset}-${p.quote_asset}`;
            return (
              <div key={p.id} style={{
                display: 'flex', justifyContent: 'space-between', gap: 10,
                padding: '7px 0', borderTop: '1px solid var(--border)', fontSize: 12.5,
              }}>
                <span style={{ display: 'flex', gap: 6, alignItems: 'center', minWidth: 0 }}>
                  <HeaderPill variant={p.side === 'buy' || p.side === 'long' ? 'long' : 'short'}>
                    {p.side === 'buy' || p.side === 'long' ? 'Long' : 'Short'}
                  </HeaderPill>
                  <span style={{ fontFamily: MONO }}>{p.base_asset}</span>
                  <span style={{ fontFamily: MONO, color: 'var(--dim)' }}>
                    {formatSize(sym, p.size, p)} @ {formatPrice(sym, p.entry_price, p)}
                  </span>
                </span>
                <span style={{ fontFamily: MONO, color: pnlColor(p.unrealized_pnl), fontWeight: 700 }}>
                  {p.unrealized_pnl != null ? formatPnl(p.unrealized_pnl) : '—'}
                </span>
              </div>
            );
          })}
        </Card>
      )}

      {/* ── trade history ── */}
      <Card title={`Trade history${trades.length ? ` (${trades.length}${moreTrades ? '+' : ''})` : ''}`}
            sub="All closed trades, newest first — not limited by the period switch">
        {trades.length === 0 ? <Empty text="No closed trades yet." /> : (
          <>
            {trades.map(t => {
              const sym = `${t.base_asset}-${t.quote_asset}`;
              const held = t.closed_at
                ? (new Date(t.closed_at).getTime() - new Date(t.opened_at).getTime()) / 1000
                : null;
              return (
                <div key={t.id} style={{ padding: '8px 0', borderTop: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                    <span style={{ display: 'flex', gap: 6, alignItems: 'center', minWidth: 0 }}>
                      <HeaderPill variant={t.side === 'buy' || t.side === 'long' ? 'long' : 'short'}>
                        {t.side === 'buy' || t.side === 'long' ? 'Long' : 'Short'}
                      </HeaderPill>
                      <span style={{ fontFamily: MONO, fontSize: 12.5 }}>{t.base_asset}</span>
                      {t.leverage ? <HeaderPill variant="lev">{t.leverage}×</HeaderPill> : null}
                    </span>
                    <span style={{
                      fontFamily: MONO, fontSize: 13, fontWeight: 700, color: pnlColor(t.realized_pnl),
                    }}>{t.realized_pnl != null ? formatPnl(t.realized_pnl) : '—'}</span>
                  </div>
                  <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: '2px 10px', marginTop: 3,
                    fontSize: 11, color: 'var(--dim)', fontFamily: MONO,
                  }}>
                    <span>{formatRelative(t.closed_at ?? t.opened_at)}</span>
                    <span>{formatSize(sym, t.size, t)} @ {formatPrice(sym, t.entry_price, t)}
                      {t.closing_price != null && ` → ${formatPrice(sym, t.closing_price, t)}`}</span>
                    <span>{duration(held)}</span>
                    <span>{reasonLabel(t.close_reason)}</span>
                  </div>
                </div>
              );
            })}
            {moreTrades && (
              <button
                onClick={loadMore}
                disabled={loadingMore}
                style={{
                  marginTop: 10, width: '100%', padding: '8px 0', borderRadius: 9,
                  border: '1px solid var(--border)', background: 'var(--bg3)',
                  color: 'var(--muted)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                }}
              >{loadingMore ? 'Loading…' : `Load ${TRADE_PAGE} more`}</button>
            )}
          </>
        )}
      </Card>

      {/* ── settings ── */}
      <Card title="Money and risk settings">
        <KV k="Allocated capital" v={plain(s.capital_allocation)} />
        {s.initial_allocation != null && <KV k="Started with" v={plain(s.initial_allocation)} />}
        {s.allocation_peak != null && <KV k="Peak capital" v={plain(s.allocation_peak)} />}
        <KV k="Margin per trade" v={plain(s.margin_per_trade)} />
        <KV k="Leverage" v={`${s.default_leverage}× (max ${s.max_leverage}×)`} />
        <KV k="Margin mode" v={s.margin_mode} />
        <KV k="Auto-stop at drawdown" v={pct(s.max_drawdown_pct, 0)} />
        <KV k="Entry trigger" v={s.entry_trigger === 'bar_close' ? 'bar close' : 'intrabar'} />
        <KV k="Signal source" v={s.strategy_source} />
        <KV k="Created" v={formatRelative(s.created_at)} />
        <KV k="Last signal" v={formatRelative(s.last_signal_at)} />
      </Card>

    </div>
  );
}
