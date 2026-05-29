import React, { useState, useRef, useEffect, useCallback } from "react";
import { RotateCcw, Play, Pause, SkipForward, Heart, ThumbsDown, Trash2, Lock, Unlock } from "lucide-react";
import ParticleBurst from "./ParticleBurst";

function formatTime(ms) {
  if (!ms || ms < 0) return "0:00";
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

function PlayerControls({ isPlaying, onPlay, onPause, onNext, onHeart, onThumbsDown, onReplay, onClearQueue, positionMs, durationMs, onSeek, vibeLock, onVibeLockToggle }) {
  const [isDragging, setIsDragging] = useState(false);
  const [dragProgress, setDragProgress] = useState(0);
  const [heartAnim, setHeartAnim] = useState(false);
  const [dislikeAnim, setDislikeAnim] = useState(false);
  const barRef = useRef(null);

  const progress = durationMs > 0 ? (isDragging ? dragProgress : positionMs) / durationMs : 0;
  const displayProgress = isDragging ? dragProgress : positionMs;

  const seekFromEvent = useCallback((e) => {
    if (!barRef.current || durationMs <= 0) return;
    const rect = barRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    return pct * durationMs;
  }, [durationMs]);

  const handleBarMouseDown = useCallback((e) => {
    e.preventDefault();
    if (!barRef.current || durationMs <= 0) return;
    setIsDragging(true);
    const seekMs = seekFromEvent(e);
    if (seekMs !== undefined) setDragProgress(seekMs);
  }, [durationMs, seekFromEvent]);

  useEffect(() => {
    if (!isDragging) return;
    const handleMouseMove = (e) => {
      const seekMs = seekFromEvent(e);
      if (seekMs !== undefined) setDragProgress(seekMs);
    };
    const handleMouseUp = (e) => {
      const seekMs = seekFromEvent(e);
      if (seekMs !== undefined) onSeek?.(seekMs);
      setIsDragging(false);
    };
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, onSeek, seekFromEvent]);

  const handleBarKeyDown = useCallback((e) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); onSeek?.(Math.max(0, positionMs - 5000)); }
    else if (e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); onSeek?.(Math.min(durationMs, positionMs + 5000)); }
  }, [positionMs, durationMs, onSeek]);

  const handleHeart = () => { setHeartAnim(true); onHeart?.(); };
  const handleDislike = () => { setDislikeAnim(true); onThumbsDown?.(); };

  return (
    <div className="space-y-3">
      {/* Progress bar */}
      <div className="flex items-center gap-3">
        <span className="text-xs tabular-nums w-10 text-right" style={{ color: "var(--text-muted)" }}>{formatTime(displayProgress)}</span>
        <div ref={barRef} tabIndex={0} className="flex-1 h-[3px] rounded-full cursor-pointer group relative focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/50"
          style={{ background: "var(--border)" }}
          onMouseDown={handleBarMouseDown} onKeyDown={handleBarKeyDown}
          role="slider" aria-label="Seek" aria-valuemin={0} aria-valuemax={durationMs} aria-valuenow={positionMs}>
          <div className="h-full rounded-full relative transition-none" style={{ width: `${progress * 100}%`, background: "linear-gradient(90deg, #3ab8b0, #4eccc4)" }}>
            <div className="absolute right-0 top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full opacity-0 group-hover:opacity-100 transition-opacity shadow" style={{ background: "var(--accent)" }} />
          </div>
        </div>
        <span className="text-xs tabular-nums w-10" style={{ color: "var(--text-muted)" }}>{formatTime(durationMs)}</span>
      </div>

      {/* Controls */}
      <div className="flex justify-center items-center gap-1.5">
        <button onClick={onReplay} className="w-10 h-10 rounded-[10px] flex items-center justify-center transition btn-icon" title="Replay (R)">
          <RotateCcw size={16} />
        </button>
        <button onClick={isPlaying ? onPause : onPlay} className="w-12 h-12 rounded-xl flex items-center justify-center transition" title="Play/Pause (Space)"
          style={{ background: "var(--accent-muted)", border: "1px solid rgba(78,204,196,0.3)", color: "var(--accent)" }}>
          {isPlaying ? <Pause size={19} /> : <Play size={19} />}
        </button>
        <button onClick={onNext} className="w-10 h-10 rounded-[10px] flex items-center justify-center transition btn-icon" title="Next (N)">
          <SkipForward size={16} />
        </button>
        <button onClick={handleHeart} className={`w-10 h-10 rounded-[10px] flex items-center justify-center transition btn-icon btn-icon-pink ${heartAnim ? "heart-pop" : ""}`}
          onAnimationEnd={() => setHeartAnim(false)} title="Heart (H)">
          <Heart size={16} className={heartAnim ? "fill-pink-500 text-pink-500" : ""} />
          <ParticleBurst active={heartAnim} color="#f078a8" />
        </button>
        <button onClick={handleDislike} className={`w-10 h-10 rounded-[10px] flex items-center justify-center transition btn-icon btn-icon-orange ${dislikeAnim ? "shake" : ""}`}
          onAnimationEnd={() => setDislikeAnim(false)} title="Dislike (D)">
          <ThumbsDown size={16} />
          <ParticleBurst active={dislikeAnim} color="#f09060" />
        </button>
        <button onClick={onClearQueue} className="w-10 h-10 rounded-[10px] flex items-center justify-center transition btn-icon btn-icon-red" title="Clear queue">
          <Trash2 size={16} />
        </button>
        {onVibeLockToggle && (
          <button onClick={onVibeLockToggle}
            className={`w-10 h-10 rounded-[10px] flex items-center justify-center transition ${vibeLock?.active ? "" : "btn-icon btn-icon-purple"}`}
            style={{
              background: vibeLock?.active ? "rgba(168, 112, 240, 0.12)" : "var(--bg-muted)",
              border: vibeLock?.active ? "1px solid rgba(168, 112, 240, 0.25)" : "none",
              color: vibeLock?.active ? "#a870f0" : "var(--text-muted)",
            }}
            title={vibeLock?.active ? "Unlock vibe (V)" : "Lock to this song's vibe (V)"}>
            {vibeLock?.active ? <Lock size={16} /> : <Unlock size={16} />}
          </button>
        )}
      </div>
    </div>
  );
}

export default PlayerControls;
