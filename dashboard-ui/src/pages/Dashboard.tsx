import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { api, Stats, fetchStrategyComparison, StrategyComparison } from '../api';
import { StatPanel } from '../components/StatPanel';
import { LiveFeed } from '../components/LiveFeed';
import { PlatformSelector } from '../components/PlatformSelector';

const PERIODS = ['today', '7d', '30d', 'all'] as const;

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [strategies, setStrategies] = useState<StrategyComparison[]>([]);
  const [period, setPeriod] = useState<string>('7d');
  const [activePlatform, setActivePlatform] = useState('blofin');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ active_platform: { value: string } }>('/config').then((cfg) => {
      setActivePlatform(cfg.active_platform?.value ?? 'blofin');
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.get<Stats>(`/stats?period=${period}`),
      fetchStrategyComparison(period)
    ])
      .then(([stats, strats]) => {
        setStats(stats);
        setStrategies(strats);
      })
      .catch((e) => {
        console.error('Failed to load dashboard:', e);
        setError(e.message || 'Failed to load data');
      })
      .finally(() => setLoading(false));
  }, [period]);

  const pnlColor = !stats ? 'default' : stats.total_pnl >= 0 ? 'green' : 'red';
  const chartData = stats
    ? Object.entries(stats.by_platform ?? {}).map(([name, s]: [string, any]) => ({
        name,
        orders: parseInt(s.total_orders ?? 0),
        pnl: parseFloat(s.total_pnl ?? 0),
      }))
    : [];

  if (loading) return <div className="p-6 text-gray-500">Loading dashboard...</div>;
  if (error) return <div className="p-6 text-red-500">Error: {error}</div>;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-gray-900 dark:text-white transition-colors">Overview</h2>
          <p className="text-xs text-gray-500 mt-0.5">Automated Trading Platform</p>
        </div>
        <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
          <PlatformSelector current={activePlatform} onChange={setActivePlatform} />
          <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 p-1 rounded-lg">
            {PERIODS.map((p) => (
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
        </div>
      </div>

      {stats ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatPanel
            label="Total Orders"
            value={stats.total_orders ?? 0}
            sub={
              <span>
                <span className="text-emerald-500">{stats.long_count ?? 0} long</span>
                {' / '}
                <span className="text-red-500">{stats.short_count ?? 0} short</span>
              </span>
            }
          />
          <StatPanel
            label="Win Rate"
            value={`${Number(stats.win_rate || 0).toFixed(0)}%`}
            sub={`${stats.win_count ?? 0}W / ${stats.loss_count ?? 0}L`}
            color={(stats.win_rate || 0) >= 50 ? 'green' : 'red'}
          />
          <StatPanel
            label="Total P&L"
            value={`$${Number(stats.total_pnl || 0).toFixed(2)}`}
            sub={
              (() => {
                const u = Number(stats.unrealized_pnl || 0);
                const sign = u >= 0 ? '+' : '';
                return (
                  <span className={u >= 0 ? 'text-emerald-500' : 'text-red-500'}>
                    {sign}${u.toFixed(2)} unrealized
                  </span>
                );
              })()
            }
            color={pnlColor}
          />
          <StatPanel label="Failed Orders" value={stats.failed ?? 0} color={(stats.failed ?? 0) > 0 ? 'red' : 'default'} />
        </div>
      ) : null}

      <div className="stat-card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Strategy Performance</h3>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-500 uppercase border-b border-gray-200 dark:border-gray-800">
              <th className="py-2 text-left">Strategy</th>
              <th className="py-2 text-right">Trades</th>
              <th className="py-2 text-right">Win Rate</th>
              <th className="py-2 text-right hidden md:table-cell">W / L</th>
              <th className="py-2 text-right hidden md:table-cell">Max DD</th>
              <th className="py-2 text-right hidden md:table-cell">PF</th>
              <th className="py-2 text-right">P&L</th>
              <th className="py-2 text-center">●</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {(strategies || []).slice(0, 5).map(s => {
              const pnl = Number(s.pnl_total || 0);
              const pf  = s.profit_factor != null ? Number(s.profit_factor).toFixed(2) : '—';
              return (
                <tr key={s.strategy_id} className="table-row-hover">
                  <td className="py-3">
                    <Link to={`/strategy/${s.strategy_id}`} className="font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">
                      {s.name}
                    </Link>
                  </td>
                  <td className="py-3 text-right font-mono text-gray-600 dark:text-gray-300">{s.trades_count || 0}</td>
                  <td className="py-3 text-right font-mono text-gray-600 dark:text-gray-300">{Number(s.win_rate || 0).toFixed(0)}%</td>
                  <td className="py-3 text-right font-mono text-gray-500 dark:text-gray-400 text-xs hidden md:table-cell">
                    <span className="text-emerald-600 dark:text-emerald-400">{s.trades_won || 0}</span>
                    {' / '}
                    <span className="text-red-500 dark:text-red-400">{s.trades_lost || 0}</span>
                  </td>
                  <td className="py-3 text-right font-mono text-gray-500 dark:text-gray-400 text-xs hidden md:table-cell">
                    {Number(s.max_drawdown || 0).toFixed(1)}%
                  </td>
                  <td className="py-3 text-right font-mono text-gray-500 dark:text-gray-400 text-xs hidden md:table-cell">
                    {pf !== '—' ? `${pf}×` : '—'}
                  </td>
                  <td className={`py-3 text-right font-mono font-semibold ${pnl >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                    {pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(0)}
                  </td>
                  <td className="py-3 text-center">
                    <span className={`inline-block w-2 h-2 rounded-full ${(s.open_positions || 0) > 0 ? 'bg-emerald-500' : 'bg-gray-400'}`} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {(strategies || []).length > 5 && (
          <Link to="/strategies" className="block text-center text-xs text-indigo-600 dark:text-indigo-400 hover:underline pt-4">
            View All Strategies →
          </Link>
        )}
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="stat-card">
          <h3 className="text-sm font-semibold text-gray-600 dark:text-gray-300 mb-4">Orders by Platform</h3>
          {chartData.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <p className="text-gray-400 text-sm italic">No data for this period</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={chartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <XAxis dataKey="name" tick={{ fill: '#9ca3af', fontSize: 11 }} axisLine={{ stroke: '#e5e7eb' }} />
                <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} axisLine={{ stroke: '#e5e7eb' }} />
                <Tooltip
                  cursor={{ fill: 'transparent' }}
                  contentStyle={{ backgroundColor: '#1f2937', borderColor: '#e5e7eb', borderRadius: 8, fontSize: 12 }}
                  itemStyle={{ fontSize: 12 }}
                />
                <Bar dataKey="orders" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
        <LiveFeed />
      </div>
    </div>
  );
}
