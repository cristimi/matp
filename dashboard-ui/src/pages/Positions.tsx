import { useEffect, useState, useCallback } from 'react';
import { api, Position } from '../api';
import { SideBadge, PlatformBadge } from '../components/Badges';

export default function PositionsPage() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<Position[]>('/positions');
      setPositions(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, [load]);

  async function closePosition(symbol: string, side: string) {
    setClosing(symbol);
    try {
      await api.post(`/positions/${encodeURIComponent(symbol)}/close`, { side });
      await load();
    } catch (e: any) {
      alert(`Close failed: ${e.message}`);
    } finally {
      setClosing(null);
    }
  }

  function formatPrice(symbol: string, price: string | number) {
    const p = Number(price);
    if (isNaN(p)) return price;
    
    // Simple tick size mapping based on symbol
    if (symbol.includes('BTC') || symbol.includes('ETH')) {
      return p.toFixed(2);
    } else if (symbol.includes('SOL')) {
      return p.toFixed(3);
    } else if (symbol.includes('XRP') || symbol.includes('ADA') || symbol.includes('DOGE')) {
      return p.toFixed(4);
    } else if (p < 1) {
      return p.toFixed(6);
    }
    return p.toFixed(2);
  }

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white transition-colors">Open Positions</h2>
        <button className="btn-ghost text-sm" onClick={load}>↻ Refresh</button>
      </div>

      {loading && (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-20 bg-white dark:bg-gray-800 rounded-xl animate-pulse border border-gray-100 dark:border-gray-700" />
          ))}
        </div>
      )}

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-4 text-red-600 dark:text-red-300 text-sm">
          {error}
        </div>
      )}

      {!loading && !error && positions.length === 0 && (
        <div className="text-center py-20 text-gray-400">
          <p className="text-5xl mb-4">📭</p>
          <p className="font-medium text-gray-500">No open positions</p>
          <p className="text-xs mt-1">Wait for a strategy to open a trade.</p>
        </div>
      )}

      {!loading && positions.length > 0 && (
        <>
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 transition-colors">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 uppercase border-b border-gray-200 dark:border-gray-800">
                  {['Symbol', 'Side', 'Size', 'Entry', 'Mark', 'Unrealized P&L', 'Liq. Price', 'Platform', ''].map((h) => (
                    <th key={h} className="px-4 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                {positions.map((p) => {
                  const pnl = parseFloat(p.unrealizedPnl ?? '0');
                  return (
                    <tr key={`${p.symbol}-${p.side}`} className="table-row-hover">
                      <td className="px-4 py-3 font-mono font-semibold text-gray-900 dark:text-gray-200">{p.symbol}</td>
                      <td className="px-4 py-3"><SideBadge side={p.side} /></td>
                      <td className="px-4 py-3 font-mono text-gray-600 dark:text-gray-300">{p.size}</td>
                      <td className="px-4 py-3 font-mono text-gray-400 dark:text-gray-500">{formatPrice(p.symbol, p.entryPx)}</td>
                      <td className="px-4 py-3 font-mono text-gray-600 dark:text-gray-300">{formatPrice(p.symbol, p.markPx)}</td>
                      <td className={`px-4 py-3 font-mono font-semibold ${pnl >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                      </td>
                      <td className="px-4 py-3 font-mono text-gray-400 dark:text-gray-500">{p.liquidationPx ?? '—'}</td>
                      <td className="px-4 py-3"><PlatformBadge platform={p.platform} /></td>
                      <td className="px-4 py-3">
                        <button
                          className="btn-danger text-xs py-1 shadow-sm"
                          disabled={closing === p.symbol}
                          onClick={() => closePosition(p.symbol, p.side)}
                        >
                          {closing === p.symbol ? 'Closing…' : 'Close'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-3">
            {positions.map((p) => {
              const pnl = parseFloat(p.unrealizedPnl ?? '0');
              return (
                <div key={`${p.symbol}-${p.side}`} className="stat-card space-y-3 shadow-sm transition-colors">
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-bold text-gray-900 dark:text-gray-200">{p.symbol}</span>
                    <div className="flex gap-2">
                      <SideBadge side={p.side} />
                      <PlatformBadge platform={p.platform} />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <p className="text-xs text-gray-400 dark:text-gray-600 uppercase font-medium">Size</p>
                      <p className="font-mono text-gray-700 dark:text-gray-300">{p.size}</p>
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 dark:text-gray-600 uppercase font-medium">Entry</p>
                      <p className="font-mono text-gray-700 dark:text-gray-300">{formatPrice(p.symbol, p.entryPx)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 dark:text-gray-600 uppercase font-medium">Mark</p>
                      <p className="font-mono text-gray-700 dark:text-gray-300">{formatPrice(p.symbol, p.markPx)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 dark:text-gray-600 uppercase font-medium">Unrealized P&L</p>
                      <p className={`font-mono font-bold ${pnl >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                      </p>
                    </div>
                  </div>
                  <button
                    className="btn-danger w-full text-sm shadow-sm"
                    disabled={closing === p.symbol}
                    onClick={() => closePosition(p.symbol, p.side)}
                  >
                    {closing === p.symbol ? 'Closing…' : 'Close Position'}
                  </button>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
