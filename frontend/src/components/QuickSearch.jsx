import React, { useState, useEffect, useRef } from "react";
import { Search } from "lucide-react";
import * as api from "../api";
import useFocusTrap from "../hooks/useFocusTrap";

export default function QuickSearch({ visible, onClose, addToast }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (visible) {
      setQuery("");
      setResults([]);
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [visible]);

  useEffect(() => {
    if (!query.trim()) { setResults([]); return; }
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.fetchSongs({ search: query, limit: 15 });
        setResults(data.songs || []);
        setSelectedIndex(0);
      } catch (e) {}
      setLoading(false);
    }, 200);
    return () => clearTimeout(timer);
  }, [query]);

  const handleKeyDown = (e) => {
    if (e.key === "Escape") { onClose(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setSelectedIndex(i => Math.min(i + 1, results.length - 1)); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); setSelectedIndex(i => Math.max(i - 1, 0)); return; }
    if (e.key === "Enter" && results[selectedIndex]) {
      e.preventDefault();
      const song = results[selectedIndex];
      if (e.shiftKey) {
        api.playSong(song.id).catch(() => {});
        addToast?.(`Playing ${song.title || "Unknown"}`, "success");
      } else {
        api.addToQueue(song.id).catch(() => {});
        addToast?.(`Added ${song.title || "Unknown"} to queue`, "success");
      }
      onClose();
    }
  };

  const trapRef = useFocusTrap(visible);

  if (!visible) return null;

  return (
    <div ref={trapRef} className="fixed inset-0 z-[200] flex items-start justify-center pt-[15vh]"
      style={{ animation: "backdropFadeIn 150ms ease" }}>
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg mx-4 rounded-2xl overflow-hidden shadow-2xl"
        style={{ animation: "scaleIn 150ms ease", background: "var(--bg-surface)" }}>
        <div className="flex items-center gap-3 px-4 py-3"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <Search size={16} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search songs, artists..."
            className="flex-1 bg-transparent border-none outline-none text-sm"
            style={{ color: "var(--text-primary)" }}
            autoComplete="off" />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>ESC</kbd>
        </div>
        <div className="max-h-[50vh] overflow-y-auto">
          {loading && (
            <div className="px-4 py-6 text-center text-sm"
              style={{ color: "var(--text-muted)" }}>Searching...</div>
          )}
          {!loading && query && results.length === 0 && (
            <div className="px-4 py-6 text-center text-sm"
              style={{ color: "var(--text-muted)" }}>No songs found</div>
          )}
          {results.map((song, i) => (
            <div key={song.id}
              className="flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors"
              onClick={() => {
                api.addToQueue(song.id).catch(() => {});
                addToast?.(`Added ${song.title || "Unknown"} to queue`, "success");
                onClose();
              }}
              onMouseEnter={() => setSelectedIndex(i)}
              style={{
                background: i === selectedIndex ? "var(--accent-muted)" : "transparent",
              }}>
              <div className="flex-1 min-w-0">
                <div className="text-sm truncate" style={{ color: "var(--text-primary)" }}>
                  {song.title || "Unknown"}
                </div>
                <div className="text-xs truncate" style={{ color: "var(--text-muted)" }}>
                  {song.artist || "Unknown artist"}
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs tabular-nums flex-shrink-0"
                style={{ color: "var(--text-muted)" }}>
                {song.bpm != null && <span>{song.bpm} BPM</span>}
                {song.energy != null && (
                  <span style={{ color: song.energy > 0.6 ? "#f59e0b" : song.energy > 0.3 ? "#4eccc4" : "#64748b" }}>
                    {Math.round(song.energy * 100)}%
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="px-4 py-2 text-[10px] flex gap-4"
          style={{ background: "var(--bg-surface)", borderTop: "1px solid var(--border)", color: "var(--text-muted)" }}>
          <span>↑↓ Navigate</span>
          <span>Enter Add to queue</span>
          <span>Shift+Enter Play now</span>
        </div>
      </div>
    </div>
  );
}
