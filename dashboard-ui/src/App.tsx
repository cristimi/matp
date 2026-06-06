import { useState, useEffect } from 'react';
import { Routes, Route, NavLink, useNavigate } from 'react-router-dom';
import StrategyForm from './pages/StrategyForm';
import DashboardPage from './pages/Dashboard';
import OrdersPage from './pages/Orders';
import PositionsPage from './pages/Positions';
import StrategiesPage from './pages/Strategies';
import AccountsPage from './pages/Accounts';
import StrategyDetail from './pages/StrategyDetail';
import SettingsPage from './pages/Settings';
import SignalLogPage from './pages/SignalLog';
import { useNavCounts } from './hooks/useNavCounts';

const NAV = [
  { to: '/strategies', label: 'Strategies', icon: '⚙️' },
  { to: '/accounts',   label: 'Accounts',   icon: '🔑' },
  { to: '/positions',  label: 'Positions',  icon: '📈' },
  { to: '/orders',     label: 'Orders',     icon: '📋' },
  { to: '/signals',    label: 'Signals',    icon: '📡' },
  { to: '/settings',   label: 'Settings',   icon: '🔧' },
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

function Sidebar({ dark, toggleTheme, isCollapsed, toggleCollapsed, hasFailedOrders }: { dark: boolean; toggleTheme: () => void; isCollapsed: boolean; toggleCollapsed: () => void; hasFailedOrders: boolean }) {
  const navigate = useNavigate();
  const counts = useNavCounts();

  const renderLabel = (label: string) => {
    switch (label) {
      case 'Strategies':
        return `${label} (${counts.strategies.active}/${counts.strategies.inactive})`;
      case 'Positions':
        return (
          <span>
            {label} ({counts.positions.open}/{counts.positions.closed}{counts.positions.stale > 0 ? `/<span className="text-red-500">${counts.positions.stale}</span>` : ''})
          </span>
        );
      case 'Orders':
        return (
          <span className="flex items-center gap-1.5">
            {label}
            {hasFailedOrders && (
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" title="Failed orders detected" />
            )}
          </span>
        );
      default:
        return label;
    }
  };

  return (
    <aside className={`hidden md:flex flex-col bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 min-h-screen p-4 transition-all duration-300 ${isCollapsed ? 'w-20' : 'w-56'}`}>
      <div className="mb-8 flex items-center justify-between cursor-pointer" onClick={() => navigate('/')}>
        {!isCollapsed && (
          <div>
            <h1 className="text-xl font-bold text-indigo-600 dark:text-indigo-400 tracking-tight">MATP</h1>
            <p className="text-xs text-gray-500 mt-0.5">Trading Platform</p>
          </div>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); toggleCollapsed(); }}
          className="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          {isCollapsed ? '➡️' : '⬅️'}
        </button>
      </div>
      <nav className="flex flex-col gap-1">
        {NAV.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors
               ${isActive
                 ? 'bg-indigo-600/10 dark:bg-indigo-600/20 text-indigo-600 dark:text-indigo-300'
                 : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800'}`
            }
            title={isCollapsed ? label : ''}
          >
            <span className="text-lg">{icon}</span>
            {!isCollapsed && renderLabel(label)}
          </NavLink>
        ))}
      </nav>
      {!isCollapsed && (
        <div className="mt-auto pt-4 border-t border-gray-200 dark:border-gray-800">
           <ThemeToggle dark={dark} toggle={toggleTheme} />
        </div>
      )}
    </aside>
  );
}

function BottomNav({ dark, toggleTheme, counts, hasFailedOrders }: { dark: boolean; toggleTheme: () => void; counts: any; hasFailedOrders: boolean }) {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 md:hidden bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-800 flex">
      {NAV.map(({ to, label, icon }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            `flex-1 flex flex-col items-center py-2 text-xs font-medium transition-colors
             ${isActive ? 'text-indigo-600 dark:text-indigo-400' : 'text-gray-500'}`
          }
        >
          <span className="text-lg leading-none">{icon}</span>
          <span className="mt-0.5 flex items-center gap-1">
            {label}
            {label === 'Positions' && counts.positions.stale > 0 && (
              <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
            )}
            {label === 'Orders' && hasFailedOrders && (
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
            )}
          </span>
        </NavLink>
      ))}
    </nav>
  );
}

export default function App() {
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('theme');
    return saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  });
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [hasFailedOrders, setHasFailedOrders] = useState(false);

  useEffect(() => {
    const check = async () => {
      try {
        // Checking for both lag_failed and route_failed (backend strings)
        const res  = await fetch('/api/dashboard/orders?limit=1&status=lag_failed');
        const data = await res.json();
        const lagFailed = (data.items ?? []).length > 0;
        
        if (lagFailed) {
          setHasFailedOrders(true);
          return;
        }

        const res2 = await fetch('/api/dashboard/orders?limit=1&status=route_failed');
        const data2 = await res2.json();
        const routeFailed = (data2.items ?? []).length > 0;
        setHasFailedOrders(routeFailed);
      } catch {
        setHasFailedOrders(false);
      }
    };
    check();
    const interval = setInterval(check, 30000);
    return () => clearInterval(interval);
  }, []);

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
  const toggleCollapsed = () => setIsCollapsed(!isCollapsed);
  const counts = useNavCounts();

  return (
    <div className="flex min-h-screen bg-gray-50 dark:bg-gray-950 transition-colors duration-200">
      <Sidebar dark={dark} toggleTheme={toggleTheme} isCollapsed={isCollapsed} toggleCollapsed={toggleCollapsed} hasFailedOrders={hasFailedOrders} />
      <main className="flex-1 overflow-auto pb-20 md:pb-0">
        <Routes>
          <Route path="/"           element={<DashboardPage />} />
          <Route path="/orders"     element={<OrdersPage />} />
          <Route path="/positions"  element={<PositionsPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/strategies/new" element={<StrategyForm />} />
          <Route path="/strategies/:id/edit" element={<StrategyForm />} />
          <Route path="/accounts"   element={<AccountsPage />} />
          <Route path="/signals"    element={<SignalLogPage />} />
          <Route path="/strategy/:id" element={<StrategyDetail />} />
          <Route path="/settings"   element={<SettingsPage />} />
        </Routes>
      </main>
      <BottomNav dark={dark} toggleTheme={toggleTheme} counts={counts} hasFailedOrders={hasFailedOrders} />
    </div>
  );
}
