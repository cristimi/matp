import { useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, Order, fetchStrategies, Strategy } from '../api';
import { StatusBadge, SideBadge, PlatformBadge, StrategyBadge } from '../components/Badges';

const STATUSES = ['', 'filled', 'received', 'routing', 'route_failed', 'rejected'];

function SourceIcon({ source }: { source?: string }) {
  if (source === 'tradingview') return <span title="TradingView">📡</span>;
  if (source === 'internal') return <span title="Internal">⚙️</span>;
  return <span title="Unknown">❓</span>;
}

export default function OrdersPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [orders, setOrders] = useState<Order[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  // Filters from URL
  const symbol = searchParams.get('symbol') || '';
  const platform = searchParams.get('platform') || '';
  const status = searchParams.get('status') || '';
  const strategy_id = searchParams.get('strategy_id') || '';

  const [expanded, setExpanded] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);

  const LIMIT = 50;
  const page = parseInt(searchParams.get('page') || '1');

  const loadStrategies = async () => {
    const data = await fetchStrategies();
    setStrategies(data);
  };

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ page: String(page), limit: String(LIMIT) });
    if (symbol) params.set('symbol', symbol);
    if (platform) params.set('platform', platform);
    if (status) params.set('status', status);
    if (strategy_id) params.set('strategy_id', strategy_id);

    try {
      const res = await api.get<{ total: number; items: Order[] }>(`/orders?${params}`);
      setOrders(res.items);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, [page, symbol, platform, status, strategy_id]);

  useEffect(() => { loadStrategies(); }, []);
  useEffect(() => { load(); }, [load]);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set('page', '1');
    setSearchParams(next);
  };

  async function retry(orderId: string) {
    setRetrying(orderId);
    try {
      await api.post(`/orders/${orderId}/retry`);
      await load();
    } finally {
      setRetrying(null);
    }
  }

  const totalPages = Math.ceil(total / LIMIT);

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white">Orders</h2>
        <span className="text-xs text-gray-500">{total} total</span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <input
          className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-900 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-600 focus:outline-none focus:border-indigo-500 w-36 transition-colors"
          placeholder="Symbol…"
          value={symbol}
          onChange={(e) => setFilter('symbol', e.target.value.toUpperCase())}
        />
        <select
          className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-900 dark:text-gray-200 focus:outline-none focus:border-indigo-500 transition-colors"
          value={strategy_id}
          onChange={(e) => setFilter('strategy_id', e.target.value)}
        >
          <option value="">All strategies</option>
          {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select
          className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-900 dark:text-gray-200 focus:outline-none focus:border-indigo-500 transition-colors"
          value={platform}
          onChange={(e) => setFilter('platform', e.target.value)}
        >
          <option value="">All platforms</option>
          <option value="blofin">Blofin</option>
          <option value="hyperliquid">Hyperliquid</option>
        </select>
        <select
          className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-900 dark:text-gray-200 focus:outline-none focus:border-indigo-500 transition-colors"
          value={status}
          onChange={(e) => setFilter('status', e.target.value)}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s || 'All statuses'}</option>
          ))}
        </select>
        <button className="btn-ghost text-sm" onClick={() => setSearchParams({})}>
          Clear
        </button>
      </div>

      {/* Table — desktop */}
      {loading ? (
        <div className="space-y-2">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="h-10 bg-white dark:bg-gray-800 rounded animate-pulse border border-gray-100 dark:border-gray-700" />
          ))}
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 transition-colors">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 uppercase border-b border-gray-200 dark:border-gray-800">
                  {['Time', 'Origin', 'Symbol', 'Side', 'Signal', 'Size', 'Strategy', 'Ind. Price', 'Platform', 'Status', 'P&L', ''].map((h) => (
                    <th key={h} className="px-4 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                {orders.map((o) => (
                  <>
                    <tr
                      key={o.id}
                      className="table-row-hover cursor-pointer"
                      onClick={() => setExpanded(expanded === o.id ? null : o.id)}
                    >
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400 font-mono text-xs">
                        {new Date(o.received_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <SourceIcon source={o.signal_source} />
                      </td>
                      <td className="px-4 py-3 font-mono font-semibold text-gray-900 dark:text-gray-200">{o.symbol}</td>
                      <td className="px-4 py-3"><SideBadge side={o.side} /></td>
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400 text-xs">{o.signal}</td>
                      <td className="px-4 py-3 font-mono text-gray-600 dark:text-gray-300">{o.size}</td>
                      <td className="px-4 py-3 text-xs text-gray-500 dark:text-gray-400 font-medium">{o.strategy_id || '—'}</td>
                      <td className="px-4 py-3 font-mono text-gray-500 dark:text-gray-400 text-xs">
                        {o.indicator_price ? `$${parseFloat(o.indicator_price).toLocaleString()}` : '—'}
                      </td>
                      <td className="px-4 py-3"><PlatformBadge platform={o.platform} /></td>
                      <td className="px-4 py-3"><StatusBadge status={o.status} /></td>
                      <td className="px-4 py-3 font-mono">
                        {o.pnl != null ? (
                          <span className={parseFloat(o.pnl) >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
                            ${parseFloat(o.pnl).toFixed(2)}
                          </span>
                        ) : '—'}
                      </td>
                      <td className="px-4 py-3">
                        {(o.status === 'route_failed' || o.status === 'rejected') && (
                          <button
                            className="btn-primary text-xs py-1"
                            disabled={retrying === o.id}
                            onClick={(e) => { e.stopPropagation(); retry(o.id); }}
                          >
                            {retrying === o.id ? '…' : 'Retry'}
                          </button>
                        )}
                      </td>
                    </tr>
                    {expanded === o.id && (
                      <tr key={`${o.id}-expand`}>
                        <td colSpan={11} className="px-4 py-3 bg-gray-50 dark:bg-gray-900/50">
                          <div className="text-xs text-gray-500 dark:text-gray-400 space-y-1">
                            <p><span className="text-gray-400 dark:text-gray-600">ID:</span> {o.id}</p>
                            {o.exchange_order_id && <p><span className="text-gray-400 dark:text-gray-600">Exchange ID:</span> {o.exchange_order_id}</p>}
                            {o.strategy_id && <p><span className="text-gray-400 dark:text-gray-600">Strategy:</span> {o.strategy_id}</p>}
                            {o.signal_source && <p><span className="text-gray-400 dark:text-gray-600">Signal Source:</span> {o.signal_source}</p>}
                            {o.indicator_price && <p><span className="text-gray-400 dark:text-gray-600">Indicator Price:</span> ${o.indicator_price}</p>}
                            {o.error_msg && <p className="text-red-600 dark:text-red-400"><span className="text-gray-400 dark:text-gray-600">Error:</span> {o.error_msg}</p>}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-2">
            {orders.map((o) => (
              <div key={o.id} className="stat-card space-y-2 text-sm shadow-sm transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <SourceIcon source={o.signal_source} />
                    <span className="font-mono font-bold text-gray-900 dark:text-gray-200">{o.symbol}</span>
                  </div>
                  <StatusBadge status={o.status} />
                </div>
                <div className="flex gap-2 items-center flex-wrap">
                  <SideBadge side={o.side} />
                  <PlatformBadge platform={o.platform} />
                  <span className="text-gray-500 dark:text-gray-400 text-xs">{o.signal}</span>
                  {o.indicator_price && (
                    <span className="text-gray-400 dark:text-gray-500 text-xs font-mono ml-auto">
                      ${parseFloat(o.indicator_price).toLocaleString()}
                    </span>
                  )}
                </div>
                <div className="flex justify-between items-center text-xs text-gray-500 dark:text-gray-400">
                  <span>{new Date(o.received_at).toLocaleString()}</span>
                  {o.pnl != null && (
                    <span className={parseFloat(o.pnl) >= 0 ? 'text-emerald-600 dark:text-emerald-400 font-mono font-bold' : 'text-red-600 dark:text-red-400 font-mono font-bold'}>
                      ${parseFloat(o.pnl).toFixed(2)}
                    </span>
                  )}
                </div>
                {(o.status === 'route_failed' || o.status === 'rejected') && (
                  <button
                    className="btn-primary text-xs py-1 w-full"
                    disabled={retrying === o.id}
                    onClick={() => retry(o.id)}
                  >
                    {retrying === o.id ? 'Retrying…' : 'Retry Order'}
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-2">
              <button 
                className="btn-ghost text-sm border border-gray-200 dark:border-gray-800" 
                disabled={page === 1} 
                onClick={() => setFilter('page', String(page - 1))}
              >
                ← Prev
              </button>
              <span className="text-sm text-gray-500 dark:text-gray-400">{page} / {totalPages}</span>
              <button 
                className="btn-ghost text-sm border border-gray-200 dark:border-gray-800" 
                disabled={page >= totalPages} 
                onClick={() => setFilter('page', String(page + 1))}
              >
                Next →
              </button>
            </div>
          )}

          {orders.length === 0 && (
            <p className="text-center text-gray-500 py-12">No orders found</p>
          )}
        </>
      )}
    </div>
  );
}
