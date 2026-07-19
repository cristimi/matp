import { useState, useEffect, useCallback } from 'react';

// Spread Harvest — cross-venue funding-spread capture (docs/design/SPREAD_HARVEST.md).
// Monitor: live per-coin trailing HL-vs-Blofin spreads. Plans: armed trade cards
// with the one-tap Execute (the "confirm" of armed+confirm). Positions: open
// two-leg episodes with the abort band and a manual Close.

const API = '/api/dashboard';

interface MonitorCoin { trailing_ann_pct: number; state: string }
interface Monitor {
  enabled: boolean; enter_ann: number; exit_ann: number;
  last_run_epoch: number | null; coins: Record<string, MonitorCoin>;
}
interface Plan {
  id: string; coin: string; status: string; trailing_spread_ann: string;
  short_venue: string; long_venue: string; notional_usd: string;
  est_daily_usd: string | null; breakeven_days: string | null;
  abort_up_price: string | null; abort_down_price: string | null;
  created_at: string;
}
interface SpreadPosition {
  id: string; coin: string; status: string; short_venue: string; long_venue: string;
  size: string; notional_usd: string; short_entry_price: string | null;
  long_entry_price: string | null; abort_up_price: string; abort_down_price: string;
  pnl_realized: string | null; close_reason: string | null; opened_at: string;
}
interface FundingPlan {
  id: string; coin: string; status: string; trailing_ann: string;
  hl_funding_ann: string | null; spot_pair: string; perp_symbol: string;
  notional_usd: string; est_daily_funding_usd: string | null;
  breakeven_days: string | null; created_at: string;
}

const fmt = (v: string | number | null | undefined, dp = 2, dash = '—') =>
  v === null || v === undefined || v === '' ? dash : Number(v).toFixed(dp);

const chip = (cls: string) =>
  `inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${cls}`;

const STATUS_CHIP: Record<string, string> = {
  hot:          'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  cool:         'bg-gray-500/10 text-gray-500 dark:text-gray-400',
  armed:        'bg-indigo-500/15 text-indigo-600 dark:text-indigo-300',
  executed:     'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  expired:      'bg-gray-500/10 text-gray-500 dark:text-gray-400',
  cancelled:    'bg-gray-500/10 text-gray-500 dark:text-gray-400',
  failed:       'bg-red-500/15 text-red-600 dark:text-red-400',
  open:         'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  closed:       'bg-gray-500/10 text-gray-500 dark:text-gray-400',
  aborted:      'bg-red-500/15 text-red-600 dark:text-red-400',
  leg_failed:   'bg-red-500/15 text-red-600 dark:text-red-400',
  close_failed: 'bg-red-500/15 text-red-600 dark:text-red-400',
};

const th = 'px-3 py-2 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide';
const td = 'px-3 py-2 text-sm text-gray-800 dark:text-gray-200 whitespace-nowrap';

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</h2>
        {subtitle && <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{subtitle}</p>}
      </div>
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

export default function Spread() {
  const [monitor, setMonitor] = useState<Monitor | null>(null);
  const [fundingMonitor, setFundingMonitor] = useState<Monitor | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [fundingPlans, setFundingPlans] = useState<FundingPlan[]>([]);
  const [positions, setPositions] = useState<SpreadPosition[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [m, fm, p, fp, pos] = await Promise.all([
        fetch(`${API}/spread/monitor`).then(r => r.json()),
        fetch(`${API}/spread/funding-monitor`).then(r => r.json()),
        fetch(`${API}/spread/plans`).then(r => r.json()),
        fetch(`${API}/spread/funding-plans`).then(r => r.json()),
        fetch(`${API}/spread/positions`).then(r => r.json()),
      ]);
      if (m && m.coins) setMonitor(m);
      if (fm && fm.coins) setFundingMonitor(fm);
      setPlans(p.plans || []);
      setFundingPlans(fp.plans || []);
      setPositions(pos.positions || []);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const executePlan = async (plan: Plan) => {
    if (!window.confirm(
      `Execute ${plan.coin} spread?\n\nShort ${plan.short_venue} / long ${plan.long_venue}, ` +
      `$${fmt(plan.notional_usd, 0)}/leg. This places REAL orders on both venues.`)) return;
    setBusy(plan.id); setError(null);
    try {
      const r = await fetch(`${API}/spread/plans/${plan.id}/execute`, { method: 'POST' });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || data.error || r.statusText);
    } catch (e: any) {
      setError(`Execute ${plan.coin}: ${e.message}`);
    } finally {
      setBusy(null); refresh();
    }
  };

  const closePosition = async (pos: SpreadPosition) => {
    if (!window.confirm(`Close both ${pos.coin} legs now?`)) return;
    setBusy(pos.id); setError(null);
    try {
      const r = await fetch(`${API}/spread/positions/${pos.id}/close`, { method: 'POST' });
      const data = await r.json();
      if (!r.ok || data.closed === false) throw new Error(data.detail || data.error || 'close failed — check both venues');
    } catch (e: any) {
      setError(`Close ${pos.coin}: ${e.message}`);
    } finally {
      setBusy(null); refresh();
    }
  };

  const coins = monitor
    ? Object.entries(monitor.coins).sort((a, b) => Math.abs(b[1].trailing_ann_pct) - Math.abs(a[1].trailing_ann_pct))
    : [];
  const fundingCoins = fundingMonitor
    ? Object.entries(fundingMonitor.coins).sort((a, b) => Math.abs(b[1].trailing_ann_pct) - Math.abs(a[1].trailing_ann_pct))
    : [];

  return (
    <div className="p-4 md:p-6 space-y-5 max-w-7xl mx-auto">
      <div>
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">⚡ Harvest</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Funding-regime harvest (single-venue HL) and cross-venue spread capture (HL vs Blofin),
          both delta-neutral. Entries need your confirmation; exits (cooled regime, ±25% abort band) are automatic.
          {monitor?.last_run_epoch && (
            <span> Last monitor cycle: {new Date(monitor.last_run_epoch * 1000).toLocaleTimeString()}.</span>
          )}
        </p>
      </div>

      {error && (
        <div className="px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      <Section
        title="Spread monitor — trailing 7d HL−Blofin spread, annualized"
        subtitle={monitor ? `enter > |${(monitor.enter_ann * 100).toFixed(0)}%|/yr · exit < ${(monitor.exit_ann * 100).toFixed(0)}%/yr · positive = HL funding above Blofin (short HL)` : 'loading…'}
      >
        <div className="flex flex-wrap gap-2 p-4">
          {coins.map(([coin, c]) => (
            <div key={coin}
              className={`px-3 py-2 rounded-lg border text-sm font-mono
                ${c.state === 'hot'
                  ? 'border-amber-500/50 bg-amber-500/10'
                  : 'border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/50'}`}>
              <span className="font-semibold text-gray-900 dark:text-gray-100">{coin}</span>{' '}
              <span className={c.trailing_ann_pct >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400'}>
                {c.trailing_ann_pct >= 0 ? '+' : ''}{c.trailing_ann_pct.toFixed(1)}%
              </span>
              {c.state === 'hot' && <span className="ml-1">🔥</span>}
            </div>
          ))}
          {!coins.length && <span className="text-sm text-gray-500 p-2">No monitor data yet.</span>}
        </div>
      </Section>

      <Section
        title="Funding monitor — trailing 3d Binance funding, annualized"
        subtitle={fundingMonitor ? `single-venue HL harvest (short perp + long Unit spot) · hot > ${(fundingMonitor.enter_ann * 100).toFixed(0)}%/yr · cooled < ${(fundingMonitor.exit_ann * 100).toFixed(0)}%/yr` : 'loading…'}
      >
        <div className="flex flex-wrap gap-2 p-4">
          {fundingCoins.map(([coin, c]) => (
            <div key={coin}
              className={`px-3 py-2 rounded-lg border text-sm font-mono
                ${c.state === 'hot'
                  ? 'border-amber-500/50 bg-amber-500/10'
                  : 'border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/50'}`}>
              <span className="font-semibold text-gray-900 dark:text-gray-100">{coin}</span>{' '}
              <span className={c.trailing_ann_pct >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400'}>
                {c.trailing_ann_pct >= 0 ? '+' : ''}{c.trailing_ann_pct.toFixed(1)}%
              </span>
              {c.state === 'hot' && <span className="ml-1">🔥</span>}
            </div>
          ))}
          {!fundingCoins.length && <span className="text-sm text-gray-500 p-2">No funding-monitor data yet.</span>}
        </div>
      </Section>

      {fundingPlans.length > 0 && (
        <Section title="Funding-harvest plans" subtitle="Single-venue HL spot+perp plans. Execution for this trade is not built yet — informational; act manually if armed.">
          <table className="w-full">
            <thead className="bg-gray-50 dark:bg-gray-800/50">
              <tr>
                <th className={th}>Coin</th><th className={th}>Status</th><th className={th}>Signal</th>
                <th className={th}>HL funding</th><th className={th}>Legs</th><th className={th}>$/leg</th>
                <th className={th}>Est/day</th><th className={th}>Breakeven</th><th className={th}>Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {fundingPlans.map(p => (
                <tr key={p.id}>
                  <td className={`${td} font-semibold`}>{p.coin}</td>
                  <td className={td}><span className={chip(STATUS_CHIP[p.status] || STATUS_CHIP.cool)}>{p.status}</span></td>
                  <td className={td}>{(Number(p.trailing_ann) * 100).toFixed(1)}%/yr</td>
                  <td className={td}>{p.hl_funding_ann !== null ? `${(Number(p.hl_funding_ann) * 100).toFixed(1)}%/yr` : '—'}</td>
                  <td className={td}>short {p.perp_symbol} perp / long {p.spot_pair}</td>
                  <td className={td}>${fmt(p.notional_usd, 0)}</td>
                  <td className={td}>${fmt(p.est_daily_funding_usd)}</td>
                  <td className={td}>{fmt(p.breakeven_days, 1)}d</td>
                  <td className={td}>{new Date(p.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      <Section title="Spread plans" subtitle="Armed plans await your Execute — that is the one confirmation between signal and live legs.">
        <table className="w-full">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <th className={th}>Coin</th><th className={th}>Status</th><th className={th}>Spread</th>
              <th className={th}>Legs</th><th className={th}>$/leg</th><th className={th}>Est/day</th>
              <th className={th}>Breakeven</th><th className={th}>Abort band</th>
              <th className={th}>Created</th><th className={th}></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {plans.map(p => (
              <tr key={p.id}>
                <td className={`${td} font-semibold`}>{p.coin}</td>
                <td className={td}><span className={chip(STATUS_CHIP[p.status] || STATUS_CHIP.cool)}>{p.status}</span></td>
                <td className={td}>{(Number(p.trailing_spread_ann) * 100).toFixed(1)}%/yr</td>
                <td className={td}>short {p.short_venue} / long {p.long_venue}</td>
                <td className={td}>${fmt(p.notional_usd, 0)}</td>
                <td className={td}>${fmt(p.est_daily_usd)}</td>
                <td className={td}>{fmt(p.breakeven_days, 1)}d</td>
                <td className={td}>{fmt(p.abort_down_price, 0)} – {fmt(p.abort_up_price, 0)}</td>
                <td className={td}>{new Date(p.created_at).toLocaleString()}</td>
                <td className={td}>
                  {p.status === 'armed' && (
                    <button
                      onClick={() => executePlan(p)}
                      disabled={busy === p.id}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors">
                      {busy === p.id ? 'Executing…' : 'Execute'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!plans.length && (
              <tr><td className={`${td} text-gray-500`} colSpan={10}>No plans yet — the monitor arms one when a spread runs hot.</td></tr>
            )}
          </tbody>
        </table>
      </Section>

      <Section title="Spread positions" subtitle="Open two-leg episodes. The watcher auto-closes on the abort band or when the regime cools.">
        <table className="w-full">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <th className={th}>Coin</th><th className={th}>Status</th><th className={th}>Legs</th>
              <th className={th}>Size</th><th className={th}>Entries (S/L)</th>
              <th className={th}>Abort band</th><th className={th}>PnL</th>
              <th className={th}>Reason</th><th className={th}>Opened</th><th className={th}></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {positions.map(pos => (
              <tr key={pos.id}>
                <td className={`${td} font-semibold`}>{pos.coin}</td>
                <td className={td}><span className={chip(STATUS_CHIP[pos.status] || STATUS_CHIP.cool)}>{pos.status}</span></td>
                <td className={td}>short {pos.short_venue} / long {pos.long_venue}</td>
                <td className={td}>{Number(pos.size).toPrecision(4)} (${fmt(pos.notional_usd, 0)}/leg)</td>
                <td className={td}>{fmt(pos.short_entry_price)} / {fmt(pos.long_entry_price)}</td>
                <td className={td}>{fmt(pos.abort_down_price, 0)} – {fmt(pos.abort_up_price, 0)}</td>
                <td className={`${td} ${pos.pnl_realized && Number(pos.pnl_realized) < 0 ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'}`}>
                  {pos.pnl_realized !== null ? `$${fmt(pos.pnl_realized, 4)}` : '—'}
                </td>
                <td className={td}>{pos.close_reason || '—'}</td>
                <td className={td}>{new Date(pos.opened_at).toLocaleString()}</td>
                <td className={td}>
                  {pos.status === 'open' && (
                    <button
                      onClick={() => closePosition(pos)}
                      disabled={busy === pos.id}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-red-600 text-white hover:bg-red-500 disabled:opacity-50 transition-colors">
                      {busy === pos.id ? 'Closing…' : 'Close'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!positions.length && (
              <tr><td className={`${td} text-gray-500`} colSpan={10}>No spread positions yet.</td></tr>
            )}
          </tbody>
        </table>
      </Section>
    </div>
  );
}
