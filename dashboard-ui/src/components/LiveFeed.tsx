import { useRef, useEffect, useState } from 'react';
import { useOrderStream, OrderEvent } from '../hooks/useOrderStream';
import { StatusBadge } from './Badges';

function SidePill({ side }: { side?: string }) {
  if (!side) return null;
  const buy = side === 'buy';
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-bold uppercase ${
      buy
        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400'
        : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400'
    }`}>
      {buy ? 'L' : 'S'}
    </span>
  );
}

function EventRow({ evt }: { evt: OrderEvent }) {
  const time  = new Date(evt.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const price = evt.actual_fill_price ? `@ $${Number(evt.actual_fill_price).toFixed(2)}` : null;
  const src   = evt.signal_source && evt.signal_source !== 'tradingview' ? evt.signal_source : null;

  return (
    <div className="py-2 border-b border-gray-100 dark:border-gray-800/50 text-sm last:border-0">
      <div className="flex items-center gap-2">
        <span className="text-gray-400 dark:text-gray-500 font-mono text-xs w-18 shrink-0">{time}</span>
        <SidePill side={evt.side} />
        <StatusBadge status={evt.status ?? evt.event.split(':')[1] ?? evt.event} />
        <span className="text-gray-800 dark:text-gray-200 font-mono font-medium">{evt.symbol ?? '—'}</span>
        {evt.size && (
          <span className="text-gray-600 dark:text-gray-400 font-mono text-xs">{evt.size}</span>
        )}
        {price && (
          <span className="text-gray-500 dark:text-gray-400 font-mono text-xs">{price}</span>
        )}
      </div>
      {(evt.account_label || src || evt.signal) && (
        <div className="flex items-center gap-2 mt-0.5 pl-[4.5rem]">
          {evt.account_label && (
            <span className="text-gray-400 dark:text-gray-500 text-xs">{evt.account_label}</span>
          )}
          {src && (
            <span className="text-indigo-400 dark:text-indigo-500 text-xs uppercase">{src}</span>
          )}
          {evt.signal && (
            <span className="text-gray-400 dark:text-gray-500 text-xs italic">{evt.signal.replace('_', ' ')}</span>
          )}
        </div>
      )}
    </div>
  );
}

interface LiveFeedProps {
  strategyId?: string;
}

const SCROLL_THRESHOLD = 60; // px from bottom to be considered "at bottom"

export function LiveFeed({ strategyId }: LiveFeedProps) {
  const { connected, events } = useOrderStream(undefined, strategyId);
  const scrollRef   = useRef<HTMLDivElement>(null);
  const bottomRef   = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true); // true = auto-scroll to latest

  // Auto-scroll to bottom when new events arrive, only if pinned
  useEffect(() => {
    if (pinned) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [events, pinned]);

  // Detect when user scrolls away from the bottom
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_THRESHOLD;
    setPinned(atBottom);
  };

  const jumpToLatest = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    setPinned(true);
  };

  // Events are stored newest-first; reverse to show oldest→newest top→bottom
  const ordered = [...events].reverse();

  return (
    <div className="stat-card shadow-sm flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 shrink-0">
        <h3 className="text-sm font-semibold text-gray-600 dark:text-gray-300">Live Feed</h3>
        <div className="flex items-center gap-3">
          {!pinned && (
            <button
              onClick={jumpToLatest}
              className="flex items-center gap-1 text-xs text-indigo-500 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 transition-colors"
            >
              <span>↓ Latest</span>
            </button>
          )}
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-500 animate-pulse' : 'bg-gray-400 dark:bg-gray-600'}`} />
            <span className="text-xs text-gray-400 dark:text-gray-500 font-medium">
              {connected ? 'Connected' : 'Reconnecting…'}
            </span>
          </div>
        </div>
      </div>

      {/* Scrollable event list */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="overflow-y-auto max-h-80 min-h-[8rem]"
      >
        {ordered.length === 0 ? (
          <p className="text-gray-400 text-sm py-8 text-center italic">Waiting for orders…</p>
        ) : (
          <>
            {ordered.map((evt, i) => <EventRow key={i} evt={evt} />)}
            <div ref={bottomRef} />
          </>
        )}
      </div>
    </div>
  );
}
