import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, Strategy, fetchStrategies, fetchStrategyComparison, StrategyComparison } from '../api';

const PERIODS = ['today', '7d', '30d', 'all'] as const;

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<(Strategy & Partial<StrategyComparison>)[]>([]);
  const [period, setPeriod] = useState<string>('7d');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [strats, comparisons] = await Promise.all([fetchStrategies(), fetchStrategyComparison(period)]);
      console.log('Strategies:', strats);
      console.log('Comparisons:', comparisons);
      
      const safeStrats = Array.isArray(strats) ? strats : [];
      const safeComparisons = Array.isArray(comparisons) ? comparisons : [];

      const merged = safeStrats.map(s => {
        const comp = safeComparisons.find(c => c.strategy_id === s.id) || {};
        return {
          ...s,
          ...comp
        };
      }).sort((a, b) => ((b as any).pnl_total || 0) - ((a as any).pnl_total || 0));
      
      console.log('Merged strategies:', merged);
      setStrategies(merged);
    } catch (e: any) {
      console.error('Load error:', e);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [period]);

  async function toggle(id: string, enabled: boolean) {
    try {
      await api.post(`/strategies/${id}/${enabled ? 'disable' : 'enable'}`);
      setStrategies((prev) => prev.map((s) => s.id === id ? { ...s, enabled: !enabled } : s));
    } catch (e: any) {
      alert(`Failed: ${e.message}`);
    }
  }

  if (loading) return <div className="p-6 text-gray-500">Loading strategies...</div>;
  if (error) return <div className="p-6 text-red-500">Error: {error}</div>;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white">Strategies</h2>
        <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 p-1 rounded-lg">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${period === p ? 'bg-white dark:bg-gray-700 text-indigo-600 dark:text-indigo-400 shadow-sm' : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="hidden md:block overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-500 uppercase border-b border-gray-200 dark:border-gray-800">
              <th className="px-4 py-3 text-left">Strategy</th>
              <th className="px-4 py-3 text-left">Symbol</th>
              <th className="px-4 py-3 text-right">Trades</th>
              <th className="px-4 py-3 text-right">Win Rate</th>
              <th className="px-4 py-3 text-right">P&L</th>
              <th className="px-4 py-3 text-right">Open Pos</th>
              <th className="px-4 py-3 text-center">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {strategies.map(s => {
              const pnl = Number(s.pnl_total || 0);
              return (
                <tr key={s.id} className="table-row-hover">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Link to={`/strategy/${s.id}`} className="font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">{s.name}</Link>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded uppercase font-bold ${s.type === 'tradingview' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-700'}`}>
                        {s.type === 'tradingview' ? 'TV' : 'Internal'}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-600 dark:text-gray-300 font-mono text-xs">{s.symbol}</td>
                  <td className="px-4 py-3 text-right font-mono">{s.trades_count || 0}</td>
                  <td className="px-4 py-3 text-right font-mono">{Number(s.win_rate || 0).toFixed(0)}%</td>
                  <td className={`px-4 py-3 text-right font-mono font-semibold ${pnl >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                    {(pnl >= 0 ? '+' : '')}${Number(pnl).toFixed(0)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono">{s.open_positions || 0}</td>
                  <td className="px-4 py-3 text-center">
                    <button onClick={() => toggle(s.id, s.enabled)} className={`flex items-center justify-center w-full gap-2 ${s.enabled ? 'text-emerald-600' : 'text-red-600'}`}>
                      <span className="inline-block w-2 h-2 rounded-full bg-current" />
                      <span className="text-xs">{s.enabled ? 'Enabled' : 'Disabled'}</span>
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="md:hidden space-y-3">
        {strategies.map(s => (
          <div key={s.id} className="stat-card shadow-sm">
            <div className="flex justify-between items-center mb-2">
              <Link to={`/strategy/${s.id}`} className="font-bold text-gray-900 dark:text-gray-200">{s.name}</Link>
              <span className={`inline-block w-2 h-2 rounded-full ${s.enabled ? 'bg-emerald-500' : 'bg-red-500'}`} />
            </div>
            <p className="text-xs text-gray-400 font-mono mb-3">{s.symbol} · {s.platform}</p>
            <div className="flex justify-between text-sm">
              <p>{s.trades_count || 0} trades</p>
              <p className="font-bold">{Number(s.pnl_total || 0).toFixed(0)} P&L</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
