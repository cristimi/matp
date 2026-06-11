import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { BottomNav } from './components/shared';
import { StrategiesScreen } from './screens/StrategiesScreen';
import { SimulationScreen } from './screens/SimulationScreen';
import './index.css';

export default function App() {
  return (
    <BrowserRouter basename="/tester">
      <div className="phone-shell">
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <Routes>
            <Route path="/"                   element={<StrategiesScreen />} />
            <Route path="/simulation"         element={<SimulationScreen />} />
            <Route path="/simulation/:runId"  element={<SimulationScreen />} />
          </Routes>
        </div>
        <BottomNav />
      </div>
    </BrowserRouter>
  );
}
