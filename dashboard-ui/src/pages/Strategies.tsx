import { useEffect, useState, useCallback } from 'react';
import { api, Strategy } from '../api';

function IntervalBadge({ interval }: { interval: string }) {
  return (
    <span className="badge badge-gray">{interval}</span>
  );
}

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<Strategy[]>('/strategies');
      setStrategies(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function toggle(id: string, enabled: boolean) {
    setToggling(id);
    try {
      await api.post(`/strategies/${id}/${enabled ? 'disable' : 'enable'}`);
      setStrategies((prev) =>
        prev.map((s) => s.id === id ? { ...s, enabled: !enabled } : s)
      );
    } catch (e: any) {
      alert(`Failed: ${e.message}`);
    } finally {
      setToggling(null);
    }
  }

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-white">Strategies</h2>
        <button className="btn-ghost text-sm" onClick={load}>↻ Refresh</button>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-800 rounded-xl p-4 text-red-300 text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-16 bg-gray-800 rounded-xl animate-pulse" />
          ))}
        </div>
      ) : strategies.length === 0 ? (
        <div className="text-center py-16 text-gray-600">
          <p className="text-4xl mb-3">⚙️</p>
          <p className="mb-1">No strategies loaded</p>
          <p className="text-xs text-gray-700 max-w-xs mx-auto">
            Add YAML files to the <code className="text-gray-500">strategies_config/</code> volume and restart the order-generator service.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {strategies.map((s) => (
            <div key={s.id} className="stat-card flex items-center gap-4">
              {/* Toggle */}
              <button
                onClick={() => toggle(s.id, s.enabled)}
                disabled={toggling === s.id}
                className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
                  s.enabled ? 'bg-indigo-600' : 'bg-gray-700'
                } ${toggling === s.id ? 'opacity-50' : ''}`}
              >
                <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${
                  s.enabled ? 'translate-x-5' : ''
                }`} />
              </button>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-gray-200 text-sm">{s.name}</span>
                  <IntervalBadge interval={s.interval} />
                  <span className="badge badge-gray">{s.platform}</span>
                  {!s.enabled && <span className="badge badge-gray">paused</span>}
                </div>
                <p className="text-xs text-gray-500 mt-0.5 font-mono">{s.symbol} · {s.id}</p>
              </div>

              {/* Last signal */}
              <div className="text-right shrink-0">
                <p className="text-xs text-gray-600">Last signal</p>
                <p className="text-xs text-gray-400 font-mono">
                  {s.last_signal_time
                    ? new Date(s.last_signal_time).toLocaleTimeString()
                    : '—'}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-xs text-gray-500 space-y-1">
        <p className="text-gray-400 font-medium mb-2">Adding a new strategy</p>
        <p>1. Create a <code className="text-gray-300">.yaml</code> config in the <code className="text-gray-300">strategies_config/</code> Docker volume</p>
        <p>2. Restart the <code className="text-gray-300">order-generator</code> container</p>
        <p>3. The strategy will appear here and can be enabled/disabled in real time</p>
      </div>
    </div>
  );
}
