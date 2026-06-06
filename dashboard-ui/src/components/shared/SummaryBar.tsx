import React from 'react';

interface SummaryCell {
  count:   number;
  label:   string;
  variant: 'live' | 'stale' | 'closed';
}

const VARIANT_COLOR: Record<SummaryCell['variant'], string> = {
  live:   'var(--green)',
  stale:  'var(--failed-color)',
  closed: 'var(--gray)',
};

interface SummaryBarProps {
  cells: SummaryCell[];
}

export function SummaryBar({ cells }: SummaryBarProps) {
  return (
    <div style={{
      display:      'flex',
      background:   'var(--bg2)',
      borderBottom: '1px solid var(--border)',
      flexShrink:   0,
    }}>
      {cells.map((cell, idx) => (
        <div
          key={cell.label}
          style={{
            flex:          1,
            display:       'flex',
            flexDirection: 'column',
            alignItems:    'center',
            padding:       '10px 0 9px',
            borderRight:   idx < cells.length - 1
              ? '1px solid var(--border)'
              : 'none',
            gap:           '3px',
            position:      'relative',
          }}
        >
          <span style={{
            fontFamily:    'JetBrains Mono, monospace',
            fontSize:      '24px',
            fontWeight:    700,
            letterSpacing: '-.02em',
            lineHeight:    1,
            color:         VARIANT_COLOR[cell.variant],
          }}>
            {cell.count}
          </span>
          <span style={{
            fontSize:      '10px',
            fontWeight:    600,
            letterSpacing: '.08em',
            textTransform: 'uppercase',
            color:         'var(--dim)',
          }}>
            {cell.label}
          </span>
          <div style={{
            position:     'absolute',
            bottom:       0,
            left:         '18%',
            right:        '18%',
            height:       '2px',
            borderRadius: '2px',
            background:   VARIANT_COLOR[cell.variant],
          }} />
        </div>
      ))}
    </div>
  );
}
