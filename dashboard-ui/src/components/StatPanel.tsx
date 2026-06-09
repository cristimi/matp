import { ReactNode } from 'react';

interface StatPanelProps {
  label: string;
  value: string | number;
  sub?: ReactNode;
  color?: 'green' | 'red' | 'blue' | 'default';
}

export function StatPanel({ label, value, sub, color = 'default' }: StatPanelProps) {
  const colorMap = {
    green:   'text-emerald-600 dark:text-emerald-400',
    red:     'text-red-600 dark:text-red-400',
    blue:    'text-indigo-600 dark:text-indigo-400',
    default: 'text-gray-900 dark:text-white',
  };
  return (
    <div className="stat-card shadow-sm">
      <p className="text-xs text-gray-500 uppercase tracking-wider mb-1 font-medium">{label}</p>
      <p className={`text-2xl font-bold font-mono ${colorMap[color]}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}
