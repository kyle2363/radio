import React from "react";
import { Moon } from "lucide-react";

function formatTime(secs) {
  if (!secs || secs <= 0) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function SleepTimer({ timer, onSet, onCancel }) {
  if (timer?.active) {
    return (
      <div className="flex items-center gap-2 text-xs" style={{ color: "var(--accent)" }}>
        <Moon size={12} />
        <span className="tabular-nums">{formatTime(timer.remaining_seconds)}</span>
        {timer.fading && <span className="text-yellow-400">fading</span>}
        <button
          onClick={onCancel}
          className="text-[var(--text-muted)] hover:text-red-400 transition-colors ml-1"
        >
          ✕
        </button>
      </div>
    );
  }

  return null;
}

export default SleepTimer;