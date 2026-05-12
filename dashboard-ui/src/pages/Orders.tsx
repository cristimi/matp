import { useEffect, useState, useCallback } from 'react';
import { api, Order } from '../api';
import { StatusBadge, SideBadge, PlatformBadge } from '../components/Badges';

const STATUSES = ['', 'filled', 'received', 'routing', 'route_failed', 'rejected'];

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  // Filters
  const [symbol, setSymbol] = useState('');
  const [platform, setPlatform] = useState('');
  const [status, setStatus] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);

  const LIMIT = 50;

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ page: String(page), limit: String(LIMIT) });
    if (symbol) params.set('symbol', symbol);
    if (platform) params.set('platform', platform);
    if (status) params.set('status', status);
    try {
      const res = await api.get<{ total: number; items: Order[] }>(`/orders?${params}`);
      setOrders(res.items);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, [page, symbol, platform, status]);

  useEffect(() => { load(); }, [load]);

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
        <h2 className="text-xl font-bold text-white">Orders</h2>
        <span className="text-xs text-gray-500">{total} total</span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <input
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-indigo-500 w-36"
          placeholder="Symbol…"
          value={symbol}
          onChange={(e) => { setSymbol(e.target.value.toUpperCase()); setPage(1); }}
        />
        <select
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
          value={platform}
          onChange={(e) => { setPlatform(e.target.value); setPage(1); }}
        >
          <option value="">All platforms</option>
          <option value="blofin">Blofin</option>
          <option value="hyperliquid">Hyperliquid</option>
        </select>
        <select
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
          value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1); }}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s || 'All statuses'}</option>
          ))}
        </select>
        <button className="btn-ghost text-sm" onClick={() => { setSymbol(''); setPlatform(''); setStatus(''); setPage(1); }}>
          Clear
        </button>
      </div>

      {/* Table — desktop */}
      {loading ? (
        <div className="space-y-2">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="h-10 bg-gray-800 rounded animate-pulse" />
          ))}
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto rounded-xl border border-gray-800">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 uppercase border-b border-gray-800">
                  {['Time', 'Symbol', 'Side', 'Signal', 'Size', 'Platform', 'Status', 'P&L', ''].map((h) => (
                    <th key={h} className="px-4 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <>
                    <tr
                      key={o.id}
                      className="table-row-hover cursor-pointer"
                      onClick={() => setExpanded(expanded === o.id ? null : o.id)}
                    >
                      <td className="px-4 py-3 text-gray-400 font-mono text-xs">
                        {new Date(o.received_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 font-mono font-semibold text-gray-200">{o.symbol}</td>
                      <td className="px-4 py-3"><SideBadge side={o.side} /></td>
                      <td className="px-4 py-3 text-gray-400 text-xs">{o.signal}</td>
                      <td className="px-4 py-3 font-mono text-gray-300">{o.size}</td>
                      <td className="px-4 py-3"><PlatformBadge platform={o.platform} /></td>
                      <td className="px-4 py-3"><StatusBadge status={o.status} /></td>
                      <td className="px-4 py-3 font-mono">
                        {o.pnl != null ? (
                          <span className={parseFloat(o.pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'}>
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
                        <td colSpan={9} className="px-4 py-3 bg-gray-900">
                          <div className="text-xs text-gray-400 space-y-1">
                            <p><span className="text-gray-600">ID:</span> {o.id}</p>
                            {o.exchange_order_id && <p><span className="text-gray-600">Exchange ID:</span> {o.exchange_order_id}</p>}
                            {o.strategy_id && <p><span className="text-gray-600">Strategy:</span> {o.strategy_id}</p>}
                            {o.error_msg && <p className="text-red-400"><span className="text-gray-600">Error:</span> {o.error_msg}</p>}
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
              <div key={o.id} className="stat-card space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-mono font-bold text-gray-200">{o.symbol}</span>
                  <StatusBadge status={o.status} />
                </div>
                <div className="flex gap-2">
                  <SideBadge side={o.side} />
                  <PlatformBadge platform={o.platform} />
                  <span className="text-gray-500 text-xs">{o.signal}</span>
                </div>
                <div className="flex justify-between items-center text-xs text-gray-500">
                  <span>{new Date(o.received_at).toLocaleString()}</span>
                  {o.pnl != null && (
                    <span className={parseFloat(o.pnl) >= 0 ? 'text-emerald-400 font-mono' : 'text-red-400 font-mono'}>
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
              <button className="btn-ghost text-sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
              <span className="text-sm text-gray-400">{page} / {totalPages}</span>
              <button className="btn-ghost text-sm" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next →</button>
            </div>
          )}

          {orders.length === 0 && (
            <p className="text-center text-gray-600 py-12">No orders found</p>
          )}
        </>
      )}
    </div>
  );
}
