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
    let timerId = null;

    const poll = () => {
      if (Date.now() - startTime >= TIMEOUT_MS) {
        setError("Backend unreachable — check if server is running");
        return;
      }

      fetch(HEALTH_URL, { signal: AbortSignal.timeout(3000) })
        .then((r) => {
          if (r.ok) {
            setReady(true);
            setProgress(100);
            if (timerId) clearTimeout(timerId);
          } else {
            throw new Error("not ready");
          }
        })
        .catch(() => {
          setProgress(Math.min(90, ((Date.now() - startTime) / TIMEOUT_MS) * 90));
        });
    };

    intervalId = setInterval(poll, POLL_INTERVAL);
    poll();

    return () => {
      if (intervalId) clearInterval(intervalId);
      if (timerId) clearTimeout(timerId);
    };
  }, []);

  useEffect(() => {
    start();
  }, [start]);

  return { ready, error, progress, start };
}
