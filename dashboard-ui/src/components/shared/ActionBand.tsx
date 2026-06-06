import React from 'react';

interface ActionButton {
  label:   string;
  color:   'red' | 'blue' | 'green' | 'orange';
  onClick: () => void;
}

const COLOR_MAP: Record<ActionButton['color'], string> = {
  red:    'var(--red)',
  blue:   'var(--blue)',
  green:  'var(--green)',
  orange: 'var(--failed-color)',
};

interface ActionBandProps {
  buttons: ActionButton[];
}

export function ActionBand({ buttons }: ActionBandProps) {
  return (
    <div style={{
      borderTop:  '1px solid var(--border)',
      background: 'var(--bg2)',
      display:    'flex',
    }}>
      {buttons.map((btn, idx) => (
        <button
          key={idx}
          onClick={btn.onClick}
          style={{
            flex:          1,
            background:    'transparent',
            border:        'none',
            borderRight:   idx < buttons.length - 1
              ? '1px solid var(--border)'
              : 'none',
            color:         COLOR_MAP[btn.color],
            fontSize:      '11px',
            fontWeight:    700,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
            padding:       '10px',
            cursor:        'pointer',
            textAlign:     'center',
          }}
        >
          {btn.label}
        </button>
      ))}
    </div>
  );
}
