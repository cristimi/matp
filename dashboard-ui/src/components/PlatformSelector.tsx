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
      <span className="text-xs text-gray-500">Active platform:</span>
      <div className="flex gap-1">
        {PLATFORMS.map((p) => (
          <button
            key={p}
            onClick={() => apply(p)}
            disabled={saving}
            className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${
              selected === p
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            {p}
          </button>
        ))}
      </div>
      {saved && <span className="text-xs text-emerald-400">✓ Saved</span>}
    </div>
  );
}
