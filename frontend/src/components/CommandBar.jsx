import React, { useState, useRef, useEffect, useCallback } from "react";
import { Terminal, X } from "lucide-react";
import * as api from "../api";

const COMMAND_HINTS = ['"more energetic"', '"chill vibes"', '"vibe lock"', '"what is next"', '"play more jazz"', '"heart this"', '"pause"', '"play 5 songs from Radiohead"', '"queue chill songs for studying"', '"play something upbeat"', '"sounds like midnight driving"', '"play dreamy reverb music"', '"help"'];
const MAX_HISTORY = 10;

function CommandBar({ onCommandSent, compact = false, onFocus, onClose,
  responseTimeout = 10, inactiveTimeout = 6, onUserActivity, lastCommandRef, onUndo,
  nlResponse, nlResponseVisible, nlResponseTimerRef, nlPausedRef,
  clearNlResponseTimer, startNlResponseTimer, setNlResponse, setNlResponseVisible }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const inputRef = useRef(null);
  const submittedCmdRef = useRef(null);
  const [placeholder] = useState(() => `Ask the DJ… try ${COMMAND_HINTS[Math.floor(Math.random() * COMMAND_HINTS.length)]}`);
  const expanded = !compact;

  useEffect(() => {
    const handler = (e) => {
      if (e.key === "/" && document.activeElement !== inputRef.current) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleActivity = useCallback(() => {
    onUserActivity?.();
    nlPausedRef.current = false;
    if (nlResponse) {
      if (nlResponseVisible) {
        clearNlResponseTimer();
        startNlResponseTimer();
      }
    }
  }, [onUserActivity, nlResponse, nlResponseVisible, clearNlResponseTimer, startNlResponseTimer]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!text.trim() || sending) return;
    const cmd = text.trim();
    // Intercept "undo" for NL command undo (before hitting backend)
    if (/^undo(\s+(last\s+)?(command|action|that)|)$/i.test(cmd) && onUndo) {
      setText("");
      onUndo();
      handleActivity();
      return;
    }
    setSending(true);
    setNlResponse(null);
    setNlResponseVisible(false);
    submittedCmdRef.current = cmd;
    setHistory((prev) => [cmd, ...prev].slice(0, MAX_HISTORY));
    setHistoryIdx(-1);
    handleActivity();
    try {
      const data = await api.sendCommand(cmd);
      setNlResponse(data);
      setNlResponseVisible(true);
      startNlResponseTimer();
      onCommandSent?.(data);
      const currentInput = inputRef.current?.value || "";
      if (currentInput.trim() === cmd) {
        setText("");
      }
    } catch (err) {
      setNlResponse({ explanation: "Command failed. Is the server running?" });
      setNlResponseVisible(true);
      startNlResponseTimer();
    } finally {
      submittedCmdRef.current = null;
      setSending(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (history.length === 0) return;
      const next = Math.min(historyIdx + 1, history.length - 1);
      setHistoryIdx(next);
      setText(history[next]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (historyIdx <= 0) { setHistoryIdx(-1); setText(""); return; }
      setHistoryIdx(historyIdx - 1);
      setText(history[historyIdx - 1]);
    }
    handleActivity();
  };

  const handleMouseEnter = () => { nlPausedRef.current = true; clearNlResponseTimer(); };
  const handleMouseLeave = () => { nlPausedRef.current = false; if (nlResponse) startNlResponseTimer(); };

  const handleInputChange = (e) => { setText(e.target.value); if (compact) onFocus?.(); handleActivity(); };

  const close = () => {
    clearNlResponseTimer(); setNlResponse(null); setNlResponseVisible(false);
    inputRef.current?.blur(); onClose?.();
  };

  // One form and one input in both states, so the first click both expands the
  // bar and leaves the cursor in the box (the old version swapped inputs).
  return (
    <div className="rounded-xl transition-all" style={{
        background: "var(--bg-surface)", padding: expanded ? "10px 12px" : "4px 6px",
        border: `1px solid ${expanded ? "color-mix(in srgb, var(--accent) 30%, transparent)" : "var(--border-subtle)"}`,
      }}
      onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      <form onSubmit={handleSubmit} className="flex items-center gap-2" style={{ padding: expanded ? "2px 2px" : "2px 6px" }}>
        <Terminal size={13} style={{ color: expanded ? "var(--accent)" : "var(--text-muted)", flexShrink: 0 }} />
        <input ref={inputRef} type="text" value={text} onChange={handleInputChange}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); close(); return; } handleKeyDown(e); }}
          onFocus={(e) => { onFocus?.(e); handleActivity(); }}
          onClick={(e) => { if (compact) onFocus?.(e); }}
          placeholder={sending ? "Thinking…" : placeholder}
          aria-label="Ask the DJ"
          className={`flex-1 bg-transparent border-none outline-none ${expanded ? "text-sm" : "text-[12px]"}`}
          style={{ color: "var(--text-primary)", minWidth: 0 }}
          readOnly={sending} />
        {!expanded && nlResponse && (
          <span className="text-[11px] truncate max-w-[240px]" style={{ color: "var(--accent)" }}>{nlResponse.explanation}</span>
        )}
        {expanded && onClose && (
          <button type="button" onClick={close} className="ui-btn sm" title="Close (Esc)" aria-label="Close">
            <X size={12} />
          </button>
        )}
        {expanded && (
          <button type="submit" disabled={sending || !text.trim()} className="ui-btn primary sm" style={{ minWidth: 56 }}>
            {sending ? (
              <span className="flex items-center gap-0.5">
                <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: "var(--accent)", animationDelay: "0ms" }} />
                <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: "var(--accent)", animationDelay: "150ms" }} />
                <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: "var(--accent)", animationDelay: "300ms" }} />
              </span>
            ) : "Send"}
          </button>
        )}
      </form>
      {expanded && nlResponse && (
        <div className="rounded-lg text-sm transition-all duration-300"
          style={{
            background: "var(--bg-elevated)", marginTop: 8, padding: "10px 14px",
            opacity: nlResponseVisible ? 1 : 0,
            transform: nlResponseVisible ? "translateY(0)" : "translateY(-4px)",
          }}>
          <p className="font-medium" style={{ color: "var(--accent)" }}>{nlResponse.explanation}</p>
          {nlResponse.songs && nlResponse.songs.length > 0 && (
            <div className="mt-2 space-y-1">
              <div className="text-[10px] font-semibold tracking-wide uppercase mb-1" style={{ color: "var(--text-muted)" }}>
                Audio matches
              </div>
              {nlResponse.songs.map((song, i) => (
                <div key={i} className="flex items-center justify-between py-1 px-2 rounded" style={{ background: "var(--bg-muted)" }}>
                  <span className="text-xs truncate" style={{ color: "var(--text-primary)" }}>
                    {song.artist} — {song.title}
                  </span>
                  <span className="text-[10px] ml-2 flex-shrink-0" style={{ color: song.score > 0.7 ? "#2dd4a0" : song.score > 0.4 ? "#f59e0b" : "var(--text-muted)" }}>
                    {Math.round((song.score || 0) * 100)}%
                  </span>
                </div>
              ))}
            </div>
          )}
          {nlResponse.commands && Array.isArray(nlResponse.commands) && (
            <ul className="mt-1.5 space-y-0.5">
              {nlResponse.commands.map((cmd, i) => <li key={i} className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{cmd}</li>)}
            </ul>
          )}
          {nlResponse.commands && !Array.isArray(nlResponse.commands) && typeof nlResponse.commands === 'object' && (
            <div className="mt-2 space-y-2.5">
              {Object.entries(nlResponse.commands).map(([category, cmds]) => (
                <div key={category}>
                  <div className="text-xs font-bold tracking-wide mb-1" style={{ color: "var(--accent)" }}>{category}</div>
                  {Array.isArray(cmds) && cmds.map(([cmd, desc], i) => (
                    <div key={i} className="flex justify-between items-center py-0.5">
                      <span className="text-xs font-mono" style={{ color: "var(--text-primary)" }}>{cmd}</span>
                      <span className="text-[10px] ml-2 text-right" style={{ color: "var(--text-muted)" }}>{desc}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default CommandBar;