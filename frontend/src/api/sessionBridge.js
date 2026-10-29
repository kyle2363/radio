// Page-local compatibility bridge. Capabilities are available only to request
// headers; subscribers receive detached logical state, never controller objects.
let current = null;
let restore = null;
let lastSignature = null;
const listeners = new Set();
const privateKeys = new Set(['owner_key', 'owner', 'token', 'password', 'capability', 'authorization']);

function publicCopy(value) {
  if (Array.isArray(value)) return value.map(publicCopy);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !privateKeys.has(key.toLowerCase())).map(([key, item]) => [key, publicCopy(item)]));
  return value;
}

export function localPlaybackState(state = current?.getState?.(), clientId = current?.clientId) {
  const session = state?.session, audio = state?.audio;
  const item = session?.items?.[session.active_index || 0];
  if (!clientId || session?.mode !== 'browser' || session.output_client_id !== clientId ||
      !session.instance_id || audio?.instance_id !== session.instance_id || !item?.item_id || audio.item_id !== item.item_id ||
      !['playing', 'paused', 'interrupted', 'awaiting_user_gesture'].includes(audio.status)) return null;
  return { instance_id: audio.instance_id, item_id: audio.item_id, song_id: item.song_id,
    status: audio.status, is_playing: audio.status === 'playing', position_ms: audio.position_ms };
}

function visible(state) {
  return { session: publicCopy(state?.session || null), audio: localPlaybackState(state), connected: Boolean(state?.connected), loading: Boolean(state?.loading) };
}

function publish(state) {
  const payload = visible(state);
  const signature = JSON.stringify(payload);
  if (signature === lastSignature) return;
  lastSignature = signature;
  for (const listener of listeners) {
    try { listener(publicCopy(payload)); } catch { /* one consumer cannot break playback */ }
  }
}

export function setPlaybackController(controller) {
  if (current === controller) return;
  restore?.();
  restore = null;
  current = controller || null;
  lastSignature = null;
  if (current) {
    const target = current;
    const original = target.onChange;
    const wrapped = function (state) {
      try { original?.call(target, state); }
      finally { if (current === target) publish(state); }
    };
    target.onChange = wrapped;
    restore = () => { if (target.onChange === wrapped) target.onChange = original; };
  }
  publish(current?.getState?.());
}

export function getPlaybackController() { return current; }
export function playbackHeaders() { return { ...(current?.requestHeaders?.() || {}) }; }
export function subscribePlayback(listener) {
  listeners.add(listener);
  listener(visible(current?.getState?.()));
  return () => listeners.delete(listener);
}

/** Existing UI event handlers can attach to this facade, which opens no socket. */
export function createPlaybackFacade({ fetchStatus = () => {}, onConnected = () => {}, electron } = {}) {
  let unsubscribe, closed = false, previous = null;
  const facade = {
    readyState: 1,
    send(message) {
      if (closed) return;
      if (message === 'position_request') {
        const physical = localPlaybackState();
        if (physical) {
          const session = current.getState().session;
          const item = session.items?.[session.active_index || 0];
          facade._positionHandler?.({ elapsed_ms: physical.position_ms, position_ms: physical.position_ms,
            duration_ms: item?.duration_ms || 0, is_playing: physical.is_playing });
          return;
        }
        Promise.resolve(fetchStatus()).then(status => {
          if (closed || !status) return;
          const physical = localPlaybackState();
          const local = physical?.song_id === status.current_song?.id ? physical : null;
          const position = local?.position_ms ?? status.elapsed_ms ?? status.position_ms ?? 0;
          facade._positionHandler?.({ ...status, elapsed_ms: position, position_ms: position });
        }).catch(() => {});
      }
    },
    close() {
      if (closed) return;
      closed = true;
      facade.readyState = 3;
      unsubscribe?.();
      onConnected(false);
    },
  };
  unsubscribe = subscribePlayback(state => {
    if (closed) return;
    onConnected(state.connected);
    const session = state.session;
    if (!session) return;
    const item = session.items?.[session.active_index || 0];
    const before = previous?.items?.[previous.active_index || 0];
    const changedSong = item?.kind === 'song' && item.started &&
      (item.item_id !== before?.item_id || session.instance_id !== previous?.instance_id || !before?.started);
    const position = state.audio?.position_ms ?? session.position_ms ?? 0;
    const duration = item?.duration_ms || 0;
    if (changedSong) facade._songStartedHandler?.({ event: 'song_started', song_id: item.song_id, elapsed_ms: position });
    facade._positionHandler?.({ elapsed_ms: position, position_ms: position, duration_ms: duration,
      ...(state.audio ? { is_playing: state.audio.is_playing } : {}) });
    if (JSON.stringify(previous?.queue) !== JSON.stringify(session.queue)) facade._queueHandler?.({ queue: session.queue || [] });
    if (previous?.mood !== session.mood) facade._moodHandler?.(session.mood);
    if (JSON.stringify(previous?.vibe_lock) !== JSON.stringify(session.vibe_lock)) facade._vibeLockHandler?.(session.vibe_lock?.active ? session.vibe_lock : null);
    const preferences = session.preferences || session.context?.preferences || {};
    const priorPreferences = previous?.preferences || previous?.context?.preferences || {};
    if (preferences.play_mode !== priorPreferences.play_mode) facade._playModeHandler?.(preferences.play_mode);
    if (JSON.stringify(preferences.folder_filter) !== JSON.stringify(priorPreferences.folder_filter)) facade._recFilterHandler?.({ folder: preferences.folder_filter });
    if (priorPreferences.sleep_timer && !preferences.sleep_timer) facade._sleepTimerHandler?.(null);
    facade._rebuildHandler?.({ rebuilding: Boolean(session.queue_rebuilding || session.rebuilding) });
    if (item?.kind === 'speech' && item.started) facade._talkHandler?.(item);
    const logicalChanged = JSON.stringify(previous) !== JSON.stringify(session);
    previous = session;
    if (logicalChanged) Promise.resolve(fetchStatus()).then(status => {
      if (closed) return;
      if (changedSong && status?.current_song) electron?.updateNotification?.(status.current_song.artist || '', status.current_song.title || '');
    }).catch(() => {});
    electron?.updateTaskbarProgress?.(position, duration);
  });
  return facade;
}
