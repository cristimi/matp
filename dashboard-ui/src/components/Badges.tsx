export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    filled:       'badge-green',
    received:     'badge-blue',
    routing:      'badge-blue',
    submitted:    'badge-yellow',
    route_failed: 'badge-red',
    rejected:     'badge-red',
    pending:      'badge-yellow',
  };
  return (
    <span className={map[status] ?? 'badge-gray'}>
      {status.replace('_', ' ')}
    </span>
  );
}

export function SideBadge({ side }: { side: string }) {
  return (
    <span className={side === 'buy' ? 'badge-green' : 'badge-red'}>
      {side.toUpperCase()}
    </span>
  );
}

export function PlatformBadge({ platform }: { platform: string }) {
  const colors: Record<string, string> = {
    blofin:      'badge-blue',
    hyperliquid: 'badge-yellow',
    auto:        'badge-gray',
  };
  return <span className={colors[platform] ?? 'badge-gray'}>{platform}</span>;
}

export function StrategyBadge({ strategyId }: { strategyId: string }) {
  return (
    <a
      href={`/strategy/${strategyId}`}
      className="inline-flex items-center px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 font-mono text-[10px] hover:bg-indigo-100 dark:hover:bg-indigo-900/30 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
    >
      {strategyId}
    </a>
  );
}
