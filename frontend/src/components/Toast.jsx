import React, { useState, useEffect, useRef, useCallback } from "react";

const DOT_COLORS = {
  error: "var(--danger)",
  success: "var(--accent)",
  info: "var(--info)",
  action: "var(--accent)",
};

function Toast({ message, type = "error", progress, onClose, actionLabel, onAction }) {
  const [visible, setVisible] = useState(true);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const timerRef = useRef(null);
  const pausedRef = useRef(false);

  const startTimer = useCallback(() => {
    if (progress != null) return;
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      if (!pausedRef.current) {
        setVisible(false);
        setTimeout(() => onCloseRef.current(), 300);
      }
    }, 4000);
  }, [progress]);

  useEffect(() => {
    startTimer();
    return () => clearTimeout(timerRef.current);
  }, [startTimer]);

  const handleMouseEnter = () => {
    pausedRef.current = true;
    clearTimeout(timerRef.current);
  };

  const handleMouseLeave = () => {
    pausedRef.current = false;
    startTimer();
  };

  const iconMap = {
    error: (
      <svg className="w-4 h-4 text-[var(--danger)] flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    success: (
      <svg className="w-4 h-4 text-[var(--accent)] flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    ),
    info: (
      <svg className="w-4 h-4 text-[var(--info)] flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    action: (
      <svg className="w-4 h-4 text-[var(--accent)] flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  };

  return (
    <div className={`rounded-lg px-4 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.4)] backdrop-blur-xl border border-[var(--border)] transition-all duration-300 ${visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2"}`}
      onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}
      style={{ background: "var(--bg-overlay)" }}>
      <div className="flex items-center gap-2.5">
        <div style={{ width: 6, height: 6, borderRadius: "50%", background: DOT_COLORS[type], flexShrink: 0 }} />
        {iconMap[type]}
        <p className="text-sm text-[var(--text-primary)] flex-1 leading-snug">{message}</p>
        {progress != null && (
          <span className="text-xs text-[var(--text-muted)] tabular-nums flex-shrink-0">{Math.round(progress)}%</span>
        )}
        {actionLabel && onAction && (
          <button
            onClick={() => { onAction(); setVisible(false); setTimeout(() => onCloseRef.current(), 300); }}
            className="text-xs font-semibold text-[var(--accent)] bg-[var(--accent-muted)] hover:bg-[var(--accent-hover)]/20 rounded-md px-2.5 py-1 transition-colors flex-shrink-0"
          >
            {actionLabel}
          </button>
        )}
      </div>
      {progress != null && (
        <div className="mt-2 h-[3px] rounded-full overflow-hidden" style={{ background: "var(--border)" }}>
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{ width: `${Math.min(100, Math.max(0, progress))}%`, background: "var(--accent)" }}
          />
        </div>
      )}
    </div>
  );
}

export default function ToastContainer({ toasts, onRemove }) {
  const [removingIds, setRemovingIds] = useState(new Set());

  const handleRemove = useCallback((id) => {
    setRemovingIds((prev) => new Set(prev).add(id));
    setTimeout(() => {
      onRemove(id);
      setRemovingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }, 300);
  }, [onRemove]);

  // Cap at 5 visible toasts
  const visibleToasts = toasts.slice(0, 5);

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {visibleToasts.map((toast) => {
        const isRemoving = removingIds.has(toast.id);
        return (
          <div
            key={toast.id}
            className="transition-all duration-300 ease-out overflow-hidden"
            style={{
              maxHeight: isRemoving ? 0 : 120,
              opacity: isRemoving ? 0 : 1,
              marginBottom: isRemoving ? "-8px" : undefined,
            }}
          >
            <Toast
              message={toast.message}
              type={toast.type}
              progress={toast.progress}
              actionLabel={toast.actionLabel}
              onAction={toast.onAction}
              onClose={() => handleRemove(toast.id)}
            />
          </div>
        );
      })}
    </div>
  );
}
