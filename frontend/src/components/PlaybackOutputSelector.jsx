import React from 'react';

export default function PlaybackOutputSelector({ playback }) {
  const { session, audio, loading, pendingHandoff, error, retryNeeded } = playback;
  const run = action => { Promise.resolve(action()).catch(() => {}); };
  const needsGesture = audio?.status === 'awaiting_user_gesture';
  const needsReconnect = playback.needsReconnect || ['interrupted', 'awaiting_output'].includes(session?.status);
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" aria-label="Playback output">
      <label className="flex items-center gap-2">
        <span style={{ color: "var(--text-muted)" }}>Output</span>
        <select aria-label="Playback output" value={session?.mode || 'browser'} disabled={loading || pendingHandoff || !session}
          onChange={event => run(() => playback.switchOutput(event.target.value))}
          className="ui-input" style={{ padding: "4px 6px", fontSize: 12, cursor: "pointer" }}>
          <option value="vlc">Desktop speakers</option>
          <option value="browser">This browser</option>
        </select>
      </label>
      {pendingHandoff && <button type="button" onClick={() => run(playback.retryHandoff)} className="ui-btn primary sm">Confirm output switch</button>}
      {needsReconnect && <button type="button" onClick={() => run(playback.reactivate)} className="ui-btn primary sm">Reconnect listening session</button>}
      {needsGesture && !needsReconnect && <button type="button" onClick={() => run(playback.activate)} className="ui-btn primary sm">Play in this browser</button>}
      {!session && error && !loading && <button type="button" onClick={() => run(playback.retry)} className="ui-btn sm">{retryNeeded ? 'Start a new listening session' : 'Retry connection'}</button>}
      {error && <span role="status" className="text-[var(--danger)]">{error}</span>}
    </div>
  );
}
