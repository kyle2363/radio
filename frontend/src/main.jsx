import React, { useState, useEffect, useRef } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import BootScreen from "./components/BootScreen";
import "./styles/global.css";

const HEALTH_URL = "/api/health";
const POLL_INTERVAL = 500;
const TIMEOUT_MS = 30000;
const FADE_DELAY_MS = 1000;
const FADE_DURATION_MS = 400;

function AppWithBoot() {
  const [ready, setReady] = useState(false);
  const [fading, setFading] = useState(false);
  const [faded, setFaded] = useState(false);
  const [error, setError] = useState(null);
  const [bootProgress, setBootProgress] = useState(0);
  const shouldPoll = useRef(true);

  useEffect(() => {
    const startTime = Date.now();
    const interval = setInterval(() => {
      if (Date.now() - startTime >= TIMEOUT_MS) {
        setError("Backend unreachable — check if server is running");
        return;
      }
      setBootProgress(Math.min(85, ((Date.now() - startTime) / TIMEOUT_MS) * 85));
    }, POLL_INTERVAL);

    const poll = () => {
      if (!shouldPoll.current) return;
      fetch(HEALTH_URL, { signal: AbortSignal.timeout(3000) })
        .then((r) => {
          if (r.ok) {
            setReady(true);
            setBootProgress(100);
            clearInterval(interval);
            setTimeout(() => {
              setFading(true);
              setTimeout(() => {
                setFaded(true);
              }, FADE_DURATION_MS);
            }, FADE_DELAY_MS);
          } else {
            throw new Error("not ready");
          }
        })
        .catch(() => {
          if (Date.now() - startTime < TIMEOUT_MS && shouldPoll.current) {
            clearInterval(interval);
          }
        });
    };

    poll();
    const pollTimer = setInterval(poll, POLL_INTERVAL);

    return () => {
      shouldPoll.current = false;
      clearInterval(interval);
      clearInterval(pollTimer);
    };
  }, []);

  useEffect(() => {
    if (faded) {
      shouldPoll.current = false;
    }
  }, [faded]);

  if (error) {
    return <BootScreen onError={error} onReady={() => setError(null)} initialProgress={bootProgress} />;
  }

  return (
    <React.StrictMode>
      <ErrorBoundary>
        <div style={{ position: "relative", width: "100vw", height: "100vh" }}>
          {/* Boot screen — fades out during transition */}
          {!faded && (
            <div style={{
              position: "absolute", inset: 0, zIndex: 10,
              opacity: fading ? 0 : 1,
              transition: "opacity 400ms ease-in",
              pointerEvents: fading ? "none" : "auto",
            }}>
              <BootScreen initialProgress={bootProgress} />
            </div>
          )}
          {/* App — fades in during transition, fully visible after */}
          <div style={{
            opacity: fading ? 1 : 0,
            transition: fading ? "opacity 400ms ease-out" : "none",
          }}>
            <App />
          </div>
        </div>
      </ErrorBoundary>
    </React.StrictMode>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<AppWithBoot />);
