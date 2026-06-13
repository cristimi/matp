import React from 'react';

interface DataCell {
  label: string;
  value: React.ReactNode;
}

interface DataGridProps {
  rows:   DataCell[][];
  style?: React.CSSProperties;
}

export function DataGrid({ rows, style }: DataGridProps) {
  return (
    <div style={{
      display:       'flex',
      flexDirection: 'column',
      margin:        '8px 12px 8px 18px',
      ...style,
      borderRadius:  'var(--pill-r)',
      overflow:      'hidden',
      border:        '1px solid var(--border)',
      background:    'rgba(226,232,240,.4)',
    }}>
      {rows.map((row, rowIdx) => (
        <div
          key={rowIdx}
          style={{
            display:     'flex',
            width:       '100%',
            borderBottom: rowIdx < rows.length - 1 ? '1px solid var(--border)' : 'none',
          }}
        >
          {row.map((cell, cellIdx) => (
            <div
              key={cellIdx}
              style={{
                flex:          1,
                padding:       '6px 10px',
                display:       'flex',
                flexDirection: 'column',
                gap:           '1px',
                borderRight:   cellIdx < row.length - 1 ? '1px solid var(--border)' : 'none',
                overflow:      'hidden',
              }}
            >
              <span style={{
                fontSize:      '9px',
                fontWeight:    600,
                letterSpacing: '.11em',
                textTransform: 'uppercase',
                color:         'var(--dim)',
                marginBottom:  '2px',
              }}>
                {cell.label}
              </span>
              {cell.value}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
