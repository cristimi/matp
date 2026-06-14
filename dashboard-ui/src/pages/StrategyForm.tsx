import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';

const StrategyForm = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const [formData, setFormData] = useState<any>({
    id: id || '',
    name: '',
    type: 'internal',
    class: '',
    symbol: 'BTC/USDT',
    interval: '5m',
    platform: 'auto',
    default_leverage: 1,
    margin_mode: 'isolated',
    max_position_size: 1.0,
    max_leverage: 10,
    config_yaml: '',
    webhook_enabled: true,
    enabled: true
  });

  const strategyClasses: { [key: string]: string[] } = {
    internal: ['RsiStrategy', 'MaCrossoverStrategy'],
    tradingview: ['WebhookStrategy']
  };

  useEffect(() => {
    if (id) {
      setLoading(true);
      api.get<any>(`/strategies/${id}`)
        .then(data => {
          setFormData({
            ...data,
            class: data.class || ''
          });
        })
        .catch(err => setError(`Failed to load strategy: ${err.message}`))
        .finally(() => setLoading(false));
    }
  }, [id]);

  const handleTypeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setFormData({ ...formData, type: e.target.value, class: '' });
  };

  const handleSubmit = async () => {
    setSaving(true);
    setError(null);
    try {
      if (id) {
        await api.put(`/strategies/${id}`, formData);
      } else {
        await api.post('/strategies', formData);
      }
      navigate('/strategies');
    } catch (err: any) {
      setError(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Are you sure you want to delete strategy ${id}? This cannot be undone.`)) return;
    setSaving(true);
    try {
      await api.delete(`/strategies/${id}`);
      navigate('/strategies');
    } catch (err: any) {
      setError(`Delete failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="p-6">Loading...</div>;

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{id ? 'Edit Strategy' : 'Create Strategy'}</h1>
        <div className="flex gap-2">
          {id && (
            <button 
              className="text-red-600 hover:text-red-700 font-bold text-sm px-3 py-1.5 rounded-lg border border-red-200 hover:bg-red-50 transition-colors"
              onClick={handleDelete}
            >
              Delete Strategy
            </button>
          )}
          <button className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-1.5" onClick={() => navigate('/strategies')}>Cancel</button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-4 text-red-600 dark:text-red-300 text-sm">
          {error}
        </div>
      )}

      <div className="stat-card grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Strategy ID</label>
          <input 
            className="w-full bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm disabled:opacity-50" 
            disabled={!!id} 
            value={formData.id} 
            placeholder="e.g. btc-rsi-5m"
            onChange={(e) => setFormData({...formData, id: e.target.value})} 
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Name</label>
          <input 
            className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm" 
            value={formData.name} 
            placeholder="Human readable name"
            onChange={(e) => setFormData({...formData, name: e.target.value})} 
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Type</label>
          <select 
            className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm" 
            value={formData.type} 
            onChange={handleTypeChange}
          >
            <option value="internal">Internal (Python Engine)</option>
            <option value="tradingview">TradingView (External Webhook)</option>
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Class</label>
          <select 
            className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm" 
            value={formData.class} 
            onChange={(e) => setFormData({...formData, class: e.target.value})}
          >
            <option value="">Select a class...</option>
            {strategyClasses[formData.type].map((c: string) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Symbol</label>
          <input 
            className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm" 
            value={formData.symbol} 
            placeholder="BTC/USDT"
            onChange={(e) => setFormData({...formData, symbol: e.target.value})} 
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Interval</label>
          <select 
            className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm" 
            value={formData.interval} 
            onChange={(e) => setFormData({...formData, interval: e.target.value})}
          >
            {['1m', '5m', '15m', '1h', '4h', '1d'].map(i => <option key={i} value={i}>{i}</option>)}
          </select>
        </div>

        <div className="col-span-1 md:col-span-2 border-t border-gray-100 dark:border-gray-800 pt-6">
          <h3 className="text-sm font-bold text-gray-700 dark:text-gray-300 mb-4">Position Defaults</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div className="space-y-1">
              <label className="text-[10px] font-bold text-gray-400 uppercase">Default Leverage</label>
              <input
                type="number" min="1" max="125"
                className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm"
                value={formData.default_leverage ?? 1}
                onChange={(e) => setFormData({...formData, default_leverage: parseInt(e.target.value)})}
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-bold text-gray-400 uppercase">Margin Mode</label>
              <select
                className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm"
                value={formData.margin_mode ?? 'isolated'}
                onChange={(e) => setFormData({...formData, margin_mode: e.target.value})}
              >
                <option value="isolated">Isolated</option>
                <option value="cross">Cross</option>
              </select>
            </div>
          </div>
          <h3 className="text-sm font-bold text-gray-700 dark:text-gray-300 mb-4">Risk Limits</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-1">
              <label className="text-[10px] font-bold text-gray-400 uppercase">Max Pos Size</label>
              <input
                type="number" step="0.1"
                className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm"
                value={formData.max_position_size}
                onChange={(e) => setFormData({...formData, max_position_size: parseFloat(e.target.value)})}
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-bold text-gray-400 uppercase">Max Leverage</label>
              <input
                type="number"
                className="w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm"
                value={formData.max_leverage}
                onChange={(e) => setFormData({...formData, max_leverage: parseInt(e.target.value)})}
              />
            </div>
          </div>
        </div>

        <div className="col-span-1 md:col-span-2 space-y-2 pt-4">
          <div className="flex items-center justify-between">
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Config (YAML)</label>
            <span className="text-[10px] text-gray-400 italic">Strategy-specific parameters</span>
          </div>
          <textarea 
            className="w-full bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-3 text-xs font-mono h-40 focus:outline-none focus:border-indigo-500 transition-colors" 
            value={formData.config_yaml} 
            placeholder="# e.g.\nrsi_period: 14\noverbought: 70"
            onChange={(e) => setFormData({...formData, config_yaml: e.target.value})} 
          />
        </div>

        <div className="col-span-1 md:col-span-2 flex items-center gap-6 pt-4">
          <label className="flex items-center gap-2 cursor-pointer group">
            <input 
              type="checkbox" 
              className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              checked={formData.enabled} 
              onChange={(e) => setFormData({...formData, enabled: e.target.checked})} 
            />
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300 group-hover:text-gray-900 dark:group-hover:text-white transition-colors">Enabled</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer group">
            <input 
              type="checkbox" 
              className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              checked={formData.webhook_enabled} 
              onChange={(e) => setFormData({...formData, webhook_enabled: e.target.checked})} 
            />
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300 group-hover:text-gray-900 dark:group-hover:text-white transition-colors">Webhooks Active</span>
          </label>
        </div>

        <div className="col-span-1 md:col-span-2 flex justify-end gap-3 pt-6 border-t border-gray-100 dark:border-gray-800">
          <button 
            className="btn-ghost"
            onClick={() => navigate('/strategies')}
          >
            Cancel
          </button>
          <button 
            className="btn-primary px-8 shadow-lg shadow-indigo-500/20" 
            disabled={saving}
            onClick={handleSubmit}
          >
            {saving ? 'Saving...' : id ? 'Update Strategy' : 'Create Strategy'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default StrategyForm;
