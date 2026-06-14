import React, { useState, useEffect, useRef, useMemo } from "react";
import { Terminal } from "lucide-react";
import * as api from "../api";
import { SLOT_GUIDE } from "../content/wiki";
import useFocusTrap from "../hooks/useFocusTrap";

const COMMAND_CATEGORIES = [
  {
    name: "Mood & Vibe",
    commands: [
      { text: "chill vibes", desc: "Switch to chill mood" },
      { text: "energetic", desc: "Switch to energetic mood" },
      { text: "focus mode", desc: "Switch to focus mood" },
      { text: "melancholy", desc: "Switch to melancholy mood" },
      { text: "discover something new", desc: "Switch to discovery mood" },
      { text: "neutral", desc: "Reset to neutral mood" },
      { text: "vibe lock", desc: "Lock current vibe" },
      { text: "unlock", desc: "Unlock vibe lock" },
    ],
  },
  {
    name: "Playback",
    commands: [
      { text: "pause", desc: "Pause playback" },
      { text: "resume", desc: "Resume playback" },
      { text: "skip", desc: "Skip to next song" },
      { text: "next", desc: "Skip to next song" },
      { text: "replay", desc: "Replay current song" },
      { text: "again", desc: "Replay current song" },
      { text: "stop", desc: "Stop playback" },
      { text: "volume up", desc: "Turn volume up" },
      { text: "volume down", desc: "Turn volume down" },
      { text: "volume 50", desc: "Set volume to 50%" },
    ],
  },
  {
    name: "Queue & Filters",
    commands: [
      { text: "play more jazz", desc: "Filter by jazz genre" },
      { text: "play more rock", desc: "Filter by rock genre" },
      { text: "play more ambient", desc: "Filter by ambient genre" },
      { text: "clear filters", desc: "Clear all filters" },
      { text: "what is next", desc: "Show upcoming queue" },
      { text: "rebuild", desc: "Rebuild the AI queue" },
      { text: "clear queue", desc: "Clear the queue" },
      { text: "play next", desc: "Play next song now" },
    ],
  },
  {
    name: "Song Actions",
    commands: [
      { text: "heart this", desc: "Like current song" },
      { text: "thumbs down", desc: "Dislike current song" },
      { text: "go back", desc: "Undo last skip" },
      { text: "undo", desc: "Undo last command" },
    ],
  },
  {
    name: "Settings & Info",
    commands: [
      { text: "help", desc: "Show available commands" },
      { text: "exploration 50", desc: "Set exploration to 50%" },
      { text: "sleep 30", desc: "Set sleep timer for 30 minutes" },
      { text: "cancel sleep", desc: "Cancel sleep timer" },
      { text: "radio mode", desc: "Switch to radio mode" },
      { text: "normal mode", desc: "Switch to normal mode" },
    ],
  },
];

function CommandPalette({ visible, onClose, addToast, onCommandSent }) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef(null);

  const flatCommands = useMemo(() => COMMAND_CATEGORIES.flatMap(c => c.commands), []);

  const filtered = useMemo(() => {
    if (!query.trim()) return COMMAND_CATEGORIES;
    const q = query.toLowerCase();
    const match = (cmd) =>
      cmd.text.toLowerCase().includes(q) || cmd.desc.toLowerCase().includes(q);
    return COMMAND_CATEGORIES
      .map(cat => ({ ...cat, commands: cat.commands.filter(match) }))
      .filter(cat => cat.commands.length > 0);
  }, [query]);

  useEffect(() => {
    if (visible) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [visible]);

  const executeCommand = async (text) => {
    try {
      const data = await api.sendCommand(text);
      onCommandSent?.(data);
      addToast?.(data.explanation || "Done", "success");
    } catch (err) {
      addToast?.("Command failed: " + (err.message || "Server error"), "error");
    }
    onClose();
  };

  const handleKeyDown = (e) => {
    if (e.key === "Escape") { onClose(); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const total = filtered.reduce((s, c) => s + c.commands.length, 0);
      if (total === 0) return;
      setSelectedIndex(i => Math.min(i + 1, total - 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex(i => Math.max(i - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      let idx = 0;
      for (const cat of filtered) {
        for (const cmd of cat.commands) {
          if (idx === selectedIndex) {
            executeCommand(cmd.text);
            return;
          }
          idx++;
        }
      }
    }
  };

  if (!visible) return null;

  let flatIdx = 0;
  const trapRef = useFocusTrap(visible);
  return (
    <div ref={trapRef} className="fixed inset-0 z-[200] flex items-start justify-center pt-[10vh]"
      style={{ animation: "backdropFadeIn 150ms ease" }}>
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg mx-4 rounded-2xl overflow-hidden shadow-2xl"
        style={{ animation: "scaleIn 150ms ease", background: "var(--bg-surface)" }}>
        <div className="flex items-center gap-3 px-4 py-3"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <Terminal size={16} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input ref={inputRef} value={query} onChange={e => { setQuery(e.target.value); setSelectedIndex(0); }}
            onKeyDown={handleKeyDown}
            placeholder="Search commands..."
            className="flex-1 bg-transparent border-none outline-none text-sm"
            style={{ color: "var(--text-primary)" }}
            autoComplete="off" />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>ESC</kbd>
        </div>
        <div className="max-h-[55vh] overflow-y-auto">
          {filtered.length === 0 && (
            <div className="px-4 py-6 text-center text-sm"
              style={{ color: "var(--text-muted)" }}>No commands found</div>
          )}
          {filtered.map((cat) => (
            <div key={cat.name}>
              {(!query.trim() || cat.name.toLowerCase().includes(query.toLowerCase())) && (
                <div className="px-4 py-1.5 text-[10px] font-semibold tracking-wide uppercase"
                  style={{ color: "var(--text-muted)", background: "var(--bg-elevated)" }}>
                  {cat.name}
                </div>
              )}
              {cat.commands.map((cmd) => {
                const isSelected = flatIdx === selectedIndex;
                flatIdx++;
                return (
                  <div key={cmd.text}
                    className="flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors"
                    onClick={() => executeCommand(cmd.text)}
                    onMouseEnter={() => setSelectedIndex(flatIdx - 1)}
                    style={{
                      background: isSelected ? "rgba(255,255,255,0.05)" : "transparent",
                    }}>
                    <span className="text-xs font-mono min-w-[120px]" style={{ color: "var(--accent)" }}>
                      {cmd.text}
                    </span>
                    <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                      {cmd.desc}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div className="px-4 py-2 text-[10px] flex gap-4"
          style={{ background: "var(--bg-surface)", borderTop: "1px solid var(--border)", color: "var(--text-muted)" }}>
          <span>Type to filter</span>
          <span>↑↓ Navigate</span>
          <span>Enter Execute</span>
          <span>Esc Close</span>
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;