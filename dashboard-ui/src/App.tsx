import { useState, useEffect } from 'react';
import { Routes, Route, NavLink } from 'react-router-dom';
import DashboardPage from './pages/Dashboard';
import OrdersPage from './pages/Orders';
import PositionsPage from './pages/Positions';
import StrategiesPage from './pages/Strategies';
import SettingsPage from './pages/Settings';

const NAV = [
  { to: '/',           label: 'Dashboard', icon: '📊' },
  { to: '/orders',     label: 'Orders',    icon: '📋' },
  { to: '/positions',  label: 'Positions', icon: '📈' },
  { to: '/strategies', label: 'Strategies',icon: '⚙️' },
  { to: '/settings',   label: 'Settings',  icon: '🔧' },
];

function ThemeToggle({ dark, toggle }: { dark: boolean; toggle: () => void }) {
  return (
    <button
      onClick={toggle}
      className="p-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
      title={dark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
    >
      {dark ? '☀️' : '🌙'}
    </button>
  );
}

function Sidebar({ dark, toggleTheme }: { dark: boolean; toggleTheme: () => void }) {
  return (
    <aside className="hidden md:flex flex-col w-56 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 min-h-screen p-4">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-indigo-600 dark:text-indigo-400 tracking-tight">MATP</h1>
          <p className="text-xs text-gray-500 mt-0.5">Trading Platform</p>
        </div>
        <ThemeToggle dark={dark} toggle={toggleTheme} />
      </div>
      <nav className="flex flex-col gap-1">
        {NAV.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors
               ${isActive
                 ? 'bg-indigo-600/10 dark:bg-indigo-600/20 text-indigo-600 dark:text-indigo-300'
                 : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800'}`
            }
          >
            <span>{icon}</span>
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}

function BottomNav({ dark, toggleTheme }: { dark: boolean; toggleTheme: () => void }) {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 md:hidden bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-800 flex">
      {NAV.map(({ to, label, icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) =>
            `flex-1 flex flex-col items-center py-2 text-xs font-medium transition-colors
             ${isActive ? 'text-indigo-600 dark:text-indigo-400' : 'text-gray-500'}`
          }
        >
          <span className="text-lg leading-none">{icon}</span>
          <span className="mt-0.5">{label}</span>
        </NavLink>
      ))}
      <div className="flex items-center px-4 border-l border-gray-200 dark:border-gray-800">
        <ThemeToggle dark={dark} toggle={toggleTheme} />
      </div>
    </nav>
  );
}

export default function App() {
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('theme');
    return saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

  useEffect(() => {
    if (dark) {
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  }, [dark]);

  const toggleTheme = () => setDark(!dark);

  return (
    <div className="flex min-h-screen bg-gray-50 dark:bg-gray-950 transition-colors duration-200">
      <Sidebar dark={dark} toggleTheme={toggleTheme} />
      <main className="flex-1 overflow-auto pb-20 md:pb-0">
        <Routes>
          <Route path="/"           element={<DashboardPage />} />
          <Route path="/orders"     element={<OrdersPage />} />
          <Route path="/positions"  element={<PositionsPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/settings"   element={<SettingsPage />} />
        </Routes>
      </main>
      <BottomNav dark={dark} toggleTheme={toggleTheme} />
    </div>
  );
}
