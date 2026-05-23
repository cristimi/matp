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

  async function closePosition(symbol: string, side: string, platform: string) {
    setClosing(symbol);
    try {
      await api.post(`/positions/${encodeURIComponent(symbol)}/close`, { side, platform });
      await load();
    } catch (e: any) {
      alert(`Close failed: ${e.message}`);
    } finally {
      setClosing(null);
    }
  }

  function formatSize(symbol: string, size: string | number) {
    const s = parseFloat(String(size));
    return s.toFixed(4);
  }

  function formatPrice(symbol: string, price: string | number | null | undefined) {
    if (price === null || price === undefined) return '—';
    const p = Number(price);
    if (isNaN(p)) return String(price);
    
    if (symbol.includes('BTC') || symbol.includes('ETH')) return p.toFixed(2);
    else if (symbol.includes('SOL')) return p.toFixed(3);
    else if (symbol.includes('XRP') || symbol.includes('ADA') || symbol.includes('DOGE')) return p.toFixed(4);
    else if (p < 1) return p.toFixed(6);
    return p.toFixed(2);
  }

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white transition-colors">Positions</h2>
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
          <p className="font-medium text-gray-500">No positions found</p>
        </div>
      )}

      {!loading && positions.length > 0 && (
        <>
          <div className="hidden md:block overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 transition-colors">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 uppercase border-b border-gray-200 dark:border-gray-800">
                  {['Status', 'Symbol', 'Side', 'Size', 'Entry', 'Mark', 'P&L (Unrealized / Realized)', 'Platform', ''].map((h) => (
                    <th key={h} className="px-4 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                {positions.map((p) => {
                  const uPnl = parseFloat(p.unrealizedPnl ?? '0');
                  const rPnl = parseFloat(p.realizedPnl ?? '0');
                  const isOpen = p.status === 'open';
                  return (
                    <tr key={p.id} className="table-row-hover">
                      <td className="px-4 py-3">
                        <span className={`px-2 py-1 rounded text-[10px] font-bold ${isOpen ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-100 text-gray-600'}`}>
                          {p.status.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono font-semibold text-gray-900 dark:text-gray-200">{p.symbol}</td>
                      <td className="px-4 py-3"><SideBadge side={p.side} /></td>
                      <td className="px-4 py-3 font-mono text-gray-600 dark:text-gray-300">{formatSize(p.symbol, p.size)}</td>
                      <td className="px-4 py-3 font-mono text-gray-400 dark:text-gray-500">{formatPrice(p.symbol, p.entryPx)}</td>
                      <td className="px-4 py-3 font-mono text-gray-600 dark:text-gray-300">{formatPrice(p.symbol, p.markPx)}</td>
                      <td className="px-4 py-3 font-mono">
                        {isOpen ? (
                          <div className="flex items-center gap-1 font-semibold">
                            <span className={uPnl >= 0 ? 'text-emerald-600' : 'text-red-600'}>
                              {uPnl >= 0 ? '+' : ''}{uPnl.toFixed(2)}
                            </span>
                            <span className="text-gray-300 dark:text-gray-600">/</span>
                            <span className={rPnl >= 0 ? 'text-emerald-500/80' : 'text-red-500/80'}>
                              {rPnl >= 0 ? '+' : ''}{rPnl.toFixed(2)}
                            </span>
                          </div>
                        ) : (
                          <span className={`font-semibold ${rPnl >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                            {rPnl >= 0 ? '+' : ''}{rPnl.toFixed(2)}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3"><PlatformBadge platform={p.platform} /></td>
                      <td className="px-4 py-3">
                        {isOpen && (
                          <button
                            className="btn-danger text-xs py-1 shadow-sm"
                            disabled={closing === p.symbol}
                            onClick={() => closePosition(p.symbol, p.side, p.platform)}
                          >
                            {closing === p.symbol ? 'Closing…' : 'Close'}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="md:hidden space-y-2">
            {positions.map((p) => {
              const uPnl = parseFloat(p.unrealizedPnl ?? '0');
              const rPnl = parseFloat(p.realizedPnl ?? '0');
              const isOpen = p.status === 'open';
              return (
                <div key={p.id} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg p-3 shadow-sm space-y-2">
                  <div className="flex justify-between items-center">
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${isOpen ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-100 text-gray-600'}`}>
                        {p.status.toUpperCase()}
                      </span>
                      <span className="font-mono font-bold text-gray-900 dark:text-gray-200">{p.symbol}</span>
                    </div>
                    <SideBadge side={p.side} />
                  </div>
                  
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    <div className="flex justify-between"><span className="text-gray-400">Size</span> <span className="font-mono">{formatSize(p.symbol, p.size)}</span></div>
                    <div className="flex justify-between"><span className="text-gray-400">Entry</span> <span className="font-mono">{formatPrice(p.symbol, p.entryPx)}</span></div>
                    <div className="flex justify-between"><span className="text-gray-400">Mark</span> <span className="font-mono">{formatPrice(p.symbol, p.markPx)}</span></div>
                    <div className="flex justify-between"><span className="text-gray-400">Platform</span> <PlatformBadge platform={p.platform} /></div>
                  </div>
                  
                  <div className="pt-2 border-t border-gray-100 dark:border-gray-800 flex justify-between items-center">
                     <div className="font-mono text-xs font-semibold">
                      {isOpen ? (
                        <div className="flex items-center gap-1">
                          <span className={uPnl >= 0 ? 'text-emerald-600' : 'text-red-600'}>
                            {uPnl >= 0 ? '+' : ''}{uPnl.toFixed(2)}
                          </span>
                          <span className="text-gray-300">/</span>
                          <span className={rPnl >= 0 ? 'text-emerald-500/80' : 'text-red-500/80'}>
                            {rPnl >= 0 ? '+' : ''}{rPnl.toFixed(2)}
                          </span>
                        </div>
                      ) : (
                        <span className={rPnl >= 0 ? 'text-emerald-600' : 'text-red-600'}>
                          {rPnl >= 0 ? '+' : ''}{rPnl.toFixed(2)}
                        </span>
                      )}
                    </div>
                    {isOpen && (
                      <button
                        className="bg-red-50 hover:bg-red-100 dark:bg-red-900/20 text-red-600 px-3 py-1 rounded text-[10px] font-bold"
                        onClick={() => closePosition(p.symbol, p.side, p.platform)}
                      >
                        CLOSE
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
