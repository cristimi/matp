import React from 'react';

interface FilterItem {
  label:    string;
  active?:  boolean;
  clear?:   boolean;
  onClick?: () => void;
}

interface FilterBarProps {
  filters: FilterItem[];
}

export function FilterBar({ filters }: FilterBarProps) {
  return (
    <div style={{
      display:      'flex',
      gap:          '6px',
      padding:      '10px 14px',
      borderBottom: '1px solid var(--border)',
      overflowX:    'auto',
      flexShrink:   0,
      scrollbarWidth: 'none',
    }}>
      {filters.map((f, idx) => (
        <span
          key={idx}
          onClick={f.onClick}
          style={{
            whiteSpace:   'nowrap',
            background:   f.active ? 'var(--blue-a)' : 'var(--bg2)',
            border:       `1px solid ${f.active ? 'var(--blue)' : 'var(--border)'}`,
            borderRadius: '20px',
            padding:      '5px 12px',
            fontSize:     '10px',
            fontWeight:   500,
            color:        f.clear ? 'var(--red)' : f.active ? 'var(--blue)' : 'var(--muted)',
            cursor:       'pointer',
          }}
        >
          {f.label}
        </span>
      ))}
    </div>
  );
}
