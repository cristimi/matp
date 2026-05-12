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

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-2xl">
      <h2 className="text-xl font-bold text-white">Settings</h2>

      {/* Active platform */}
      <section className="stat-card space-y-4">
        <h3 className="font-semibold text-gray-200">Active Platform</h3>
        <p className="text-xs text-gray-500">
          Incoming webhooks with <code className="text-gray-400">platform: "auto"</code> will be routed here.
          Changes take effect within ~5 seconds.
        </p>
        <div className="flex gap-3 items-center">
          {['blofin', 'hyperliquid'].map((p) => (
            <button
              key={p}
              onClick={() => setActivePlatform(p)}
              className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
                activePlatform === p
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              {p}
            </button>
          ))}
          <button
            className="btn-primary text-sm ml-2"
            onClick={savePlatform}
            disabled={saving}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          {saved && <span className="text-emerald-400 text-sm">✓ Saved</span>}
        </div>
        {config.active_platform && (
          <p className="text-xs text-gray-600">
            Last updated: {new Date(config.active_platform.updated_at).toLocaleString()}
          </p>
        )}
      </section>

      {/* Exchange credentials info */}
      <section className="stat-card space-y-3">
        <h3 className="font-semibold text-gray-200">Exchange Credentials</h3>
        <p className="text-xs text-gray-500">
          Credentials are configured via environment variables in <code className="text-gray-400">.env</code> and stored
          encrypted (AES-256-GCM) in the database. They are never returned in plaintext via the API.
        </p>
        <div className="space-y-2">
          {[
            { key: 'blofin_api_key', label: 'Blofin API Key' },
            { key: 'blofin_api_secret', label: 'Blofin API Secret' },
            { key: 'hyperliquid_private_key', label: 'Hyperliquid Private Key' },
          ].map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between py-2 border-b border-gray-800">
              <span className="text-sm text-gray-300">{label}</span>
              <span className="text-xs text-gray-600 font-mono">
                {config[key] ? '●●●●●●●● (set)' : 'not set'}
              </span>
            </div>
          ))}
        </div>
        <p className="text-xs text-gray-600">
          To update credentials, edit <code className="text-gray-500">.env</code> and restart the containers.
        </p>
      </section>

      {/* Webhook info */}
      <section className="stat-card space-y-3">
        <h3 className="font-semibold text-gray-200">Webhook Endpoint</h3>
        <div className="bg-gray-950 rounded-lg p-3 font-mono text-xs text-indigo-300 break-all">
          POST {window.location.origin}/api/listener/webhook
        </div>
        <p className="text-xs text-gray-500">
          Include <code className="text-gray-400">"token": "your-WEBHOOK_SECRET"</code> in every payload.
          See <code className="text-gray-400">README.md</code> for the full TradingView alert format.
        </p>
      </section>

      {/* Docker info */}
      <section className="stat-card space-y-3">
        <h3 className="font-semibold text-gray-200">System</h3>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-500">Version</span>
            <span className="text-gray-300 font-mono">MATP v1.0.0</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Dashboard API</span>
            <a href="/api/dashboard/health" target="_blank" className="text-indigo-400 text-xs hover:underline">
              /api/dashboard/health
            </a>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Order Listener</span>
            <a href="/api/listener/health" target="_blank" className="text-indigo-400 text-xs hover:underline">
              /api/listener/health
            </a>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Order Generator</span>
            <a href="/api/generator/health" target="_blank" className="text-indigo-400 text-xs hover:underline">
              /api/generator/health
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}
