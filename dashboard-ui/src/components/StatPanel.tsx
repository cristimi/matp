interface StatPanelProps {
  label: string;
  value: string | number;
  sub?: string;
  color?: 'green' | 'red' | 'blue' | 'default';
}

export function StatPanel({ label, value, sub, color = 'default' }: StatPanelProps) {
  const colorMap = {
    green:   'text-emerald-400',
    red:     'text-red-400',
    blue:    'text-indigo-400',
    default: 'text-white',
  };
  return (
    <div className="stat-card">
      <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-2xl font-bold font-mono ${colorMap[color]}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}
