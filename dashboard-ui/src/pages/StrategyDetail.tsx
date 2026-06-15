import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api, Strategy, StrategyStats, EquityCurvePoint, Position, fetchStrategyStats, fetchEquityCurve, fetchStrategyPositions } from '../api';
import { SideBadge } from '../components/Badges';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { StatPanel } from '../components/StatPanel';
import { LiveFeed } from '../components/LiveFeed';

const PERIODS = ['today', '7d', '30d', 'all'] as const;

export default function StrategyDetail() {
  const { id } = useParams<{ id: string }>();
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [stats, setStats] = useState<StrategyStats | null>(null);
  const [curve, setCurve] = useState<EquityCurvePoint[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [period, setPeriod] = useState<string>('7d');
  const [loading, setLoading] = useState(true);

  const load = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const [strats, statsData, curveData, posData] = await Promise.all([
        api.get<Strategy[]>('/strategies'),
        fetchStrategyStats(id, period),
        fetchEquityCurve(id, 30),
        fetchStrategyPositions(id),
      ]);
      setStrategy(strats.find(s => s.id === id) || null);
      setStats(statsData);
      setCurve(curveData);
      setPositions(posData);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [id, period]);

  if (loading) return <div className="p-6 text-gray-500">Loading…</div>;
  if (!strategy) return <div className="p-6">Strategy not found</div>;

  const pnlRealized   = Number(stats?.pnl_total    || 0);
  const pnlUnrealized = Number(stats?.unrealized_pnl ?? 0);
  const pf            = stats?.profit_factor != null ? Number(stats.profit_factor).toFixed(2) : null;

  return (
    <div className="p-4 md:p-6 space-y-6">

      {/* ── Header ── */}
      <div className="flex items-center gap-4">
        <Link to="/strategies" className="text-gray-500 hover:text-gray-900 dark:hover:text-white text-lg">←</Link>
        <div>
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">{strategy.name}</h2>
          <p className="text-xs text-gray-500">
            {strategy.symbol} · {strategy.interval}
            {strategy.account_id && ` · ${strategy.account_id}`}
          </p>
        </div>
        <span className={`ml-auto inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
          strategy.enabled
            ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
            : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
        }`}>
          <span className={`w-1.5 h-1.5 rounded-full ${strategy.enabled ? 'bg-emerald-500' : 'bg-gray-400'}`} />
          {strategy.enabled ? 'Active' : 'Stopped'}
        </span>
      </div>

      {/* ── Period picker ── */}
      <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 p-1 rounded-lg w-max">
        {PERIODS.map(p => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
              period === p
                ? 'bg-white dark:bg-gray-700 text-indigo-600 dark:text-indigo-400 shadow-sm'
                : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {/* ── Stat cards ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* P&L */}
        <StatPanel
          label="Total P&L"
          value={`${pnlRealized >= 0 ? '+' : ''}$${pnlRealized.toFixed(2)}`}
          sub={
            <span className={pnlUnrealized >= 0 ? 'text-emerald-500' : 'text-red-500'}>
              {pnlUnrealized >= 0 ? '+' : ''}${pnlUnrealized.toFixed(2)} unrealized
            </span>
          }
          color={pnlRealized >= 0 ? 'green' : 'red'}
        />

        {/* Trades with long/short */}
        <StatPanel
          label="Trades"
          value={stats?.trades_count || 0}
          sub={
            <span>
              <span className="text-emerald-500">{stats?.long_count ?? 0} long</span>
              {' / '}
              <span className="text-red-500">{stats?.short_count ?? 0} short</span>
            </span>
          }
        />

        {/* Win Rate + W/L + PF */}
        <StatPanel
          label="Win Rate"
          value={`${Number(stats?.win_rate || 0).toFixed(0)}%`}
          sub={
            <span>
              <span className="text-emerald-500">{stats?.trades_won ?? 0}W</span>
              {' / '}
              <span className="text-red-500">{stats?.trades_lost ?? 0}L</span>
              {pf && <span className="text-gray-400 ml-1">· PF {pf}×</span>}
            </span>
          }
          color={(stats?.win_rate || 0) >= 50 ? 'green' : 'red'}
        />

        {/* Max Drawdown */}
        <StatPanel
          label="Max Drawdown"
          value={`${Number(stats?.max_drawdown || 0).toFixed(1)}%`}
          color={Number(stats?.max_drawdown || 0) > 10 ? 'red' : 'default'}
        />
      </div>

      {/* ── Cumulative P&L chart ── */}
      {curve.length > 0 && (
        <div className="stat-card h-56">
          <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Cumulative P&L</h3>
          <ResponsiveContainer width="100%" height="85%">
            <LineChart data={curve}>
              <XAxis dataKey="date" hide />
              <YAxis hide />
              <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none', fontSize: 12 }} />
              <Line type="monotone" dataKey="cumulative" stroke="#6366f1" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── Live Feed (filtered to this strategy) ── */}
      <LiveFeed strategyId={id} />

      {/* ── Open Positions ── */}
      <div className="stat-card">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">Open Positions</h3>
        {positions.length === 0 ? (
          <p className="text-sm text-gray-500 italic">No open positions</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-500 uppercase border-b border-gray-200 dark:border-gray-800">
                <th className="py-2 text-left">Symbol</th>
                <th className="py-2 text-right">Side</th>
                <th className="py-2 text-right">Size</th>
                <th className="py-2 text-right">Entry</th>
                <th className="py-2 text-right">Unr. P&L</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => {
                const upnl = Number(p.unrealizedPnl || 0);
                return (
                  <tr key={i} className="border-t border-gray-100 dark:border-gray-800">
                    <td className="py-2 font-mono">{p.pair.label}</td>
                    <td className="py-2 text-right"><SideBadge side={p.side} /></td>
                    <td className="py-2 text-right font-mono">{p.size}</td>
                    <td className="py-2 text-right font-mono">{p.entryPx}</td>
                    <td className={`py-2 text-right font-mono font-semibold ${upnl >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                      {upnl >= 0 ? '+' : ''}${upnl.toFixed(2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Risk Limits ── */}
      <div className="stat-card">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Risk Limits</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-xs text-gray-500 dark:text-gray-400">
          <div><span className="font-medium text-gray-700 dark:text-gray-300">Max Leverage</span><br />{strategy.max_leverage}×</div>
        </div>
      </div>

    </div>
  );
}
