import React, { useState, useEffect, useRef, useCallback } from "react";
import * as api from "../api";

function LyricsPanel({ song, positionMs = 0, durationMs = 0, isPlaying = false }) {
  const [karaokeMode, setKaraokeMode] = useState(true);
  const [timedLines, setTimedLines] = useState(null);
  const [loading, setLoading] = useState(false);
  const scrollerRef = useRef(null);
  const activeLineRef = useRef(null);
  const prevSongIdRef = useRef(null);

  // Local position interpolation for smooth 60fps lyrics sync
  const [interpolatedPos, setInterpolatedPos] = useState(0);
  const anchorRef = useRef({ pos: 0, time: 0 });
  const rafRef = useRef(null);

  // Sync anchor from backend position updates (arrives every 300ms)
  useEffect(() => {
    anchorRef.current = { pos: positionMs, time: performance.now() };
    setInterpolatedPos(positionMs);
  }, [positionMs]);

  // RAF interpolation loop — estimates position between backend polls
  useEffect(() => {
    if (!isPlaying || !song?.id) {
      setInterpolatedPos(positionMs);
      return;
    }
    const tick = () => {
      const elapsed = performance.now() - anchorRef.current.time;
      setInterpolatedPos(anchorRef.current.pos + elapsed);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, song?.id]);

  // Fetch timing data when song changes
  useEffect(() => {
    if (!song?.id) {
      setTimedLines(null);
      return;
    }
    if (song.id === prevSongIdRef.current) return;
    prevSongIdRef.current = song.id;

    setLoading(true);
    api.fetchLyricsTiming(song.id).then((data) => {
      if (data?.lines && data.lines.length > 0) {
        setTimedLines(data.lines);
      } else {
        setTimedLines(null);
      }
    }).catch(() => {
      setTimedLines(null);
    }).finally(() => {
      setLoading(false);
    });
  }, [song?.id]);

  // Build the display lines array from either timed data or plain lyrics
  const displayLines = useCallback(() => {
    if (timedLines) {
      return timedLines;
    }
    if (song?.lyrics) {
      return song.lyrics.split("\n").map((line) => ({
        text: line || "",
        start_ms: 0,
        end_ms: 0,
        words: [],
      }));
    }
    return null;
  }, [timedLines, song?.lyrics]);

  // Determine the active line index using interpolated position
  const activeIndex = (() => {
    const lines = displayLines();
    if (!lines || lines.length === 0) return -1;
    const pos = interpolatedPos;

    if (timedLines) {
      // Find the last line whose start_ms <= current position
      let idx = -1;
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (line.start_ms > 0 && pos >= line.start_ms) {
          idx = i;
        }
        if (line.start_ms > 0 && pos < line.start_ms) {
          break;
        }
      }
      return idx;
    }

    // Fallback: equal-time-per-line estimation
    const contentLines = lines.filter((l) => l.text.trim());
    if (contentLines.length === 0 || durationMs <= 0) return -1;
    const msPerLine = durationMs / contentLines.length;
    const idx = Math.floor(pos / msPerLine);
    return Math.min(idx, contentLines.length - 1);
  })();

  // Scroll to active line
  useEffect(() => {
    if (activeLineRef.current && scrollerRef.current) {
      activeLineRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [activeIndex]);

  // Non-karaoke mode: smooth scroll based on interpolated position
  useEffect(() => {
    if (karaokeMode || !scrollerRef.current || !displayLines()) return;
    const el = scrollerRef.current;
    const pct = durationMs > 0 ? Math.min(interpolatedPos / durationMs * 1.1, 1) : 0;
    const target = Math.min((el.scrollHeight - el.clientHeight) * pct, el.scrollHeight);
    el.scrollTo({ top: target, behavior: "smooth" });
  }, [interpolatedPos, karaokeMode, durationMs, displayLines]);

  const lines = displayLines();
  if (!lines && !loading) {
    return null;
  }

  return (
    <div
      className="lyrics-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        borderRadius: "10px",
        border: "1px solid var(--border)",
        background: "var(--bg-page)",
        overflow: "hidden",
        maxHeight: "170px",
      }}
    >
      {/* Header */}
      <div
        className="lyrics-label"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 16px 6px",
        }}
      >
        <span style={{ fontSize: "10px", fontWeight: 600, letterSpacing: "0.1em", color: "var(--text-muted)" }}>
          LYRICS
        </span>
        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
          <button
            onClick={() => setKaraokeMode(true)}
            style={{
              fontSize: "10px", padding: "2px 8px", borderRadius: "10px",
              border: karaokeMode ? "1px solid rgba(45,212,191,0.35)" : "1px solid var(--border)",
              color: karaokeMode ? "var(--accent)" : "var(--text-muted)",
              background: karaokeMode ? "var(--accent-dim, rgba(45,212,191,0.12))" : "transparent",
              cursor: "pointer", transition: "all 0.2s",
            }}
          >
            karaoke
          </button>
          <button
            onClick={() => setKaraokeMode(false)}
            style={{
              fontSize: "10px", padding: "2px 8px", borderRadius: "10px",
              border: !karaokeMode ? "1px solid rgba(45,212,191,0.35)" : "1px solid var(--border)",
              color: !karaokeMode ? "var(--accent)" : "var(--text-muted)",
              background: !karaokeMode ? "var(--accent-dim, rgba(45,212,191,0.12))" : "transparent",
              cursor: "pointer", transition: "all 0.2s",
            }}
          >
            view
          </button>
        </div>
      </div>

      {/* Scrolling lyrics area */}
      <div style={{ position: "relative", flex: 1, minHeight: 0, overflow: "hidden" }}>
        {/* Fade edges */}
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: "52px",
          pointerEvents: "none", zIndex: 2,
          background: "linear-gradient(to bottom, var(--bg-page) 0%, transparent 100%)",
        }} />
        <div style={{
          position: "absolute", bottom: 0, left: 0, right: 0, height: "52px",
          pointerEvents: "none", zIndex: 2,
          background: "linear-gradient(to top, var(--bg-page) 0%, transparent 100%)",
        }} />

        {/* Scrollable content */}
        <div
          ref={scrollerRef}
          style={{
            height: "100%", overflowY: "auto", scrollbarWidth: "none",
            padding: "0 16px",
          }}
          className="lyrics-scroller"
        >
          <div style={{ height: "52px" }} />
          {lines.length === 0 ? (
            <div style={{
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center",
              height: "100%", opacity: 0.25, color: "var(--text-muted)",
              fontSize: "12px", gap: "8px",
            }}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 18V5l12-2v13"/>
                <circle cx="6" cy="18" r="3"/>
                <circle cx="18" cy="16" r="3"/>
              </svg>
              <span>No lyrics available</span>
            </div>
          ) : (
            lines.map((line, i) => {
              const isBlank = !line.text.trim();
              const dist = Math.abs(i - activeIndex);
              let opacity;
              if (!karaokeMode) {
                opacity = 0.75;
              } else if (activeIndex < 0 && durationMs > 0) {
                const virtualIdx = Math.floor((interpolatedPos / durationMs) * lines.length);
                const vdist = Math.abs(i - virtualIdx);
                if (vdist === 0) opacity = 1;
                else if (vdist === 1) opacity = 0.48;
                else if (vdist === 2) opacity = 0.28;
                else opacity = 0.15;
              } else if (dist === 0) {
                opacity = 1;
              } else if (dist === 1) {
                opacity = 0.48;
              } else if (dist === 2) {
                opacity = 0.28;
              } else {
                opacity = 0.15;
              }

              if (isBlank) {
                return <div key={i} style={{ height: "14px" }} />;
              }

              return (
                <div
                  key={i}
                  ref={i === activeIndex ? activeLineRef : null}
                  className={`lyrics-line${i === activeIndex && karaokeMode ? " active" : ""}`}
                  style={{
                    padding: "3px 0",
                    fontSize: i === activeIndex && karaokeMode ? "15px" : "13.5px",
                    lineHeight: 1.65,
                    cursor: "default",
                    userSelect: "none",
                    transition: "opacity 0.45s ease, color 0.45s ease, font-size 0.3s ease",
                    color: i === activeIndex && karaokeMode ? "var(--accent)" : "var(--text-primary)",
                    fontWeight: i === activeIndex && karaokeMode ? 500 : 400,
                    opacity,
                  }}
                >
                  {line.text}
                </div>
              );
            })
          )}
          <div style={{ height: "52px" }} />
        </div>
      </div>
    </div>
  );
}

export default LyricsPanel;
