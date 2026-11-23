import React, { useEffect, useRef, useState } from 'react';
import { remoteHostingPill } from '../ui/consistency.js';

const hintStyle = { fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6 };
const titles = { off: 'Remote hosting is stopped', starting_gateway: 'Starting secure gateway', starting_tunnel: 'Starting Cloudflare Quick Tunnel', verifying_endpoint: 'Verifying endpoint identity', available: 'Remote connection is available', reconnecting: 'Reconnecting', discovery_error: 'Connection update failed', stopping: 'Stopping remote hosting', error: 'Remote hosting needs attention' };

export default function RemoteHostingSettings() {
  const [hosting, setHosting] = useState({ state: 'off', endpoint: null, binaryPath: '', restoreOnLaunch: false });
  const [pathValue, setPathValue] = useState('');
  const pathDirtyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [discovery, setDiscovery] = useState({ configured: false, signedIn: false, uid: null, publishing: false });
  const [projectId, setProjectId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const discoveryConfigDirtyRef = useRef(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const alive = useRef(false);
  const refresh = async () => { const [value, discoveryValue] = await Promise.all([window.electron.getRemoteHostingStatus(), window.electron.getDiscoveryStatus?.()]); if (alive.current) { setHosting(value); if (!pathDirtyRef.current) setPathValue(value.binaryPath || ''); if (discoveryValue) { setDiscovery(discoveryValue); if (!discoveryConfigDirtyRef.current) { setProjectId(discoveryValue.projectId || ''); setApiKey(discoveryValue.apiKey || ''); } } } };
  useEffect(() => {
    alive.current = true;
    refresh().catch(() => {});
    const timer = setInterval(() => refresh().catch(() => {}), 1500);
    const unsubscribe = window.electron.onRemoteHostingChange(value => { setHosting(value); if (!pathDirtyRef.current) setPathValue(value.binaryPath || ''); });
    return () => { alive.current = false; clearInterval(timer); unsubscribe?.(); };
  }, []);
  const perform = async action => {
    if (busy) return;
    setBusy(true); setError('');
    try { await action(); await refresh(); }
    catch (failure) { if (alive.current) setError(failure?.message || 'Remote hosting could not be updated.'); }
    finally { if (alive.current) setBusy(false); }
  };
  const copy = () => perform(async () => {
    if (!hosting.endpoint || hosting.state !== 'available') throw Error('Wait until the endpoint is verified before copying the phone update.');
    const descriptor = await window.electron.getFreshRemoteHostingDescriptor();
    await navigator.clipboard.writeText(JSON.stringify(descriptor));
  });
  const saveDiscoveryConfig = () => perform(async () => {
    const value = await window.electron.configureDiscovery({ projectId: projectId.trim(), apiKey: apiKey.trim() });
    discoveryConfigDirtyRef.current = false; setProjectId(value.projectId || ''); setApiKey(value.apiKey || ''); setDiscovery(value);
  });
  const signIn = () => perform(async () => {
    const submittedPassword = password; setPassword('');
    try { const value = await window.electron.signInDiscovery(email.trim(), submittedPassword); setEmail(''); setDiscovery(value); }
    finally { setPassword(''); }
  });
  const tone = remoteHostingPill(hosting)?.tone;
  return <section aria-label="Remote hosting" className="card" style={{ margin: '10px 0 16px', padding: 12 }}>
    <div className="set-label" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <span className={`dot ${tone === 'ok' ? 'dot-ok' : tone === 'err' ? 'dot-err' : tone === 'warn' ? 'dot-warn' : ''}`} style={tone ? undefined : { background: 'var(--text-muted)' }} />
      Remote hosting
    </div>
    <p role="status" className="ui-hint">{titles[hosting.state] || 'Remote hosting status unavailable'}{hosting.error ? ` · ${hosting.error}` : ''}</p>
    {hosting.endpoint && hosting.state === 'available' && <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '8px 0' }}>
      <code aria-label="Validated remote endpoint" style={{ fontSize: 11, color: 'var(--text-primary)', overflowWrap: 'anywhere' }}>{hosting.endpoint}</code>
      <button type="button" className="ui-btn sm" disabled={busy} onClick={() => perform(() => navigator.clipboard.writeText(hosting.endpoint))}>Copy URL</button>
      <button type="button" className="ui-btn sm" disabled={busy || !hosting.descriptor} onClick={copy}>Copy phone connection update</button>
    </div>}
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
      {hosting.state === 'off' || hosting.state === 'error' ? <button type="button" className="ui-btn primary sm" disabled={busy} onClick={() => perform(() => window.electron.startRemoteHosting())}>{hosting.state === 'error' ? 'Start hosting' : 'Start remote hosting'}</button> : null}
      {hosting.state === 'error' && <button type="button" className="ui-btn sm" disabled={busy} onClick={() => perform(() => window.electron.retryRemoteHosting())}>Retry</button>}
      {hosting.state !== 'off' && <button type="button" className="ui-btn sm" disabled={busy || hosting.state === 'stopping'} onClick={() => perform(() => window.electron.stopRemoteHosting())}>Stop hosting</button>}
    </div>
    <p className="ui-hint">Remote access is off until you start it. Quick Tunnel URLs can change after reconnecting. Without Firebase discovery, paste the signed phone connection update into Android Server settings after an endpoint change. Local phone playback remains independent.</p>
    <details className="ui-details">
      <summary>Advanced</summary>
      <label className="set-label" htmlFor="cloudflared-path">cloudflared.exe path</label>
      <div style={{ display: 'flex', gap: 8, margin: '5px 0 9px' }}>
        <input className="ui-input" id="cloudflared-path" aria-label="cloudflared executable path" value={pathValue} onChange={event => { setPathValue(event.target.value); pathDirtyRef.current = true; }} placeholder="Full path to cloudflared.exe" style={{ flex: 1, minWidth: 0 }} />
        <button type="button" className="ui-btn sm" disabled={busy || !pathValue.trim()} onClick={() => perform(async () => { await window.electron.setRemoteHostingBinaryPath(pathValue.trim()); pathDirtyRef.current = false; })}>Save path</button>
      </div>
      <label style={{ ...hintStyle, display: 'flex', gap: 7, alignItems: 'center' }}>
        <input type="checkbox" checked={hosting.restoreOnLaunch === true} onChange={event => perform(() => window.electron.setRemoteHostingRestore(event.target.checked))} />
        Restore remote hosting when Radio starts
      </label>
    </details>
    {error && <p role="alert" className="ui-error">{error}</p>}
    <details className="ui-details">
      <summary>Automatic endpoint discovery (Firebase)</summary>
      <p className="ui-hint">Discovery stores only the signed endpoint descriptor. Configure your Firebase project and sign in with its publisher account. Passwords are not saved; refresh credentials use Windows protected storage.</p>
      <label className="set-label" htmlFor="firebase-project-id">Firebase project ID</label>
      <input className="ui-input" id="firebase-project-id" aria-label="Firebase project ID" value={projectId} onChange={e => { discoveryConfigDirtyRef.current = true; setProjectId(e.target.value); }} placeholder="your-project-id" style={{ width: '100%', boxSizing: 'border-box', margin: '4px 0 8px' }} />
      <label className="set-label" htmlFor="firebase-api-key">Firebase Web API key</label>
      <input className="ui-input" id="firebase-api-key" aria-label="Firebase Web API key" value={apiKey} onChange={e => { discoveryConfigDirtyRef.current = true; setApiKey(e.target.value); }} placeholder="Web API key" autoComplete="off" style={{ width: '100%', boxSizing: 'border-box', margin: '4px 0 8px' }} />
      <button type="button" className="ui-btn sm" disabled={busy || !projectId.trim() || !apiKey.trim()} onClick={saveDiscoveryConfig}>Save project config</button>
      {discovery.signedIn ? <div style={{ marginTop: 8 }}><span className="ui-hint">Signed in as {discovery.uid}; publisher {discovery.publishing ? 'active' : 'idle'}.</span> <button type="button" className="ui-btn sm" disabled={busy} onClick={() => perform(async () => setDiscovery(await window.electron.signOutDiscovery()))}>Sign out</button></div> : <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
        <input className="ui-input" aria-label="Publisher email" value={email} onChange={e => setEmail(e.target.value)} placeholder="Publisher email" autoComplete="username"  />
        <input className="ui-input" aria-label="Publisher password" type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Password (not stored)" autoComplete="current-password"  />
        <button type="button" className="ui-btn sm" disabled={busy || !discovery.configured || !email.trim() || !password} onClick={signIn}>Sign in to Firebase</button>
      </div>}
      {discovery.serverId && <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}><span className="ui-hint">Server ID</span><code aria-label="Discovery server ID" style={{ fontSize: 11, userSelect: 'all' }}>{discovery.serverId}</code><button type="button" className="ui-btn sm" onClick={() => navigator.clipboard.writeText(discovery.serverId)}>Copy</button></div>}
      {discovery.error && <p role="status" className="ui-error">{discovery.error}</p>}
    </details>
  </section>;
}
