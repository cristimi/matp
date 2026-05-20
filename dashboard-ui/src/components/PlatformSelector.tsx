import { useState } from 'react';
import { api } from '../api';

interface Props {
  current: string;
  onChange?: (platform: string) => void;
}

const PLATFORMS = ['blofin', 'hyperliquid'];

export function PlatformSelector({ current, onChange }: Props) {
  const [selected, setSelected] = useState(current);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function apply(platform: string) {
    setSelected(platform);
    setSaving(true);
    try {
      await api.put('/config/active_platform', { platform });
      onChange?.(platform);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 font-medium uppercase tracking-tight">Active:</span>
      <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 p-1 rounded-lg">
        {PLATFORMS.map((p) => (
          <button
            key={p}
            onClick={() => apply(p)}
            disabled={saving}
            className={`px-3 py-1 rounded-md text-xs font-bold transition-all ${
              selected === p
                ? 'bg-white dark:bg-gray-700 text-indigo-600 dark:text-indigo-400 shadow-sm'
                : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200'
            }`}
          >
            {p.toUpperCase()}
          </button>
        ))}
      </div>
      {saved && <span className="text-xs text-emerald-600 dark:text-emerald-400 font-bold ml-1">✓</span>}
    </div>
  );
}
