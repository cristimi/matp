import React from 'react';

interface TopBarProps {
  title:   string;
  right?:  React.ReactNode;
  onBack?: () => void;
}

export function TopBar({ title, right, onBack }: TopBarProps) {
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
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        {onBack && (
          <button onClick={onBack} style={{
            background: 'none', border: 'none', color: 'var(--dim)',
            fontSize: '18px', cursor: 'pointer', padding: '0 4px 0 0', lineHeight: 1,
          }}>
            ←
          </button>
        )}
        <span style={{
          fontSize:      '23px',
          fontWeight:    800,
          letterSpacing: '-.02em',
          color:         'var(--text)',
        }}>
          {title}
        </span>
      </div>
      {right && (
        <div style={{ display:'flex', alignItems:'center', gap:'6px' }}>
          {right}
        </div>
      )}
    </div>
  );
}
