import React from 'react';

interface SectionHeaderProps {
  label:   string;
  count:   number;
  variant: 'live' | 'stale' | 'closed' | 'running';
}

const COLORS = {
  live:    { text:'var(--green)',        bg:'var(--green-a)',         border:'var(--green-b)' },
  stale:   { text:'var(--failed-color)', bg:'var(--failed-color-a)', border:'var(--failed-color-b)' },
  closed:  { text:'var(--gray)',         bg:'var(--gray-a)',          border:'var(--gray-b)' },
  running: { text:'var(--blue)',         bg:'var(--blue-a)',          border:'var(--blue-b)' },
};

export function SectionHeader({ label, count, variant }: SectionHeaderProps) {
  const c = COLORS[variant];
  return (
    <div style={{
      display:    'flex',
      alignItems: 'center',
      gap:        '8px',
      padding:    '4px 2px 10px',
      marginTop:  '14px',
    }}>
      <div style={{
        width:        '9px',
        height:       '9px',
        borderRadius: '50%',
        background:   c.text,
        flexShrink:   0,
      }} />
      <span style={{
        fontSize:      '12px',
        fontWeight:    800,
        letterSpacing: '.07em',
        textTransform: 'uppercase',
        flex:          1,
        color:         c.text,
      }}>
        {label}
      </span>
      <span style={{
        fontFamily:    'JetBrains Mono, monospace',
        fontSize:      '11px',
        fontWeight:    700,
        borderRadius:  '20px',
        padding:       '2px 9px',
        border:        `1px solid ${c.border}`,
        background:    c.bg,
        color:         c.text,
      }}>
        {count}
      </span>
    </div>
  );
}
