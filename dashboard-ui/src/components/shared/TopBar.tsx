import React from 'react';

interface TopBarProps {
  title: string;
  right?: React.ReactNode;
}

export function TopBar({ title, right }: TopBarProps) {
  return (
    <div style={{
      display:        'flex',
      alignItems:     'center',
      justifyContent: 'space-between',
      padding:        '18px 20px 12px',
      background:     'var(--bg2)',
      borderBottom:   '1px solid var(--border)',
      flexShrink:     0,
    }}>
      <span style={{
        fontSize:      '23px',
        fontWeight:    800,
        letterSpacing: '-.02em',
        color:         'var(--text)',
      }}>
        {title}
      </span>
      {right && (
        <div style={{ display:'flex', alignItems:'center', gap:'6px' }}>
          {right}
        </div>
      )}
    </div>
  );
}
