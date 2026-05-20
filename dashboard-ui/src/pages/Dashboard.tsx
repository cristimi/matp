import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { api, Stats } from '../api';
import { StatPanel } from '../components/StatPanel';
import { LiveFeed } from '../components/LiveFeed';
import { PlatformSelector } from '../components/PlatformSelector';

const PERIODS = ['today', '7d', '30d', 'all'] as const;

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [period, setPeriod] = useState<string>('today');
  const [activePlatform, setActivePlatform] = useState('blofin');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<{ active_platform: { value: string } }>('/config').then((cfg) => {
      setActivePlatform(cfg.active_platform?.value ?? 'blofin');
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    api.get<Stats>(`/stats?period=${period}`)
      .then(setStats)
      .finally(() => setLoading(false));
  }, [period]);

  const pnlColor = !stats ? 'default' : stats.total_pnl >= 0 ? 'green' : 'red';

  // Build chart data from by_platform
  const chartData = stats
    ? Object.entries(stats.by_platform ?? {}).map(([name, s]: [string, any]) => ({
        name,
        orders: s.total_orders ?? 0,
        pnl: parseFloat(s.total_pnl ?? 0),
      }))
    : [];

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
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

      {/* Stat cards */}
      {loading ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="stat-card animate-pulse h-24 bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800" />
          ))}
        </div>
      ) : stats ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatPanel label="Total Orders" value={stats.total_orders} />
          <StatPanel
            label="Win Rate"
            value={`${stats.win_rate}%`}
            sub={`${stats.win_count}W / ${stats.loss_count}L`}
            color={stats.win_rate >= 50 ? 'green' : 'red'}
          />
          <StatPanel
            label="Total P&L"
            value={`$${stats.total_pnl.toFixed(2)}`}
            sub={`Avg $${stats.avg_pnl.toFixed(2)} / trade`}
            color={pnlColor}
          />
          <StatPanel label="Failed Orders" value={stats.failed} color={stats.failed > 0 ? 'red' : 'default'} />
        </div>
      ) : null}

      {/* Chart + Live Feed */}
      <div className="grid md:grid-cols-2 gap-4">
        {/* Platform breakdown bar chart */}
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
                  contentStyle={{ 
                    backgroundColor: 'var(--tw-bg-opacity, #ffffff)', 
                    borderColor: '#e5e7eb', 
                    borderRadius: 8,
                    fontSize: 12
                  }}
                  itemStyle={{ fontSize: 12 }}
                />
                <Bar dataKey="orders" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Live feed */}
        <LiveFeed />
      </div>
    </div>
  );
}
