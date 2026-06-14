import React, { useState, useEffect, useRef, useCallback } from "react";

const REASONS = [
  { key: "too_intense", label: "Too intense?" },
  { key: "too_slow", label: "Too slow?" },
  { key: "too_samey", label: "Too samey?" },
  { key: "wrong_mood", label: "Wrong mood?" },
  { key: "just_skip", label: "Just skip it." },
];

const DURATIONS = [
  { key: "24h", label: "Skip for 24 hours" },
  { key: "session", label: "Skip this session" },
  { key: "permanent", label: "Never play this" },
];

function SkipFeedback({ visible, onReason, onDismiss }) {
  const [selectedReason, setSelectedReason] = useState(null);
  const [showDuration, setShowDuration] = useState(false);
  const pausedRef = useRef(false);
  const timerRef = useRef(null);
  const [countdownPct, setCountdownPct] = useState(100);
  const countdownIntervalRef = useRef(null);

  const startTimer = useCallback(() => {
    clearTimeout(timerRef.current);
    clearInterval(countdownIntervalRef.current);
    setCountdownPct(100);
    const totalMs = 10000;
    const startTime = Date.now();
    countdownIntervalRef.current = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, 1 - elapsed / totalMs);
      setCountdownPct(remaining * 100);
    }, 100);
    timerRef.current = setTimeout(() => {
      if (!pausedRef.current) {
        clearInterval(countdownIntervalRef.current);
        onDismiss();
      }
    }, totalMs);
  }, [onDismiss]);

  useEffect(() => {
    if (visible) {
      startTimer();
    } else {
      clearTimeout(timerRef.current);
      clearInterval(countdownIntervalRef.current);
      setSelectedReason(null);
      setShowDuration(false);
      setCountdownPct(100);
    }
    return () => {
      clearTimeout(timerRef.current);
      clearInterval(countdownIntervalRef.current);
    };
  }, [visible, startTimer]);

  const handleMouseEnter = () => {
    pausedRef.current = true;
    clearTimeout(timerRef.current);
  };

  const handleMouseLeave = () => {
    pausedRef.current = false;
    startTimer();
  };

  const handleReasonClick = (reason) => {
    setSelectedReason(reason);
    setShowDuration(true);
    pausedRef.current = true;
    clearTimeout(timerRef.current);
  };

  const handleDurationClick = (duration) => {
    onReason(selectedReason, duration);
    setSelectedReason(null);
    setShowDuration(false);
  };

  const handleDismiss = () => {
    onDismiss();
  };

  if (!visible) return null;

  return (
    <div className="fixed bottom-20 right-4 z-[99] max-w-xs fade-in"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}>
      <div
        className="rounded-xl px-4 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.4)] backdrop-blur-xl border"
        style={{
          background: "var(--bg-overlay)",
          borderColor: "var(--border)",
          borderLeft: "3px solid var(--accent)",
        }}
      >
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
            {!showDuration ? "What's not working?" : "How long?"}
          </p>
          <button onClick={handleDismiss}
            className="flex items-center justify-center rounded-md transition-colors"
            style={{ width: 20, height: 20, color: "var(--text-muted)" }}
            title="Dismiss">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {!showDuration ? (
          <>
            <div className="flex flex-col gap-1">
              {REASONS.map((r, idx) => (
                <button key={r.key} onClick={() => handleReasonClick(r.key)}
                  className="text-xs text-left rounded-lg px-3 py-1.5 transition-colors skip-opt-enter"
                  style={{
                    color: "var(--text-secondary)",
                    animationDelay: `${idx * 40}ms`,
                    background: "transparent",
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = "var(--accent-muted)"}
                  onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
                  {r.label}
                </button>
              ))}
            </div>
            <button onClick={handleDismiss}
              className="mt-2 text-xs transition-colors"
              style={{ color: "var(--text-muted)" }}
              onMouseEnter={(e) => e.currentTarget.style.color = "var(--text-secondary)"}
              onMouseLeave={(e) => e.currentTarget.style.color = "var(--text-muted)"}>
              Dismiss
            </button>
          </>
        ) : (
          <>
            <div className="flex flex-col gap-1">
              {DURATIONS.map((d) => (
                <button key={d.key} onClick={() => handleDurationClick(d.key)}
                  className="text-xs text-left rounded-lg px-3 py-1.5 transition-colors"
                  style={{
                    color: "var(--text-secondary)",
                    background: "transparent",
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = "var(--accent-muted)"}
                  onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
                  {d.label}
                </button>
              ))}
            </div>
            <button onClick={() => { setShowDuration(false); setSelectedReason(null); startTimer(); }}
              className="mt-2 text-xs transition-colors"
              style={{ color: "var(--text-muted)" }}
              onMouseEnter={(e) => e.currentTarget.style.color = "var(--text-secondary)"}
              onMouseLeave={(e) => e.currentTarget.style.color = "var(--text-muted)"}>
              ← Back to reasons
            </button>
          </>
        )}
      </div>
      {!showDuration && (
        <div style={{ marginTop: 8, height: 2, borderRadius: 1, background: "var(--border)", overflow: "hidden" }}>
          <div style={{
            height: "100%", borderRadius: 1,
            background: "var(--accent)",
            width: `${countdownPct}%`,
            transition: "width 100ms linear",
          }} />
        </div>
      )}
    </div>
  );
}

export default SkipFeedback;
