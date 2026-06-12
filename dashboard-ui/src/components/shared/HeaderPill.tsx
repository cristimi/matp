import React from 'react';

export type PillVariant =
  | 'long' | 'short' | 'lev' | 'tech'
  | 'open' | 'stale' | 'closed' | 'neutral'
  | 'buy'  | 'sell'  | 'ai';

const VARIANT_STYLES: Record<PillVariant, React.CSSProperties> = {
  long:    { background:'var(--green-a)',         color:'var(--green)',        borderColor:'var(--green-b)' },
  short:   { background:'var(--red-a)',           color:'var(--red)',          borderColor:'var(--red-b)' },
  lev:     { background:'var(--blue-a)',          color:'var(--blue)',         borderColor:'var(--blue-b)',  textTransform:'lowercase' },
  tech:    { background:'var(--blue-a)',          color:'var(--blue)',         borderColor:'var(--blue-b)' },
  open:    { background:'var(--green-a)',         color:'var(--green)',        borderColor:'var(--green-b)' },
  stale:   { background:'var(--failed-color-a)',  color:'var(--failed-color)', borderColor:'var(--failed-color-b)' },
  closed:  { background:'var(--gray-a)',          color:'var(--gray)',         borderColor:'var(--gray-b)' },
  neutral: { background:'var(--bg2)',             color:'var(--muted)',        borderColor:'var(--border)',  textTransform:'none' as const },
  buy:     { background:'var(--green-a)',         color:'var(--green)',        borderColor:'var(--green-b)' },
  sell:    { background:'var(--red-a)',           color:'var(--red)',          borderColor:'var(--red-b)' },
  ai:      { background:'rgba(83,74,183,.10)',    color:'#534AB7',             borderColor:'rgba(83,74,183,.25)' },
};

interface HeaderPillProps {
  variant:  PillVariant;
  children: React.ReactNode;
  style?:   React.CSSProperties;
}

export function HeaderPill({ variant, children, style }: HeaderPillProps) {
  return (
    <span style={{
      fontFamily:    'JetBrains Mono, monospace',
      fontSize:      '10px',
      fontWeight:    600,
      textTransform: 'uppercase',
      borderRadius:  'var(--pill-r)',
      padding:       '2px 6px',
      border:        '1px solid',
      textAlign:     'center',
      lineHeight:    1,
      display:       'inline-block',
      flexShrink:    0,
      letterSpacing: '.04em',
      ...VARIANT_STYLES[variant],
      ...style,
    }}>
      {children}
    </span>
  );
}
