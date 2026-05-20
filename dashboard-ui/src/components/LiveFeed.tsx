import { useOrderStream, OrderEvent } from '../hooks/useOrderStream';
import { StatusBadge } from './Badges';

function EventRow({ evt }: { evt: OrderEvent }) {
  const time = new Date(evt.timestamp).toLocaleTimeString();
  return (
    <div className="flex items-center gap-3 py-2 border-b border-gray-100 dark:border-gray-800/50 text-sm">
      <span className="text-gray-400 dark:text-gray-500 font-mono text-xs w-20 shrink-0">{time}</span>
      <StatusBadge status={evt.status ?? evt.event.split(':')[1] ?? evt.event} />
      <span className="text-gray-700 dark:text-gray-300 font-mono">{evt.symbol ?? '—'}</span>
      <span className="text-gray-400 dark:text-gray-500 text-xs ml-auto uppercase">{evt.platform ?? ''}</span>
    </div>
  );
}

export function LiveFeed() {
  const { connected, events } = useOrderStream();

  return (
    <div className="stat-card shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-600 dark:text-gray-300">Live Feed</h3>
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-500 animate-pulse' : 'bg-gray-400 dark:bg-gray-600'}`} />
          <span className="text-xs text-gray-400 dark:text-gray-500 font-medium">{connected ? 'Connected' : 'Reconnecting…'}</span>
        </div>
      </div>
      <div className="max-h-64 overflow-y-auto">
        {events.length === 0 ? (
          <p className="text-gray-400 text-sm py-8 text-center italic">Waiting for orders…</p>
        ) : (
          events.map((evt, i) => <EventRow key={i} evt={evt} />)
        )}
      </div>
    </div>
  );
}
