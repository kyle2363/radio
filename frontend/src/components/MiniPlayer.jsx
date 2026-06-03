import React, { useState, useEffect, useRef } from "react";
import * as api from "../api";
import { Play, Pause, SkipForward, Heart, ThumbsDown, Maximize2, Minus, X } from "lucide-react";

function formatTime(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export default function MiniPlayer() {
  const [track, setTrack] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [positionMs, setPositionMs] = useState(0);
  const [durationMs, setDurationMs] = useState(0);
  const animRef = useRef(null);

  const fetchStatus = async () => {
    try {
      const status = await api.fetchPlayStatus();
      setTrack(status.current_song || null);
      setIsPlaying(status.is_playing || false);
      setPositionMs(status.position_ms || 0);
      setDurationMs(status.duration_ms || 0);
    } catch (_) {}
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!isPlaying) {
      if (animRef.current) cancelAnimationFrame(animRef.current);
      return;
    }
    let last = Date.now();
    const tick = () => {
      const now = Date.now();
      setPositionMs((prev) => {
        const next = prev + (now - last);
        last = now;
        return next < durationMs ? next : durationMs;
      });
      animRef.current = requestAnimationFrame(tick);
    };
    animRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animRef.current);
  }, [isPlaying, durationMs]);

  const handlePlayPause = () => {
    if (isPlaying) api.pauseSong().catch(() => {});
    else api.resumeSong().catch(() => {});
    setIsPlaying(!isPlaying);
  };

  const pct = durationMs > 0 ? Math.min(positionMs / durationMs, 1) : 0;

  return (
    <div className="mini-player"
      style={{
        width: "100%",
        height: "100vh",
        background: "var(--bg-page)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        userSelect: "none",
        cursor: "default",
      }}>
      {/* Progress bar */}
      <div style={{
        width: "100%",
        height: "3px",
        background: "var(--bg-elevated)",
        flexShrink: 0,
      }}>
        <div style={{
          width: `${pct * 100}%`,
          height: "100%",
          background: "var(--accent, #4ade80)",
          transition: "width 0.1s linear",
        }} />
      </div>

      {/* Main content row */}
      <div style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        padding: "0 12px",
        gap: "10px",
        minWidth: 0,
        WebkitAppRegion: "drag",
      }}>
        {/* Song info */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: "13px",
            fontWeight: 600,
            color: "var(--text-primary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}>
            {track ? `${track.title || "Unknown"}` : "No track"}
          </div>
          <div style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            marginTop: "1px",
          }}>
            {track ? track.artist || "Unknown artist" : "—"}
          </div>
        </div>

        {/* Time */}
        <div style={{
          fontSize: "10px",
          color: "var(--text-muted)",
          fontVariantNumeric: "tabular-nums",
          flexShrink: 0,
          WebkitAppRegion: "no-drag",
        }}>
          {formatTime(positionMs)} / {formatTime(durationMs)}
        </div>

        {/* Controls */}
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "2px",
          flexShrink: 0,
          WebkitAppRegion: "no-drag",
        }}>
          <button onClick={handlePlayPause} className="mini-btn" title={isPlaying ? "Pause" : "Play"}>
            {isPlaying ? <Pause size={16} /> : <Play size={16} />}
          </button>
          <button onClick={() => api.skipSong().catch(() => {})} className="mini-btn" title="Next">
            <SkipForward size={16} />
          </button>
          <button onClick={() => api.heartSong().catch(() => {})} className="mini-btn" title="Heart">
            <Heart size={14} />
          </button>
          <button onClick={() => api.thumbsDown().catch(() => {})} className="mini-btn" title="Thumbs down">
            <ThumbsDown size={14} />
          </button>
        </div>

        {/* Window controls */}
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "2px",
          flexShrink: 0,
          WebkitAppRegion: "no-drag",
        }}>
          <button onClick={() => window.electron?.windowMinimize?.()} className="mini-win-btn" title="Minimize">
            <Minus size={12} />
          </button>
          <button onClick={() => window.electron?.toggleMiniPlayer?.()} className="mini-win-btn" title="Expand">
            <Maximize2 size={12} />
          </button>
          <button onClick={() => window.electron?.windowClose?.()} className="mini-win-btn mini-close" title="Close">
            <X size={12} />
          </button>
        </div>
      </div>

      <style>{`
        .mini-player button {
          background: transparent;
          border: none;
          color: var(--text-secondary, #94a3b8);
          cursor: pointer;
          padding: 6px;
          border-radius: 6px;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: all 0.15s;
        }
        .mini-player button:hover {
          background: var(--bg-elevated, rgba(255,255,255,0.06));
          color: var(--text-primary, #e2e8f0);
        }
        .mini-player .mini-btn {
          padding: 7px;
        }
        .mini-player .mini-btn:hover {
          background: rgba(255,255,255,0.08);
        }
        .mini-player .mini-win-btn:hover {
          background: rgba(255,255,255,0.08);
        }
        .mini-player .mini-close:hover {
          background: rgba(239,68,68,0.2);
          color: #ef4444;
        }
      `}</style>
    </div>
  );
}
