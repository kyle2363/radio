import React, { useState, useEffect } from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import ErrorBoundary from './components/ErrorBoundary';
import BootScreen from './components/BootScreen';
import AuthenticationGate from './components/AuthenticationGate';
import TooltipLayer from './components/TooltipLayer';
import { API_BASE_URL } from './api/index.js';
import { connectToServer } from './ui/bootConnection.js';
import './styles/global.css';

function AppWithBoot() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => connectToServer({
    probe: () => fetch(`${API_BASE_URL}/api/health`, { signal: AbortSignal.timeout(3000), credentials: 'include' }),
    onReady: () => setReady(true), onError: setError,
  }), [attempt]);
  return <ErrorBoundary><div style={{ position: 'relative', width: '100vw', height: '100vh' }}>
    {ready ? <App /> : <BootScreen error={error} onRetry={() => { setError(null); setReady(false); setAttempt(n => n + 1); }} />}
    <TooltipLayer />
  </div></ErrorBoundary>;
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode><AuthenticationGate><AppWithBoot /></AuthenticationGate></React.StrictMode>
);
