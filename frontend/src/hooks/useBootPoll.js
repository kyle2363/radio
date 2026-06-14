import { useState, useEffect, useCallback } from "react";

const HEALTH_URL = "/api/health";
const POLL_INTERVAL = 500;
const MAX_BACKOFF = 3000;
const TIMEOUT_MS = 30000;

export default function useBootPoll() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState(0);

  const start = useCallback(() => {
    setReady(false);
    setError(null);
    setProgress(0);

    const startTime = Date.now();
    let intervalId = null;
    let cancelled = false;

    const poll = () => {
      if (cancelled) return;
      if (Date.now() - startTime >= TIMEOUT_MS) {
        setError("Backend unreachable — check if server is running");
        if (intervalId) clearInterval(intervalId);
        intervalId = null;
        return;
      }

      fetch(HEALTH_URL, { signal: AbortSignal.timeout(3000) })
        .then((r) => {
          if (cancelled) return;
          if (r.ok) {
            setReady(true);
            setProgress(100);
            if (intervalId) clearInterval(intervalId);
            intervalId = null;
          } else {
            throw new Error("not ready");
          }
        })
        .catch(() => {
          if (!cancelled) {
            setProgress(Math.min(90, ((Date.now() - startTime) / TIMEOUT_MS) * 90));
          }
        });
    };

    intervalId = setInterval(poll, POLL_INTERVAL);
    poll();

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      intervalId = null;
    };
  }, []);

  useEffect(() => {
    start();
  }, [start]);

  return { ready, error, progress, start };
}
