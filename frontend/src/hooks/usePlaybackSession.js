import { useCallback, useEffect, useRef, useState } from 'react';
import { createPlaybackApi, PlaybackSessionController } from '../api/playback.js';

/** Mount once near the app root after API authentication succeeds. */
export function usePlaybackSession({ apiBase = '', wsUrl, enabled = true, onUnauthorized, onController } = {}) {
  const controllerRef = useRef(null);
  const callbackRef = useRef({ onUnauthorized, onController });
  callbackRef.current = { onUnauthorized, onController };
  const [state, setState] = useState({ session: null, audio: { status: 'idle' }, loading: true, connected: false, error: null });
  useEffect(() => {
    if (!enabled) return;
    const controller = new PlaybackSessionController({ apiBase, wsUrl,
      api: createPlaybackApi({ baseUrl: apiBase, onUnauthorized: () => callbackRef.current.onUnauthorized?.() }),
      onChange: setState });
    controllerRef.current = controller;
    callbackRef.current.onController?.(controller);
    controller.start().catch(() => {});
    return () => {
      callbackRef.current.onController?.(null);
      controllerRef.current = null;
      controller.dispose();
    };
  }, [apiBase, wsUrl, enabled]);
  const invoke = useCallback((method, ...args) => {
    const controller = controllerRef.current;
    if (!controller) return Promise.reject(new Error('Playback session is not ready.'));
    return Promise.resolve(controller[method](...args)).catch(error => { controller.fail(error); throw error; });
  }, []);
  return { ...state, controllerRef,
    retry: useCallback(() => invoke('retry'), [invoke]),
    play: useCallback(songId => invoke('play', songId), [invoke]),
    activate: useCallback(() => invoke('activate'), [invoke]),
    reactivate: useCallback(() => invoke('reactivate'), [invoke]),
    switchOutput: useCallback(mode => invoke('switchOutput', mode), [invoke]),
    retryHandoff: useCallback(() => invoke('retryHandoff'), [invoke]),
    routeLegacy: useCallback((path, options) => invoke('routeLegacy', path, options), [invoke]),
  };
}

export default usePlaybackSession;
