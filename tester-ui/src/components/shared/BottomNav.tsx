import React from 'react';
import { NavLink } from 'react-router-dom';

const TABS = [
  { to: '/',           label: 'Strats',     icon: '⚙️' },
  { to: '/simulation', label: 'Simulation', icon: '📊' },
];

export function BottomNav() {
  return (
    <nav style={{
      background:   'var(--bg2)',
      borderTop:    '1px solid var(--border)',
      display:      'flex',
      padding:      '8px 0 16px',
      flexShrink:   0,
      position:     'sticky',
      bottom:       0,
    }}>
      {TABS.map(({ to, label, icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          style={({ isActive }) => ({
            flex:          1,
            display:       'flex',
            flexDirection: 'column',
            alignItems:    'center',
            gap:           '4px',
            padding:       '5px 0',
            textDecoration: 'none',
            color:         isActive ? 'var(--blue)' : 'var(--dim)',
          })}
        >
          <span style={{ fontSize: '18px', lineHeight: 1 }}>{icon}</span>
          <span style={{
            fontSize:      '9px',
            fontWeight:    600,
            textTransform: 'uppercase',
            letterSpacing: '.06em',
          }}>
            {label}
          </span>
        </NavLink>
      ))}
    </nav>
  );
}
