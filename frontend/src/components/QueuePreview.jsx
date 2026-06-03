import React, { useState } from "react";
import { RefreshCw, Mic } from "lucide-react";
import * as api from "../api";
import { SkeletonQueueCompact } from "./Skeleton";

const SLOT_STYLES = {
  exploit: { cls: "exploit", label: "exploit" },
  explore: { cls: "explore", label: "explore" },
  wildcard: { cls: "wildcard", label: "wildcard" },
  context: { cls: "context", label: "context" },
  fallback: { cls: "fallback", label: "fallback" },
  recovery: { cls: "recovery", label: "recovery" },
  manual: { cls: "manual", label: "manual" },
};

const QueuePreview = React.memo(function QueuePreview({ queue, isRebuilding, onPromoteSong, onRebuild, compact = false, queueLoading = false }) {
  const [rebuilding, setRebuilding] = useState(false);

  const handleRebuild = async () => {
    setRebuilding(true);
    try { await onRebuild?.(); } catch (e) {}
    setTimeout(() => setRebuilding(false), 2000);
  };

  const count = queue?.length || 0;

  const sectionHeader = (
    <div className="flex items-center gap-2 mb-3" style={
      compact ? { fontSize: "9px", letterSpacing: ".1em", color: "#2dd4a0", fontWeight: 700, marginBottom: "6px", textTransform: "uppercase", display: "flex", alignItems: "center", justifyContent: "space-between" }
      : { fontSize: "10.5px", fontWeight: 600, letterSpacing: ".08em", color: "var(--text-muted)", textTransform: "uppercase" }
    }>
      <span>Up Next</span>
      <div style={{ display: "flex", alignItems: "center", gap: compact ? 5 : 2 }}>
        {count > 0 && (
          <span style={{ fontSize: 9, padding: "1px 4px", borderRadius: 3, background: "rgba(255,255,255,0.05)", color: "var(--text-muted)" }}>{count}</span>
        )}
        <button onClick={handleRebuild} disabled={rebuilding} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, color: "var(--text-muted)" }}>
          <RefreshCw size={compact ? 12 : 14} className={rebuilding ? "animate-spin" : ""} />
        </button>
      </div>
    </div>
  );

  if (isRebuilding || (queueLoading && count === 0)) {
    if (compact) {
      return (
        <div>
          {sectionHeader}
          <SkeletonQueueCompact count={5} />
        </div>
      );
    }
    return (
      <div className="rounded-xl p-5" style={{ background: "var(--bg-surface)" }}>
        {sectionHeader}
        <div className="flex items-center justify-center gap-2 py-8">
          <RefreshCw size={14} className="animate-spin" style={{ color: "var(--accent)" }} />
          <span className="text-sm" style={{ color: "var(--text-secondary)" }}>Building queue...</span>
        </div>
      </div>
    );
  }

  const emptyState = !queue || count === 0 ? (
    <div style={{ textAlign: "center", padding: compact ? "12px 0" : "16px 0" }}>
      <p style={{ fontSize: compact ? 11 : 13, marginBottom: 6, color: "var(--text-muted)" }}>Queue is empty</p>
      <button onClick={handleRebuild} disabled={rebuilding}
        style={{ padding: "4px 14px", borderRadius: "4px", fontSize: 10, fontWeight: 500, border: "none", cursor: "pointer", background: "var(--accent-muted)", color: "var(--accent)" }}>
        {rebuilding ? "Building..." : "Rebuild"}
      </button>
    </div>
  ) : null;

  const queueCards = queue && count > 0 ? (
    <div style={compact ? {} : { spaceY: "0.5" }}>
      {queue.slice(0, 5).map((item, idx) => {
        const slot = SLOT_STYLES[item.slot] || SLOT_STYLES.fallback;
        const isTalk = item.type === "talk_segment";

        if (isTalk) {
          const segLabels = {
            caller: "Caller on the line",
            dj_monologue: "DJ Monologue",
            guest_takeover_intro: "Guest takeover",
            guest_takeover_outro: "Handing back",
          };
          return (
            <div key={`talk-${idx}`} style={{
              display: "flex", alignItems: "flex-start", gap: 6, padding: "6px 8px",
              borderLeft: "2px solid rgba(168,112,240,0.3)", opacity: 0.7,
              borderTop: compact ? "1px solid var(--border-subtle)" : "none",
              borderRight: compact ? "1px solid var(--border-subtle)" : "none",
              borderBottom: compact ? "1px solid var(--border-subtle)" : "none",
              borderRadius: compact ? 0 : 8,
            }}>
              <Mic size={14} style={{ color: "var(--accent)", flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 11, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--accent)" }}>
                  {segLabels[item.segment_type] || "Talk segment"}
                </div>
                {item.reason && (
                  <div style={{ fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 1, color: "var(--text-muted)" }}>{item.reason}</div>
                )}
              </div>
            </div>
          );
        }

        const borderColor = item.vibe_affected ? "rgba(168,112,240,0.5)" : undefined;

        if (compact) {
          const slotColors = { exploit: "#2dd4a0", explore: "#5eadf5", wildcard: "#a78bfa", context: "#f59e0b", fallback: "var(--border-strong)", recovery: "#f09060", manual: "#4eccc4" };
          const slotBgs = { exploit: "#0a2418", explore: "#09142a", wildcard: "#100728", context: "#161008", fallback: "rgba(255,255,255,0.05)", recovery: "#2a1a0a", manual: "#0a2a28" };
          return (
            <div key={item.song_id} onClick={() => onPromoteSong?.(item.song_id)} className="qc-compact" style={{
              borderLeftColor: slotColors[item.slot] || "var(--border-strong)",
              borderLeft: item.vibe_affected ? "3px solid rgba(168,112,240,0.5)" : undefined,
              cursor: "pointer",
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 5, marginBottom: 2 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                  {item.artist} – {item.title}
                </span>
                <span style={{ fontSize: 8, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", padding: "1px 4px", borderRadius: 3, background: slotBgs[item.slot] || "rgba(255,255,255,0.05)", color: slotColors[item.slot] || "var(--text-muted)", flexShrink: 0 }}>
                  {slot.label}
                </span>
              </div>
              {item.reason && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginBottom: 4 }}>{item.reason}</div>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                  <span style={{ fontSize: 9, color: "var(--text-muted)" }}>E</span>
                  <div className="qb"><div className="qb-fill" style={{ width: `${Math.min(100, Math.round((item.energy || 0.5) * 100))}%` }} /></div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                  <span style={{ fontSize: 9, color: "var(--text-muted)" }}>V</span>
                  <div className="qb"><div className="qb-fill valence" style={{ width: `${Math.min(100, Math.round((item.valence || 0.5) * 100))}%` }} /></div>
                </div>
                {item.bpm > 0 && (
                  <span style={{ fontSize: 9, color: "var(--text-muted)", marginLeft: "auto" }}>{item.bpm} BPM</span>
                )}
              </div>
            </div>
          );
        }

        return (
          <div key={item.song_id} onClick={() => onPromoteSong?.(item.song_id)}
            className={`queue-card ${slot.cls}`}
            style={{
              borderBottom: idx < Math.min(queue.length, 5) - 1 ? "1px solid var(--border-subtle)" : "none",
              borderLeft: item.vibe_affected ? "3px solid rgba(168,112,240,0.5)" : undefined,
              cursor: "pointer",
              marginBottom: "2px",
            }}>
            <div className="flex items-center gap-2" style={{ marginBottom: "3px" }}>
              <div className="flex-1 min-w-0">
                <span className="text-xs font-medium truncate block" style={{ color: "var(--text-primary)" }}>
                  {item.artist} – {item.title}
                </span>
              </div>
              <span className={`slot-badge ${slot.cls}`}>{slot.label}</span>
            </div>
            {item.reason && (
              <div className="text-[11px] truncate" style={{ color: "var(--text-muted)" }}>{item.reason}</div>
            )}
          </div>
        );
      })}
    </div>
  ) : null;

  if (compact) {
    return (
      <div>
        {sectionHeader}
        {emptyState || queueCards}
      </div>
    );
  }

  return (
    <div className="rounded-xl p-5" style={{ background: "var(--bg-surface)" }}>
      {sectionHeader}
      {emptyState || queueCards}
    </div>
  );
});

export default QueuePreview;
