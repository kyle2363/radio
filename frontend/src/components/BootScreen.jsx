import React from 'react';
import { CircleAlert, RefreshCw } from 'lucide-react';
import './BootScreen.css';

export default function BootScreen({ ready = false, error = null, onRetry }) {
  return <div className="boot-screen">
    <div className="glow-top" aria-hidden="true" />
    <div className="glow-bottom" aria-hidden="true" />
    <div className="boot-center">
      <div className={`boot-logo${error ? ' error' : ''}`}><div className="dot" aria-hidden="true" /><div className="logo-word">Radio</div></div>
      <div className="status-tag">{error ? 'connection unavailable' : ready ? 'connected' : 'connecting'}</div>
      <div className="eq" aria-hidden="true">{[14,22,30,26,18,38,24,34,20,40,28,16,36,22,32,18].map((height, i) =>
        <div key={i} className="eq-bar" style={{ height, '--lo': 0.5, '--d': `${1.2 + i % 4 * .2}s`, '--delay': `${i * -.13}s`, animationPlayState: ready || error ? 'paused' : 'running' }} />)}</div>
      <div className="boot-message" role={error ? 'alert' : 'status'}>
        {error ? <><CircleAlert size={18} aria-hidden="true" /><p>{error}</p></> : <><p>{ready ? 'Your station is ready' : 'Connecting to your desktop server'}</p><span>{ready ? 'Opening your station…' : 'This may take a moment while the server starts.'}</span></>}
      </div>
      {error && <button className="retry-btn" onClick={onRetry}><RefreshCw size={14} aria-hidden="true" />Try again</button>}
    </div>
  </div>;
}
