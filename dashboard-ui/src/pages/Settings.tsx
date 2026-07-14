import { useEffect, useState } from 'react';
import { api } from '../api';

function urlBase64ToUint8Array(base64Url: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const array = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) array[i] = raw.charCodeAt(i);
  return array;
}

function NotificationsSection() {
  const [supported] = useState(() => 'serviceWorker' in navigator && 'PushManager' in window);
  const [permission, setPermission] = useState<NotificationPermission>(
    supported ? Notification.permission : 'denied'
  );
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<'idle' | 'enabled' | 'error'>('idle');
  const [error, setError] = useState('');

  async function enableNotifications() {
    setBusy(true);
    setError('');
    try {
      const perm = await Notification.requestPermission();
      setPermission(perm);
      if (perm !== 'granted') {
        throw new Error('Notification permission was not granted');
      }

      const registration = await navigator.serviceWorker.ready;
      const keyRes = await fetch('/api/notifications/vapid-public-key');
      if (!keyRes.ok) throw new Error('Failed to fetch VAPID public key');
      const { public_key } = await keyRes.json();

      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(public_key),
      });

      const json = subscription.toJSON();
      const res = await fetch('/api/notifications/subscriptions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: json.endpoint,
          keys: json.keys,
          user_agent: navigator.userAgent,
        }),
      });
      if (!res.ok) throw new Error('Failed to register subscription');

      setStatus('enabled');
    } catch (e: any) {
      setStatus('error');
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!supported) {
    return (
      <section className="stat-card space-y-2 shadow-sm transition-colors">
        <h3 className="font-semibold text-gray-700 dark:text-gray-200">Push Notifications</h3>
        <p className="text-xs text-gray-500">This browser does not support push notifications.</p>
      </section>
    );
  }

  return (
    <section className="stat-card space-y-4 shadow-sm transition-colors">
      <h3 className="font-semibold text-gray-700 dark:text-gray-200">Push Notifications</h3>
      <p className="text-xs text-gray-500">
        Get notified on this device when a position opens or closes, or when the exchange feed
        or a critical service goes down.
      </p>
      <button
        className="btn-primary text-sm shadow-sm"
        onClick={enableNotifications}
        disabled={busy || permission === 'denied'}
      >
        {busy ? 'Enabling…' : status === 'enabled' ? 'Re-enable notifications' : 'Enable notifications'}
      </button>
      {status === 'enabled' && (
        <span className="ml-3 text-emerald-600 dark:text-emerald-400 text-sm font-bold">✓ Enabled</span>
      )}
      {permission === 'denied' && (
        <p className="text-xs text-amber-600 dark:text-amber-500">
          Notifications are blocked for this site in your browser settings.
        </p>
      )}
      {status === 'error' && <p className="text-xs text-red-500">{error}</p>}
    </section>
  );
}

// ── LLM Provider Keys ─────────────────────────────────────────────────────────

const LLM_PROVIDERS: { id: string; label: string }[] = [
  { id: 'anthropic', label: 'Anthropic (Claude)' },
  { id: 'openai',    label: 'OpenAI' },
  { id: 'gemini',    label: 'Google (Gemini)' },
  { id: 'groq',      label: 'Groq' },
  { id: 'cerebras',  label: 'Cerebras' },
  { id: 'zhipu',     label: 'Zhipu (GLM)' },
];

interface LlmKeyRow {
  id: number;
  provider: string;
  label: string;
  enabled: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
}

interface LlmKeyRuntime {
  id: number | null;
  label: string;
  state: 'active' | 'cooldown' | 'auth_failed';
  cooldown_remaining_s: number | null;
  dead_reason: string | null;
}

function RuntimeBadge({ rt }: { rt: LlmKeyRuntime | undefined }) {
  if (!rt || rt.state === 'active') {
    return <span className="text-[10px] font-mono text-emerald-600 dark:text-emerald-400">ACTIVE</span>;
  }
  if (rt.state === 'cooldown') {
    return (
      <span className="text-[10px] font-mono text-amber-600 dark:text-amber-500">
        COOLDOWN{rt.cooldown_remaining_s ? ` ${rt.cooldown_remaining_s}s` : ''}
      </span>
    );
  }
  return (
    <span className="text-[10px] font-mono text-red-500" title={rt.dead_reason ?? undefined}>
      AUTH FAILED
    </span>
  );
}

function LlmKeysSection() {
  const [keys, setKeys]       = useState<Record<string, LlmKeyRow[]>>({});
  const [runtime, setRuntime] = useState<Record<string, LlmKeyRuntime[]>>({});
  const [loading, setLoading] = useState(true);
  const [adding, setAdding]   = useState<string | null>(null);   // provider id
  const [keyInput, setKeyInput]     = useState('');
  const [labelInput, setLabelInput] = useState('');
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const load = () => {
    api.get<Record<string, LlmKeyRow[]>>('/config/llm-keys')
      .then(setKeys)
      .finally(() => setLoading(false));
    api.get<{ providers: Record<string, LlmKeyRuntime[]> }>('/config/llm-keys/status')
      .then(r => setRuntime(r.providers || {}))
      .catch(() => setRuntime({}));
  };

  useEffect(() => { load(); }, []);

  const startAdd = (provider: string) => {
    setAdding(provider);
    setKeyInput('');
    setLabelInput('');
    setError(null);
  };

  const addKey = async (provider: string) => {
    if (!keyInput.trim()) { setError('API key is required'); return; }
    setSaving(true);
    setError(null);
    try {
      await api.post('/config/llm-keys', {
        provider,
        label: labelInput.trim() || `key ${(keys[provider]?.length ?? 0) + 1}`,
        api_key: keyInput.trim(),
      });
      setAdding(null);
      load();
    } catch (e: any) {
      setError(e.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const toggleKey = async (row: LlmKeyRow) => {
    await api.patch(`/config/llm-keys/${row.id}`, { enabled: !row.enabled });
    load();
  };

  const deleteKey = async (row: LlmKeyRow) => {
    if (!window.confirm(`Delete key "${row.label}" for ${row.provider}?`)) return;
    await api.delete(`/config/llm-keys/${row.id}`);
    load();
  };

  return (
    <section className="stat-card space-y-4 shadow-sm transition-colors">
      <h3 className="font-semibold text-gray-700 dark:text-gray-200">LLM Provider Keys</h3>
      <p className="text-xs text-gray-500">
        Each provider can hold several keys. The AI Signal Generator uses them in the order
        listed and rotates to the next key when one hits a rate limit — changes apply
        immediately there. Strategy Tester and Social Listener pick up the first key on
        their next restart. Keys are encrypted at rest and never shown again.
      </p>
      {loading ? (
        <p className="text-xs text-gray-400">Loading…</p>
      ) : (
        <div className="space-y-1">
          {LLM_PROVIDERS.map(({ id, label }) => {
            const rows = keys[id] ?? [];
            const rts  = runtime[id] ?? [];
            const isAdding = adding === id;
            return (
              <div key={id} className="py-2.5 border-b border-gray-100 dark:border-gray-800 last:border-0">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-600 dark:text-gray-300 font-medium">{label}</span>
                  <div className="flex items-center gap-3">
                    {rows.length === 0 && (
                      <span className="text-xs text-gray-400 dark:text-gray-600 font-mono bg-gray-50 dark:bg-gray-950 px-2 py-1 rounded">
                        NOT CONFIGURED
                      </span>
                    )}
                    <button
                      className="text-xs text-indigo-600 dark:text-indigo-400 font-bold hover:underline"
                      onClick={() => startAdd(id)}
                    >
                      + Add key
                    </button>
                  </div>
                </div>
                {rows.length > 0 && (
                  <div className="mt-1.5 space-y-1">
                    {rows.map(row => (
                      <div key={row.id} className="flex items-center justify-between pl-3 py-1 rounded bg-gray-50 dark:bg-gray-950">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`text-xs font-mono truncate ${row.enabled ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 dark:text-gray-600 line-through'}`}>
                            {row.label}
                          </span>
                          {row.enabled && <RuntimeBadge rt={rts.find(r => r.id === row.id)} />}
                        </div>
                        <div className="flex items-center gap-2 shrink-0 pr-2">
                          <span className="text-[10px] text-gray-400 dark:text-gray-600 font-mono">
                            {new Date(row.updated_at).toLocaleDateString()}
                          </span>
                          <button
                            className="text-[10px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 font-bold uppercase"
                            onClick={() => toggleKey(row)}
                          >
                            {row.enabled ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            className="text-[10px] text-red-400 hover:text-red-600 font-bold uppercase"
                            onClick={() => deleteKey(row)}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {isAdding && (
                  <div className="mt-2 space-y-2">
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={labelInput}
                        onChange={e => setLabelInput(e.target.value)}
                        placeholder="Label (e.g. personal, work)"
                        className="w-40 text-xs font-mono px-2 py-1.5 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200"
                      />
                      <input
                        type="password"
                        autoFocus
                        value={keyInput}
                        onChange={e => setKeyInput(e.target.value)}
                        placeholder="Paste API key"
                        className="flex-1 text-xs font-mono px-2 py-1.5 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200"
                      />
                      <button className="btn-primary text-xs px-3 py-1.5" disabled={saving} onClick={() => addKey(id)}>
                        {saving ? 'Saving…' : 'Save'}
                      </button>
                      <button className="text-xs text-gray-400 hover:text-gray-600" onClick={() => setAdding(null)}>Cancel</button>
                    </div>
                    {error && <p className="text-xs text-red-500">{error}</p>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ── System Information ────────────────────────────────────────────────────────

interface HealthEntry { name: string; ok: boolean; detail?: string; }
interface HealthGrid   { http: HealthEntry[]; workers: string[]; }

function SystemInfoSection() {
  const [grid, setGrid]       = useState<HealthGrid | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = () => {
      api.get<HealthGrid>('/system/health-grid')
        .then(setGrid)
        .catch(() => setGrid(null))
        .finally(() => setLoading(false));
    };
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <section className="stat-card space-y-4 shadow-sm transition-colors">
      <h3 className="font-semibold text-gray-700 dark:text-gray-200">System Information</h3>
      {loading && <p className="text-xs text-gray-400">Checking services…</p>}
      {!loading && grid && (
        <>
          <div className="space-y-3 text-sm">
            {grid.http.map(s => (
              <div key={s.name} className="flex justify-between items-center">
                <span className="text-gray-400 dark:text-gray-500 font-medium uppercase text-[10px] tracking-widest">
                  {s.name}
                </span>
                <span className={`text-xs font-bold ${s.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>
                  {s.ok ? 'ONLINE ✓' : 'OFFLINE ✕'}
                </span>
              </div>
            ))}
          </div>
          <div className="pt-3 border-t border-gray-100 dark:border-gray-800">
            <p className="text-[10px] text-gray-400 dark:text-gray-600 uppercase tracking-widest mb-2">
              Background workers — no HTTP health check, verify with <code>docker compose ps</code>
            </p>
            <div className="flex flex-wrap gap-2">
              {grid.workers.map(w => (
                <span
                  key={w}
                  className="text-xs font-mono px-2 py-1 rounded bg-gray-50 dark:bg-gray-950 text-gray-500 dark:text-gray-400 border border-gray-100 dark:border-gray-800"
                >
                  {w}
                </span>
              ))}
            </div>
          </div>
        </>
      )}
      {!loading && !grid && <p className="text-xs text-red-500">Failed to load service status.</p>}
    </section>
  );
}

export default function SettingsPage() {
  return (
    <div className="p-4 md:p-6 space-y-6 max-w-2xl">
      <h2 className="text-xl font-bold text-gray-900 dark:text-white transition-colors">Settings</h2>

      {/* Webhook info */}
      <section className="stat-card space-y-4 shadow-sm transition-colors">
        <h3 className="font-semibold text-gray-700 dark:text-gray-200">Webhook Endpoint (TradingView strategies)</h3>
        <div className="bg-gray-50 dark:bg-gray-950 rounded-xl p-4 font-mono text-xs text-indigo-600 dark:text-indigo-400 break-all border border-gray-100 dark:border-gray-800 relative group">
          <span className="absolute -top-2 left-3 bg-white dark:bg-gray-900 px-2 text-[10px] text-gray-400 font-sans font-bold">POST</span>
          {window.location.origin}/api/listener/webhook
        </div>
        <p className="text-xs text-gray-500">
          Only used by TradingView-sourced strategies. Include{' '}
          <code className="text-indigo-600 dark:text-indigo-400 font-bold">"token": "your-secret"</code>{' '}
          in the payload — each strategy gets its own secret when created (see the Strategies
          add-strategy flow). AI, Social, and Internal strategies don't use webhooks.
        </p>
      </section>

      <LlmKeysSection />

      <NotificationsSection />

      <SystemInfoSection />
    </div>
  );
}
