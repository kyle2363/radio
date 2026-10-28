import React from 'react';

export default function PlaybackOutputSelector({ playback }) {
  const { session, audio, loading, pendingHandoff, error, retryNeeded } = playback;
  const run = action => { Promise.resolve(action()).catch(() => {}); };
  const needsGesture = audio?.status === 'awaiting_user_gesture';
  const needsReconnect = playback.needsReconnect || ['interrupted', 'awaiting_output'].includes(session?.status);
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" aria-label="Playback output">
      <label className="flex items-center gap-2">
        <span>Output</span>
        <select aria-label="Playback output" value={session?.mode || 'browser'} disabled={loading || pendingHandoff || !session}
          onChange={event => run(() => playback.switchOutput(event.target.value))}
          className="rounded border border-white/15 bg-black/20 px-2 py-1 text-inherit">
          <option value="vlc">Desktop speakers</option>
          <option value="browser">This browser</option>
        </select>
      </label>
      {pendingHandoff && <button type="button" onClick={() => run(playback.retryHandoff)} className="underline">Confirm output switch</button>}
      {needsReconnect && <button type="button" onClick={() => run(playback.reactivate)} className="rounded border px-2 py-1">Reconnect listening session</button>}
      {needsGesture && !needsReconnect && <button type="button" onClick={() => run(playback.activate)} className="rounded border px-2 py-1">Play in this browser</button>}
      {!session && error && !loading && <button type="button" onClick={() => run(playback.retry)} className="rounded border px-2 py-1">{retryNeeded ? 'Start a new listening session' : 'Retry connection'}</button>}
      {error && <span role="status" className="text-red-400">{error}</span>}
    </div>
  );
}
