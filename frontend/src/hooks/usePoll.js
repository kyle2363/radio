import { useRef, useCallback, useEffect } from "react";

/**
 * Coordinated polling hook that cancels stale in-flight requests
 * using AbortController. Prevents race conditions where a slow
 * response from poll N overwrites fresh data from poll N+1.
 *
 * Usage:
 *   const { start, stop, abort } = usePoll();
 *   const pollQueue = useCallback(() => start(api.fetchQueueStatus()), [start]);
 *   const pollStatus = useCallback(() => start(api.fetchPlayStatus()), [start]);
 */
export default function usePoll() {
  const abortRef = useRef(null);

  const start = useCallback((fetchFn, signal) => {
    // Cancel any in-flight request
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;

    const sig = signal || controller.signal;
    return fetchFn(sig).catch((e) => {
      if (e.name === "AbortError" || e.message.includes("timed out")) {
        // Silently ignore aborted requests
        return null;
      }
      throw e;
    });
  }, []);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

  const stop = abort;

  return { start, stop, abort };
}
