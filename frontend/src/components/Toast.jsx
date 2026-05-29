import React, { useState, useEffect, useRef, useCallback } from "react";

function Toast({ message, type = "error", progress, onClose }) {
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

  const bg = type === "error"
    ? "bg-red-500/90 border-red-400"
    : type === "success"
      ? "bg-[#4eccc4]/90 border-[#4eccc4]/60"
      : "bg-blue-500/90 border-blue-400";

  return (
    <div className={`${bg} border rounded-xl px-5 py-3.5 shadow-lg backdrop-blur-sm transition-all duration-300 ${visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2"}`}
      onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      <div className="flex items-center gap-2">
        {type === "error" && (
          <svg className="w-4 h-4 text-white flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        )}
        {type === "success" && (
          <svg className="w-4 h-4 text-white flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        )}
        <p className="text-sm text-white flex-1">{message}</p>
        {progress != null && (
          <span className="text-xs text-white/70 tabular-nums">{Math.round(progress)}%</span>
        )}
      </div>
      {progress != null && (
        <div className="mt-2 h-1 bg-white/20 rounded-full overflow-hidden">
          <div
            className="h-full bg-white rounded-full transition-all duration-300"
            style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
          />
        </div>
      )}
    </div>
  );
}

export default function ToastContainer({ toasts, onRemove }) {
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {toasts.map((toast) => (
        <Toast
          key={toast.id}
          message={toast.message}
          type={toast.type}
          progress={toast.progress}
          onClose={() => onRemove(toast.id)}
        />
      ))}
    </div>
  );
}
