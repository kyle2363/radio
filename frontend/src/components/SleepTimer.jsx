import React, { useState, useEffect } from "react";
import { Moon, Plus } from "lucide-react";
import HeaderButton from "./HeaderButton";

function formatTime(secs) {
  if (!secs || secs <= 0) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const PRESETS = [
  { label: "15m", value: 15 },
  { label: "30m", value: 30 },
  { label: "60m", value: 60 },
  { label: "90m", value: 90 },
];

function SleepTimer({ timer, onSet, onCancel }) {
  const [showSet, setShowSet] = useState(false);

  if (!timer?.active) {
    return (
      <div className="flex items-center gap-1" style={{ position: "relative" }}>
        <HeaderButton onClick={() => setShowSet(!showSet)} title="Set sleep timer">
          <Moon size={18} />
        </HeaderButton>
        {showSet && (
          <div style={{
            position: "absolute", top: "100%", right: 0, marginTop: 4, zIndex: 30,
            background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6,
            padding: 4, display: "flex", gap: 2, boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
          }}>
            {PRESETS.map((p) => (
              <button key={p.value} onClick={() => { onSet?.(p.value); setShowSet(false); }}
                style={{
                  fontSize: 10, padding: "4px 8px", borderRadius: 4, cursor: "pointer",
                  background: "var(--bg-elevated)", color: "var(--text-secondary)",
                  border: "1px solid var(--border-subtle)", fontWeight: 500,
                }}>
                {p.label}
              </button>
            ))}
            <button onClick={() => setShowSet(false)}
              style={{ fontSize: 10, padding: "4px 8px", borderRadius: 4, cursor: "pointer", background: "none", border: "none", color: "var(--text-muted)" }}>
              ✕
            </button>
          </div>
        )}
      </div>
    );
  }

  const remaining = timer.remaining_seconds || 0;
  const total = timer.total_seconds || 1;
  const pct = Math.max(0, Math.min(1, remaining / total));
  const radius = 8;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - pct);

  const color = remaining < 60 ? "var(--danger)" : remaining < 300 ? "var(--warning)" : "var(--accent)";

  return (
    <div className="flex items-center gap-2 text-xs" style={{ color, cursor: "default" }}
      title={`${Math.floor(remaining / 60)}m ${remaining % 60}s remaining`}>
      <svg width="22" height="22" viewBox="0 0 22 22" style={{ flexShrink: 0 }}>
        <circle cx="11" cy="11" r={radius} fill="none" stroke="var(--border)" strokeWidth="2.5" />
        <circle cx="11" cy="11" r={radius} fill="none" stroke={color} strokeWidth="2.5"
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round" transform="rotate(-90 11 11)"
          style={{ transition: "stroke-dashoffset 1s linear, stroke 0.5s" }} />
      </svg>
      <span className="tabular-nums" style={{ minWidth: 32 }}>{formatTime(remaining)}</span>
      {timer.fading && <span style={{ fontSize: 10, color: "var(--warning)" }}>fading</span>}
      <button onClick={onCancel}
        style={{ color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: 12, lineHeight: 1, transition: "color 0.15s" }}
        onMouseEnter={(e) => e.target.style.color = "var(--danger)"}
        onMouseLeave={(e) => e.target.style.color = "var(--text-muted)"}>
        ✕
      </button>
    </div>
  );
}

export default SleepTimer;