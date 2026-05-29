import React, { useState, useEffect, useRef } from "react";
import { RefreshCw, Mic, Check } from "lucide-react";
import * as api from "../api";

const SLOT_STYLES = {
  exploit: { bg: "rgba(80,200,120,0.1)", color: "#4ade80", label: "exploit" },
  explore: { bg: "rgba(80,140,255,0.1)", color: "#60a0f0", label: "explore" },
  wildcard: { bg: "rgba(180,80,255,0.1)", color: "#c060f0", label: "wildcard" },
  context: { bg: "rgba(240,176,96,0.1)", color: "#f0b060", label: "context" },
  fallback: { bg: "rgba(255,255,255,0.05)", color: "var(--text-muted)", label: "fallback" },
  recovery: { bg: "rgba(240,144,96,0.1)", color: "#f09060", label: "recovery" },
  manual: { bg: "rgba(78,204,196,0.1)", color: "#4eccc4", label: "manual" },
};

const QueuePreview = React.memo(function QueuePreview({ queue, isRebuilding, onPromoteSong }) {
  const [rebuilding, setRebuilding] = useState(false);
  const [queueReady, setQueueReady] = useState(false);
  const prevQueueRef = useRef(queue);

  // Flash "ready" indicator when queue changes (proactive rebuild completed)
  useEffect(() => {
    if (prevQueueRef.current !== queue && queue && queue.length > 0 && !isRebuilding) {
      setQueueReady(true);
      // Fade out after 1.5s, remove after 2s
      const fadeTimer = setTimeout(() => setQueueReady("fading"), 1500);
      const removeTimer = setTimeout(() => setQueueReady(false), 2000);
      prevQueueRef.current = queue;
      return () => { clearTimeout(fadeTimer); clearTimeout(removeTimer); };
    }
    prevQueueRef.current = queue;
  }, [queue, isRebuilding]);

  const handleRebuild = async () => {
    setRebuilding(true);
    try { await api.rebuildQueue(); } catch (e) {}
    setTimeout(() => setRebuilding(false), 2000);
  };

  if (isRebuilding) {
    return (
      <div className="rounded-xl p-5" style={{ background: "var(--bg-surface)" }}>
        <div className="flex items-center gap-2 mb-3">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Up Next</span>
        </div>
        <div className="flex items-center justify-center gap-2 py-8">
          <RefreshCw size={14} className="animate-spin" style={{ color: "var(--accent)" }} />
          <span className="text-sm" style={{ color: "var(--text-secondary)" }}>Building queue...</span>
        </div>
      </div>
    );
  }

  const count = queue?.length || 0;

  return (
    <div className="rounded-xl p-5" style={{ background: "var(--bg-surface)" }}>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Up Next</span>
        {queueReady && (
          <span className="inline-flex items-center gap-0.5 text-[10px] font-medium px-1.5 py-0.5 rounded-full"
            style={{
              background: "rgba(72,187,120,0.1)",
              color: "#4ade80",
              border: "1px solid rgba(72,187,120,0.2)",
              opacity: queueReady === "fading" ? 0 : 1,
              transition: "opacity 500ms ease-out",
            }}>
            <Check size={9} />
            Ready
          </span>
        )}
        {count > 0 && !queueReady && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "rgba(255,255,255,0.05)", color: "var(--text-muted)" }}>{count}</span>
        )}
        <button onClick={handleRebuild} disabled={rebuilding} className="ml-auto transition link-accent">
          <RefreshCw size={14} className={rebuilding ? "animate-spin" : ""} />
        </button>
      </div>

      {!queue || count === 0 ? (
        <div className="text-center py-4">
          <p className="text-sm mb-2" style={{ color: "var(--text-muted)" }}>Queue is empty</p>
            <button onClick={handleRebuild} disabled={rebuilding} className="px-5 py-2 rounded-lg text-xs font-medium transition"
            style={{ background: "var(--accent-muted)", color: "var(--accent)" }}>
            {rebuilding ? "Building..." : "Rebuild queue \u2192"}
          </button>
        </div>
      ) : (
        <div className="space-y-0.5">
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
                <div key={idx} className="flex items-start gap-2.5 py-2 rounded" style={{
                  borderLeft: "2px solid rgba(168,112,240,0.2)",
                  opacity: 0.7,
                  borderBottom: idx < Math.min(queue.length, 5) - 1 ? "1px solid var(--border-subtle)" : "none",
                }}>
                  <div className="flex-shrink-0 w-[52px] flex justify-center mt-0.5">
                    <Mic size={14} style={{ color: "var(--accent)" }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium truncate" style={{ color: "var(--accent)" }}>
                      {segLabels[item.segment_type] || "Talk segment"}
                    </div>
                    {item.reason && (
                      <div className="text-[11px] truncate mt-0.5" style={{ color: "var(--text-muted)" }}>{item.reason}</div>
                    )}
                  </div>
                </div>
              );
            }

            return (
              <div key={idx} onClick={() => onPromoteSong?.(item.song_id)} className="flex items-start gap-2.5 py-2 cursor-pointer rounded transition group row-hover" style={{
                background: item.vibe_affected ? "rgba(168,112,240,0.04)" : "transparent",
                borderLeft: item.vibe_affected ? "2px solid rgba(168,112,240,0.3)" : "2px solid transparent",
                borderBottom: idx < Math.min(queue.length, 5) - 1 ? "1px solid var(--border-subtle)" : "none",
              }}>
                <span className="text-[9.5px] font-semibold px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 min-w-[52px] text-center"
                  style={{ background: slot.bg, color: slot.color, letterSpacing: "0.04em" }}>
                  {slot.label}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate flex items-center gap-2" style={{ color: "var(--text-secondary)" }}>
                    <span className="truncate">{item.artist} – {item.title}</span>
                    <span className="text-[10px] opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 ml-auto" style={{ color: "var(--accent)" }}>next</span>
                  </div>
                  {item.reason && (
                    <div className="text-[11px] truncate mt-0.5" style={{ color: "var(--text-muted)" }}>{item.reason}</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

export default QueuePreview;
