import React from "react";
import { X } from "lucide-react";

const SHORTCUTS = [
  { keys: ["Space"], action: "Play / Pause" },
  { keys: ["N"], action: "Next song" },
  { keys: ["H"], action: "Heart current song" },
  { keys: ["D"], action: "Dislike current song" },
  { keys: ["R"], action: "Replay current song" },
  { keys: ["V"], action: "Toggle vibe lock" },
  { keys: ["M"], action: "Cycle mood" },
  { keys: ["/"], action: "Focus command bar" },
  { keys: ["?"], action: "Show this panel" },
  { keys: ["↑", "↓"], action: "Volume up / down" },
  { keys: ["←", "→"], action: "Seek ±10s" },
  { keys: ["Esc"], action: "Close panel" },
];

export default function ShortcutsModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-gray-900 rounded-xl w-full max-w-sm mx-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 sticky top-0 bg-gray-900">
          <h2 className="text-base font-semibold text-white">Keyboard Shortcuts</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition"><X size={16} /></button>
        </div>
        <div className="px-5 py-4 space-y-2.5">
          {SHORTCUTS.map((s, i) => (
            <div key={i} className="flex items-center justify-between">
              <span className="text-sm text-gray-400">{s.action}</span>
              <div className="flex gap-1">
                {s.keys.map((k) => (
                  <kbd key={k} className="px-2 py-0.5 rounded bg-gray-800 text-xs text-gray-300 font-mono">{k}</kbd>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
