import React, { useEffect, useRef, useState } from 'react';
import { createPairingBundleState } from '../api/pairingBundleState';
import MobileCatalogSettings from './MobileCatalogSettings';
import RemoteHostingSettings from './RemoteHostingSettings';
import { hasConnectedRadio } from '../api/connectedRadioState';

const buttonStyle = { border: '1px solid var(--border)', borderRadius: 6, background: 'var(--bg-surface)', color: 'var(--text-primary)', padding: '6px 10px', fontSize: 11, cursor: 'pointer' };
const hintStyle = { fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.6 };

export default function MobilePairingSettings() {
  const [available, setAvailable] = useState(false);
  const [gatewayEnabled, setGatewayEnabled] = useState(false);
  const [deviceName, setDeviceName] = useState('Phone');
  const [devices, setDevices] = useState([]);
  const [bundle, setBundle] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const alive = useRef(false);
  const bundleState = useRef(null);

  const refresh = async () => {
    const result = await window.electron.listPairedMobileDevices();
    const status = await window.electron.getMobileGatewayStatus();
    if (alive.current) { setDevices(result.devices); setGatewayEnabled(status.enabled); }
  };

  useEffect(() => {
    alive.current = true;
    const state = createPairingBundleState(setBundle);
    bundleState.current = state;
    const timer = setInterval(() => state.tick(), 1000);
    window.electron?.isMobilePairingAvailable?.().then(async enabled => {
      if (!alive.current) return;
      setAvailable(enabled);
      if (enabled) {
        try { await refresh(); } catch { if (alive.current) setError('Sign in to the owned desktop server to manage paired devices.'); }
      }
    }).catch(() => {});
    return () => { alive.current = false; state.dispose(); clearInterval(timer); };
  }, []);

  const perform = async action => {
    if (busy) return;
    setBusy(true);
    setError('');
    setMessage('');
    try { await action(); } catch { if (alive.current) setError('Mobile pairing is unavailable. Sign in to the owned desktop server and try again.'); }
    finally { if (alive.current) setBusy(false); }
  };

  const create = () => perform(async () => {
    const state = bundleState.current;
    const request = state.begin();
    const value = await window.electron.createMobilePairingBundle(deviceName.trim());
    state.accept(request, value);
    await refresh();
  });

  const revoke = id => perform(async () => {
    bundleState.current.clear();
    await window.electron.revokePairedMobileDevice(id);
    await refresh();
    if (alive.current) setMessage('Device revoked. Local phone music stays available.');
  });

  const enableGateway = () => perform(async () => {
    await window.electron.enableMobileGateway();
    await refresh();
    if (alive.current) setMessage('USB connection enabled for paired devices.');
  });

  const grantProfileSync = id => perform(async () => {
    await window.electron.grantMobileProfileSync(id);
    await refresh();
    if (alive.current) setMessage('Profile sync allowed. The existing phone credential and pending events are preserved.');
  });

  const setConnectedRadio = (id, enabled, name) => perform(async () => {
    if (enabled) await window.electron.grantMobileConnectedRadio(id);
    else await window.electron.revokeMobileConnectedRadio(id);
    await refresh();
    if (alive.current) setMessage(enabled ? `Connected Radio allowed for ${name}.` : `Connected Radio turned off for ${name}.`);
  });

  const copy = () => perform(async () => {
    if (!bundle || bundle.expires_at * 1000 <= Date.now()) { bundleState.current.clear(); return; }
    await navigator.clipboard.writeText(JSON.stringify(bundle));
    if (alive.current) setMessage('Pairing bundle copied. Paste it into the Android debug app.');
  });

  if (!available) return <div><RemoteHostingSettings /><div style={hintStyle}>USB pairing is available in the desktop development build and Android debug app.</div></div>;

  return <div>
    <RemoteHostingSettings />
    <div className="set-label">USB development only</div>
    <p style={hintStyle}>Connect the phone by USB with USB debugging enabled. Run this on the computer before pairing:</p>
    <code style={{ display: 'block', fontSize: 11, color: 'var(--text-primary)', marginBottom: 12 }}>adb reverse tcp:18766 tcp:18766</code>
    <p style={hintStyle}>Create a bundle, then paste it into Android Settings → Server. The USB server starts on creation and stops when Radio quits. Local phone playback stays independent.</p>
    {!gatewayEnabled && <div className="set-row">
      <div><div className="set-label">USB connection is stopped</div><div style={hintStyle}>Start it to let an already paired phone connect after Radio restarts.</div></div>
      <button type="button" style={buttonStyle} disabled={busy} onClick={enableGateway}>Enable USB connection</button>
    </div>}
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', margin: '12px 0' }}>
      <label htmlFor="pairing-device-name" className="set-label">Device name</label>
      <input id="pairing-device-name" value={deviceName} maxLength={80} disabled={busy} onChange={event => setDeviceName(event.target.value)} style={{ width: 140, padding: '6px 8px', fontSize: 12 }} />
      <button type="button" style={buttonStyle} disabled={busy || !deviceName.trim()} onClick={create}>Create pairing bundle</button>
    </div>
    {bundle && <div style={{ marginBottom: 16 }}>
      <div style={hintStyle}>One-time bundle · Expires {new Date(bundle.expires_at * 1000).toLocaleTimeString()}</div>
      <textarea aria-label="One-time pairing bundle" readOnly value={JSON.stringify(bundle)} rows={5} spellCheck={false} style={{ width: '100%', boxSizing: 'border-box', fontSize: 11, margin: '6px 0', resize: 'vertical' }} />
      <button type="button" style={buttonStyle} disabled={busy} onClick={copy}>Copy bundle</button>
    </div>}
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '16px 0 6px' }}>
      <div className="set-label">Paired devices</div>
      <button type="button" style={buttonStyle} disabled={busy} onClick={() => perform(refresh)}>Refresh</button>
    </div>
    {!devices.length && <div style={hintStyle}>No paired devices.</div>}
    {devices.map(device => {
      const expired = device.expires_at != null && device.expires_at * 1000 <= Date.now();
      const hasProfileSync = device.scopes.includes('profile:read') && device.scopes.includes('profile:write');
      return <div className="set-row" key={device.device_id}>
        <div><div className="set-label">{device.name}</div><div style={hintStyle}>{device.revoked ? 'Revoked' : expired ? 'Expired' : 'Paired'} · {device.scopes.join(', ')}</div></div>
        {!device.revoked && !expired && !hasProfileSync && <button type="button" style={buttonStyle} disabled={busy} onClick={() => grantProfileSync(device.device_id)} aria-label={`Allow profile sync for ${device.name}`}>Allow profile sync</button>}
        {!device.revoked && !expired && hasProfileSync && <span style={hintStyle}>Profile sync allowed</span>}
        {!device.revoked && !expired && !hasConnectedRadio(device) && <button type="button" style={buttonStyle} disabled={busy} onClick={() => setConnectedRadio(device.device_id, true, device.name)} aria-label={`Allow Connected Radio for ${device.name}`}>Allow Connected Radio</button>}
        {!device.revoked && !expired && hasConnectedRadio(device) && <button type="button" style={buttonStyle} disabled={busy} onClick={() => setConnectedRadio(device.device_id, false, device.name)} aria-label={`Turn off Connected Radio for ${device.name}`}>Turn off Connected Radio</button>}
        <button type="button" style={buttonStyle} disabled={busy || device.revoked} onClick={() => revoke(device.device_id)} aria-label={`Revoke ${device.name}`}>Revoke</button>
      </div>;
    })}
    <MobileCatalogSettings />
    {error && <p role="alert" style={{ ...hintStyle, color: 'var(--danger, #f87171)' }}>{error}</p>}
    {message && <p role="status" style={hintStyle}>{message}</p>}
  </div>;
}
