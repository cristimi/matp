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
