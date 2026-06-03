import React, { useState, useRef, useEffect } from "react";
import { Terminal, Heart, ThumbsDown, SkipBack, Play, Pause, Volume2, RefreshCw, Moon, Zap, HelpCircle } from "lucide-react";
import * as api from "../api";

const COMMAND_HINTS = ['"more energetic"', '"chill vibes"', '"vibe lock"', '"what is next"', '"play more jazz"', '"heart this"', '"pause"', '"play 5 songs from Radiohead"', '"queue chill songs for studying"', '"play something upbeat"', '"help"'];
const MAX_HISTORY = 10;

function CommandBar({ onCommandSent, compact = false, onFocus, onClose }) {
  const [text, setText] = useState("");
  const [response, setResponse] = useState(null);
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const inputRef = useRef(null);
  const submittedCmdRef = useRef(null);

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

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!text.trim() || sending) return;
    setSending(true);
    setResponse(null);
    const cmd = text.trim();
    submittedCmdRef.current = cmd;
    setHistory((prev) => [cmd, ...prev].slice(0, MAX_HISTORY));
    setHistoryIdx(-1);
    try {
      const data = await api.sendCommand(cmd);
      setResponse(data);
      onCommandSent?.(data);
      // Only clear input if user hasn't typed a new command while waiting
      const currentInput = inputRef.current?.value || "";
      if (currentInput.trim() === cmd) {
        setText("");
      }
    } catch (err) {
      setResponse({ explanation: "Command failed. Is the server running?" });
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
  };

  if (compact) {
    return (
      <div className="rounded-lg px-3 py-1.5" style={{ background: "var(--bg-surface)" }}>
        <form onSubmit={handleSubmit} className="flex items-center gap-2">
          <Terminal size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input ref={inputRef} type="text" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={handleKeyDown}
            onFocus={onFocus}
            placeholder={sending ? "Thinking..." : `Ask something... (try ${COMMAND_HINTS[Math.floor(Math.random() * COMMAND_HINTS.length)]})`}
            className="flex-1 bg-transparent border-none outline-none text-[11px]"
            style={{ color: "var(--text-primary)" }}
            disabled={sending} />
          {response && (
            <span className="text-[10px] truncate max-w-[200px]" style={{ color: "var(--accent)" }}>{response.explanation}</span>
          )}
        </form>
      </div>
    );
  }

  return (
    <div className="rounded-xl p-4" style={{ background: "var(--bg-surface)" }}>
      <form onSubmit={handleSubmit} className="flex items-center gap-1.5 rounded-lg px-4 py-2.5"
        style={{ background: "var(--bg-surface)" }}>
        <input ref={inputRef} type="text" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={handleKeyDown}
          placeholder={sending ? "Thinking..." : `Say something... (try ${COMMAND_HINTS[Math.floor(Math.random() * COMMAND_HINTS.length)]})`}
          className="flex-1 bg-transparent border-none outline-none text-sm"
          style={{ color: "var(--text-primary)" }}
          disabled={sending} />
        {onClose && (
          <button type="button" onClick={onClose} className="px-2 py-1 rounded text-xs transition hover:opacity-80"
            style={{ color: "var(--text-muted)" }}>✕</button>
        )}
        <button type="submit" disabled={sending || !text.trim()} className="px-5 py-2 rounded-md text-xs font-medium transition"
          style={{ background: "var(--accent-muted)", color: "var(--accent)", border: "1px solid rgba(78,204,196,0.25)" }}>
          {sending ? (
            <span className="flex items-center gap-0.5">
              <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: "var(--accent)", animationDelay: "0ms" }} />
              <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: "var(--accent)", animationDelay: "150ms" }} />
              <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: "var(--accent)", animationDelay: "300ms" }} />
            </span>
          ) : "Send"}
        </button>
      </form>
      {response && (
        <div className="mt-2 px-4 py-2.5 rounded-lg text-sm" style={{ background: "var(--bg-elevated)" }}>
          <p className="font-medium" style={{ color: "var(--accent)" }}>{response.explanation}</p>
          {response.commands && Array.isArray(response.commands) && (
            <ul className="mt-1.5 space-y-0.5">
              {response.commands.map((cmd, i) => <li key={i} className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{cmd}</li>)}
            </ul>
          )}
          {response.commands && !Array.isArray(response.commands) && typeof response.commands === 'object' && (
            <div className="mt-2 space-y-2.5">
              {Object.entries(response.commands).map(([category, cmds]) => (
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
