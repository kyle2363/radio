import React from "react";
import { X } from "lucide-react";
import useFocusTrap from "../hooks/useFocusTrap";

const GROUPS = [
  { title: "Playback", items: [
    { keys: ["Space"], action: "Play / pause" },
    { keys: ["N"], action: "Next song" },
    { keys: ["R"], action: "Replay current song" },
    { keys: ["←", "→"], action: "Seek ±10 s" },
    { keys: ["↑", "↓"], action: "Volume up / down" },
  ] },
  { title: "Radio", items: [
    { keys: ["H"], action: "Heart current song" },
    { keys: ["D"], action: "Dislike current song" },
    { keys: ["V"], action: "Toggle vibe lock" },
    { keys: ["M"], action: "Cycle mood" },
  ] },
  { title: "Commands", items: [
    { keys: ["/"], action: "Ask the DJ" },
    { keys: ["Ctrl", "K"], action: "Search songs" },
    { keys: ["Ctrl", "Shift", "K"], action: "Command palette" },
    { keys: ["Ctrl", "Z"], action: "Undo last command" },
  ] },
  { title: "Window", items: [
    { keys: ["?"], action: "Show this panel" },
    { keys: ["Esc"], action: "Close panel" },
  ] },
];

export default function ShortcutsModal({ isOpen, onClose }) {
  const ref = useFocusTrap(isOpen);
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div ref={ref} className="relative w-full max-w-md rounded-2xl shadow-2xl"
        style={{ animation: "scaleIn 150ms ease", background: "var(--bg-surface)", border: "1px solid var(--border)", margin: "0 16px" }}>
        <div className="flex items-center justify-between" style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)" }}>
          <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>Keyboard shortcuts</h2>
          <button type="button" onClick={onClose} className="ui-btn sm" style={{ padding: 5 }} aria-label="Close"><X size={14} /></button>
        </div>
        <div className="grid gap-5" style={{ gridTemplateColumns: "1fr 1fr", padding: "16px 20px 20px" }}>
          {GROUPS.map((group) => (
            <div key={group.title}>
              <div className="card-title" style={{ marginBottom: 6 }}>{group.title}</div>
              <div className="flex flex-col gap-1.5">
                {group.items.map((s) => (
                  <div key={s.action} className="flex items-center justify-between gap-2">
                    <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{s.action}</span>
                    <span className="flex gap-0.5 flex-shrink-0">
                      {s.keys.map((k) => (
                        <kbd key={k} className="rounded text-[10px] font-mono"
                          style={{ padding: "1px 6px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>{k}</kbd>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
