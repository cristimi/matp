import { useState, useEffect } from 'react';
import { DataGrid, HeaderPill } from '../components/shared';
import { formatPct, formatPnl, pnlColor } from '../utils/pnl';
import { fetchStrategyTree } from '../api';
import type { StrategyTreeItem } from '../api';

const MONO = '"JetBrains Mono", monospace';

export default function StrategyTreePage() {
  const [strategies, setStrategies] = useState<StrategyTreeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchStrategyTree()
      .then(setStrategies)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ maxWidth: 460, margin: '0 auto', padding: '14px 10px 70px' }}>
      {loading && (
        <div style={{ padding: 24, color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>
          Loading…
        </div>
      )}
      {error && (
        <div style={{ padding: 24, color: 'var(--red)', fontSize: 13 }}>
          {error}
        </div>
      )}
      {!loading && !error && strategies.length === 0 && (
        <div style={{ padding: 24, color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>
          No strategies
        </div>
      )}
      {strategies.map(s => (
        <StrategyCard key={s.id} strategy={s} />
      ))}
    </div>
  );
}

function StrategyCard({ strategy: s }: { strategy: StrategyTreeItem }) {
  const hasOpen = s.open_positions_count > 0;
  const isStopped = !s.enabled;

  const gridCells = buildGridCells(s, hasOpen);

  return (
    <div
      style={{
        background: 'var(--bg2)',
        border: '1px solid var(--border)',
        borderRadius: 11,
        marginBottom: 11,
        overflow: 'hidden',
        position: 'relative',
        boxShadow: '0 1px 2px rgba(20,30,50,.04)',
      }}
    >
      {/* Left accent bar */}
      <div
        style={{
          position: 'absolute',
          left: 0, top: 0, bottom: 0, width: 4,
          borderRadius: '11px 0 0 11px',
          background: isStopped ? 'var(--gray)' : 'var(--blue)',
          zIndex: 1,
        }}
      />

      {/* Top-right icon buttons (inert in Phase 2) */}
      <div
        style={{
          position: 'absolute',
          top: 0, right: 2,
          display: 'flex',
          alignItems: 'flex-start',
          zIndex: 2,
        }}
      >
        <button
          type="button"
          disabled
          aria-label={isStopped ? 'Resume (Phase 3)' : 'Pause (Phase 3)'}
          style={{
            width: 30, height: 30, borderRadius: '50%',
            border: '1px solid var(--border)',
            background: 'var(--bg3)',
            color: 'var(--muted)',
            fontSize: 11,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '6px 5px',
            flexShrink: 0,
            cursor: 'default',
            padding: 0,
          }}
        >
          {isStopped ? '▶' : '⏸'}
        </button>
        <button
          type="button"
          disabled
          aria-label="Strategy details (Phase 2C)"
          style={{
            width: 30, height: 30, borderRadius: '50%',
            border: '1px solid var(--border)',
            background: 'var(--bg3)',
            color: 'var(--muted)',
            fontSize: 13, fontWeight: 600,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '6px 9px 6px 0',
            flexShrink: 0,
            cursor: 'default',
            padding: 0,
          }}
        >
          ⓘ
        </button>
      </div>

      {/* Rows 1 + 2 — tap target (expansion wired in Phase 2B) */}
      <div
        role="button"
        tabIndex={0}
        aria-label={`Expand ${s.name}`}
        style={{
          paddingLeft: 14,
          paddingRight: 84,
          paddingTop: 9,
          paddingBottom: 6,
          cursor: 'pointer',
          userSelect: 'none',
          WebkitUserSelect: 'none',
        }}
      >
        {/* Row 1: green dot + symbol pill + strategy name */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
          {hasOpen && (
            <span
              style={{
                width: 7, height: 7,
                borderRadius: '50%',
                background: 'var(--green)',
                boxShadow: '0 0 0 2px var(--green-a)',
                flexShrink: 0,
                display: 'inline-block',
              }}
            />
          )}
          <HeaderPill variant="neutral" style={{ fontWeight: 700 }}>
            {s.symbol}
          </HeaderPill>
          <span
            style={{
              fontWeight: 700,
              fontSize: 14,
              letterSpacing: '-.01em',
              color: isStopped ? 'var(--muted)' : 'var(--text)',
            }}
          >
            {s.name}
          </span>
        </div>

        {/* Row 2: account chip + stop chip */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 5, flexWrap: 'wrap' }}>
          <span
            style={{
              fontSize: 10.5,
              padding: '1px 7px',
              borderRadius: 20,
              border: '1px solid var(--blue-b)',
              color: 'var(--blue)',
              background: 'var(--blue-a)',
              whiteSpace: 'nowrap',
            }}
          >
            {s.account_label}
          </span>
          {isStopped && (
            <span
              style={{
                fontFamily: MONO,
                fontSize: 10, fontWeight: 600, letterSpacing: '.02em',
                padding: '2px 7px', borderRadius: 6,
                border: '1px solid var(--border-hi)',
                background: 'var(--bg3)',
                color: 'var(--muted)',
                whiteSpace: 'nowrap', lineHeight: 1,
              }}
            >
              {s.stop_reason ?? 'stopped'}
            </span>
          )}
        </div>
      </div>

      {/* Row 3: Allocation / Total Return / Open PnL */}
      <DataGrid rows={[gridCells]} />
    </div>
  );
}

function buildGridCells(s: StrategyTreeItem, hasOpen: boolean) {
  const alloc = {
    label: 'Allocation',
    value: (
      <span style={{ fontFamily: MONO, fontWeight: 700, fontSize: 13, color: 'var(--text)' }}>
        {Number(s.capital_allocation).toFixed(0)}
      </span>
    ),
  };
  const ret = {
    label: 'Total Return',
    value: (
      <span style={{ fontFamily: MONO, fontWeight: 700, fontSize: 13, color: pnlColor(s.total_return) }}>
        {formatPct(s.total_return)}
      </span>
    ),
  };
  if (hasOpen) {
    return [
      alloc,
      ret,
      {
        label: 'Open PnL',
        value: (
          <span style={{ fontFamily: MONO, fontWeight: 700, fontSize: 13, color: pnlColor(s.open_pnl) }}>
            {formatPnl(s.open_pnl)}
          </span>
        ),
      },
    ];
  }
  return [alloc, ret];
}
