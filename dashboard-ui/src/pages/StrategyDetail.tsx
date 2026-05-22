import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, Strategy, StrategyStats, EquityCurvePoint, Position, fetchStrategyStats, fetchEquityCurve, fetchStrategyPositions } from '../api';
import { SideBadge, PlatformBadge } from '../components/Badges';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { StatPanel } from '../components/StatPanel';

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
        api.get<Strategy[]>(`/strategies`),
        fetchStrategyStats(id, period),
        fetchEquityCurve(id, 30),
        fetchStrategyPositions(id)
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

  if (!strategy && !loading) return <div className="p-6">Strategy not found</div>;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center gap-4">
        <a href="/strategies" className="text-gray-500 hover:text-gray-900 dark:hover:text-white">←</a>
        <div>
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">{strategy?.name}</h2>
          <p className="text-xs text-gray-500">
            {strategy?.symbol} · {strategy?.interval} · {strategy?.platform} · Tags: {(strategy?.tags || []).join(', ')}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatPanel label="Trades" value={stats?.trades_count || 0} />
        <StatPanel label="Win Rate" value={`${Number(stats?.win_rate || 0).toFixed(0)}%`} />
        <StatPanel label="Total P&L" value={`$${Number(stats?.pnl_total || 0).toFixed(0)}`} color={Number(stats?.pnl_total || 0) >= 0 ? 'green' : 'red'} />
        <StatPanel label="Max Drawdown" value={`${Number(stats?.max_drawdown || 0).toFixed(0)}%`} />
      </div>

      <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 p-1 rounded-lg w-max">
        {PERIODS.map(p => (
          <button key={p} onClick={() => setPeriod(p)} className={`px-3 py-1 rounded-md text-xs ${period === p ? 'bg-white dark:bg-gray-700 text-indigo-600' : 'text-gray-500'}`}>
            {p}
          </button>
        ))}
      </div>

      <div className="stat-card h-64">
        <h3 className="text-sm font-semibold mb-4">Cumulative P&L</h3>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={curve}>
            <XAxis dataKey="date" hide />
            <YAxis hide />
            <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none' }} />
            <Line type="monotone" dataKey="cumulative" stroke="#6366f1" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="stat-card">
        <h3 className="text-sm font-semibold mb-4">Open Positions</h3>
        {positions.length === 0 ? <p className="text-sm text-gray-500">No open positions</p> : (
            <table className="w-full text-sm">
                <thead><tr className="text-xs text-gray-500">
                    <th className="text-left py-2">Symbol</th><th className="text-right py-2">Side</th><th className="text-right py-2">Size</th><th className="text-right py-2">Entry</th><th className="text-right py-2">P&L</th>
                </tr></thead>
                <tbody>{positions.map(p => (
                    <tr key={p.symbol} className="border-t border-gray-800">
                        <td className="py-2">{p.symbol}</td><td className="text-right"><SideBadge side={p.side} /></td><td className="text-right font-mono">{p.size}</td><td className="text-right font-mono">{p.entryPx}</td><td className="text-right font-mono">{p.unrealizedPnl}</td>
                    </tr>
                ))}</tbody>
            </table>
        )}
      </div>

      <div className="stat-card">
        <h3 className="text-sm font-semibold mb-4">Risk Limits</h3>
        <div className="grid grid-cols-3 text-xs text-gray-500">
            <p>Max Size: {strategy?.max_position_size}</p>
            <p>Max Leverage: {strategy?.max_leverage}x</p>
            <p>Max Drawdown: {strategy?.max_daily_drawdown_percent}%</p>
        </div>
      </div>
    </div>
  );
}
