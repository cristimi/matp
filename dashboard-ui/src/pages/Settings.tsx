import { useEffect, useState } from 'react';
import { api } from '../api';

interface ConfigEntry {
  value: string;
  updated_at: string;
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, ConfigEntry>>({});
  const [loading, setLoading] = useState(true);
  const [activePlatform, setActivePlatform] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<Record<string, ConfigEntry>>('/config').then((data) => {
      setConfig(data);
      setActivePlatform(data.active_platform?.value ?? 'blofin');
    }).finally(() => setLoading(false));
  }, []);

  async function savePlatform() {
    setSaving(true);
    try {
      await api.put('/config/active_platform', { platform: activePlatform });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="p-4 md:p-6 space-y-4 max-w-2xl">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white">Settings</h2>
        <div className="stat-card animate-pulse h-48 bg-white dark:bg-gray-900" />
        <div className="stat-card animate-pulse h-64 bg-white dark:bg-gray-900" />
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-2xl">
      <h2 className="text-xl font-bold text-gray-900 dark:text-white transition-colors">Settings</h2>

      {/* Active platform */}
      <section className="stat-card space-y-4 shadow-sm transition-colors">
        <h3 className="font-semibold text-gray-700 dark:text-gray-200">Active Platform</h3>
        <p className="text-xs text-gray-500">
          Incoming webhooks with <code className="text-indigo-600 dark:text-indigo-400 font-bold">platform: "auto"</code> will be routed here.
          Changes take effect within ~5 seconds.
        </p>
        <div className="flex gap-2 items-center bg-gray-50 dark:bg-gray-800 p-1.5 rounded-xl w-fit">
          {['blofin', 'hyperliquid'].map((p) => (
            <button
              key={p}
              onClick={() => setActivePlatform(p)}
              className={`px-4 py-2 rounded-lg text-sm font-bold transition-all ${
                activePlatform === p
                  ? 'bg-white dark:bg-gray-700 text-indigo-600 dark:text-indigo-400 shadow-sm'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200'
              }`}
            >
              {p.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 pt-2">
          <button
            className="btn-primary text-sm shadow-sm"
            onClick={savePlatform}
            disabled={saving}
          >
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
          {saved && <span className="text-emerald-600 dark:text-emerald-400 text-sm font-bold animate-in fade-in">✓ Saved</span>}
        </div>
        {config.active_platform && (
          <p className="text-[10px] text-gray-400 dark:text-gray-600 uppercase tracking-widest pt-2">
            Last updated: {new Date(config.active_platform.updated_at).toLocaleString()}
          </p>
        )}
      </section>

      {/* Exchange credentials info */}
      <section className="stat-card space-y-4 shadow-sm transition-colors">
        <h3 className="font-semibold text-gray-700 dark:text-gray-200">Exchange Credentials</h3>
        <p className="text-xs text-gray-500">
          Credentials are configured via environment variables in <code className="text-indigo-600 dark:text-indigo-400">.env</code> and stored
          encrypted (AES-256-GCM) in the database.
        </p>
        <div className="space-y-1">
          {[
            { key: 'blofin_api_key', label: 'Blofin API Key' },
            { key: 'blofin_api_secret', label: 'Blofin API Secret' },
            { key: 'hyperliquid_private_key', label: 'Hyperliquid Private Key' },
          ].map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between py-2.5 border-b border-gray-100 dark:border-gray-800 last:border-0">
              <span className="text-sm text-gray-600 dark:text-gray-300 font-medium">{label}</span>
              <span className="text-xs text-gray-400 dark:text-gray-600 font-mono bg-gray-50 dark:bg-gray-950 px-2 py-1 rounded">
                {config[key] ? '•••••••• SET' : 'NOT CONFIGURED'}
              </span>
            </div>
          ))}
        </div>
        <div className="bg-amber-50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 rounded-lg p-3">
          <p className="text-xs text-amber-700 dark:text-amber-500 font-medium">
            To update credentials, edit the <code className="font-bold">.env</code> file and restart the Docker containers.
          </p>
        </div>
      </section>

      {/* Webhook info */}
      <section className="stat-card space-y-4 shadow-sm transition-colors">
        <h3 className="font-semibold text-gray-700 dark:text-gray-200">Webhook Endpoint</h3>
        <div className="bg-gray-50 dark:bg-gray-950 rounded-xl p-4 font-mono text-xs text-indigo-600 dark:text-indigo-400 break-all border border-gray-100 dark:border-gray-800 relative group">
          <span className="absolute -top-2 left-3 bg-white dark:bg-gray-900 px-2 text-[10px] text-gray-400 font-sans font-bold">POST</span>
          {window.location.origin}/api/listener/webhook
        </div>
        <p className="text-xs text-gray-500">
          Include <code className="text-indigo-600 dark:text-indigo-400 font-bold">"token": "your-secret"</code> in the payload.
          Refer to the TradingView guide in <code className="text-gray-400">docs/</code> for details.
        </p>
      </section>

      {/* System info */}
      <section className="stat-card space-y-4 shadow-sm transition-colors">
        <h3 className="font-semibold text-gray-700 dark:text-gray-200">System Information</h3>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between items-center">
            <span className="text-gray-400 dark:text-gray-500 font-medium uppercase text-[10px] tracking-widest">Platform Version</span>
            <span className="text-gray-700 dark:text-gray-300 font-mono font-bold">v1.2.0</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-gray-400 dark:text-gray-500 font-medium uppercase text-[10px] tracking-widest">Dashboard API</span>
            <a href="/api/dashboard/health" target="_blank" className="text-indigo-600 dark:text-indigo-400 text-xs font-bold hover:underline">
              ONLINE ✓
            </a>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-gray-400 dark:text-gray-500 font-medium uppercase text-[10px] tracking-widest">Order Listener</span>
            <a href="/api/listener/health" target="_blank" className="text-indigo-600 dark:text-indigo-400 text-xs font-bold hover:underline">
              ONLINE ✓
            </a>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-gray-400 dark:text-gray-500 font-medium uppercase text-[10px] tracking-widest">Order Generator</span>
            <a href="/api/generator/health" target="_blank" className="text-indigo-600 dark:text-indigo-400 text-xs font-bold hover:underline">
              ONLINE ✓
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}
