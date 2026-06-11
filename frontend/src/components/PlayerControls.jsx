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

function PlayerControls({ isPlaying, onPlay, onPause, onNext, onHeart, onThumbsDown, onReplay, onClearQueue, positionMs, durationMs, onSeek, vibeLock, onVibeLockToggle, audioRms = 0.5, compact = false, isHearted = false, isThumbedDown = false }) {
  const [isDragging, setIsDragging] = useState(false);
  const [dragProgress, setDragProgress] = useState(0);
  const [heartAnim, setHeartAnim] = useState(false);
  const [dislikeAnim, setDislikeAnim] = useState(false);
  const [playPauseFlip, setPlayPauseFlip] = useState(false);
  const prevPlayingRef = useRef(isPlaying);
  const barRef = useRef(null);
  const ghostRef = useRef(null);

  const progress = durationMs > 0 ? (isDragging ? dragProgress : positionMs) / durationMs : 0;
  const displayProgress = isDragging ? dragProgress : positionMs;

  // Ghost waveform under progress bar
  useEffect(() => {
    const canvas = ghostRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.clientWidth * window.devicePixelRatio;
    const h = canvas.clientHeight * window.devicePixelRatio;
    canvas.width = w;
    canvas.height = h;
    ctx.clearRect(0, 0, w, h);
    const bars = 48;
    const gap = 1 * window.devicePixelRatio;
    const barW = (w - gap * (bars - 1)) / bars;
    for (let i = 0; i < bars; i++) {
      const barH = (0.15 + Math.sin(i * 0.6 + audioRms * 4) * 0.85) * 0.5 + 0.15;
      ctx.fillStyle = "rgba(78, 204, 196, 0.08)";
      ctx.fillRect(i * (barW + gap), h * (1 - barH), barW, h * barH);
    }
  }, [audioRms]);

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

  const handleHeart = () => { if (isHearted) return; setHeartAnim(true); onHeart?.(); };
  const handleDislike = () => { if (isThumbedDown) return; setDislikeAnim(true); onThumbsDown?.(); };

  // Flip animation on play/pause toggle
  useEffect(() => {
    if (prevPlayingRef.current !== isPlaying) {
      setPlayPauseFlip(true);
      const t = setTimeout(() => setPlayPauseFlip(false), 300);
      prevPlayingRef.current = isPlaying;
      return () => clearTimeout(t);
    }
  }, [isPlaying]);

  const seekBar = (
    <div className="flex items-center gap-3 relative">
      <span className="text-xs tabular-nums w-10 text-right" style={{ color: "var(--text-muted)" }}>{formatTime(displayProgress)}</span>
      <div className="flex-1 relative" style={{ height: "16px" }}>
        {!compact && (
          <canvas ref={ghostRef}
            className="absolute inset-0 w-full pointer-events-none"
            style={{ height: "8px", top: "50%", transform: "translateY(-50%)", opacity: 0.6 }} />
        )}
        <div ref={barRef} tabIndex={0} className={`absolute inset-0 cursor-pointer group focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/50 ${isDragging ? "seek-dragging" : ""}`}
          style={{ top: "50%", height: "3px", transform: "translateY(-50%)" }}
          onMouseDown={handleBarMouseDown} onKeyDown={handleBarKeyDown}
          role="slider" aria-label="Seek" aria-valuemin={0} aria-valuemax={durationMs} aria-valuenow={positionMs}>
          <div className="w-full h-full rounded-full relative" style={{ background: "var(--border)" }}>
            <div className="h-full rounded-full relative transition-none" style={{ width: `${progress * 100}%`, background: "linear-gradient(90deg, #3ab8b0, #4eccc4)" }}>
              <div className={`absolute right-0 top-1/2 -translate-y-1/2 rounded-full transition-all ${isDragging ? "w-3.5 h-3.5 opacity-100" : "w-2.5 h-2.5 opacity-0 group-hover:opacity-100"}`}
                style={{ background: "var(--accent)", boxShadow: isDragging ? "0 0 12px rgba(78,204,196,0.5)" : "none" }} />
            </div>
          </div>
        </div>
        {/* Drag tooltip */}
        {isDragging && (
          <div style={{
            position: "absolute", top: -28,
            left: `${(dragProgress / (durationMs || 1)) * 100}%`,
            transform: "translateX(-50%)",
            padding: "2px 8px", borderRadius: 4, fontSize: 10,
            background: "var(--bg-elevated)", border: "1px solid var(--border)",
            color: "var(--accent)", fontVariantNumeric: "tabular-nums",
            pointerEvents: "none", whiteSpace: "nowrap",
            boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
          }}>
            {formatTime(dragProgress)}
          </div>
        )}
      </div>
      <span className="text-xs tabular-nums w-10" style={{ color: "var(--text-muted)" }}>{formatTime(durationMs)}</span>
    </div>
  );

  const controls = (
    <div className="flex justify-center items-center gap-1">
      <button onClick={onReplay} className="ctrl-btn ctrl-ter" title="Replay (R)">
        <RotateCcw size={13} />
      </button>
      <button onClick={isPlaying ? onPause : onPlay} className="ctrl-btn ctrl-main" title="Play/Pause (Space)"
        style={{ background: "var(--accent-muted)", border: "1px solid rgba(78,204,196,0.3)", color: "var(--accent)" }}>
        <span className={`icon-wrap ${playPauseFlip ? "flipping" : ""}`}>
          {isPlaying ? <Pause size={22} /> : <Play size={22} />}
        </span>
      </button>
      <button onClick={onNext} className="ctrl-btn ctrl-sec" title="Next (N)">
        <SkipForward size={15} />
      </button>
      <button onClick={handleHeart}
        className={`ctrl-btn ctrl-ter ${heartAnim ? "heart-pop" : ""}`}
        onAnimationEnd={() => setHeartAnim(false)}
        disabled={isHearted}
        title={isHearted ? "Already hearted (H)" : "Heart (H)"}
        style={{ color: "#f472b6", opacity: isHearted ? 0.45 : 1, cursor: isHearted ? "default" : undefined }}>
        <Heart size={13} fill={isHearted ? "#f472b6" : "none"} />
        <ParticleBurst active={heartAnim} color="#f078a8" />
      </button>
      <button onClick={handleDislike}
        className={`ctrl-btn ctrl-ter ${dislikeAnim ? "shake" : ""}`}
        onAnimationEnd={() => setDislikeAnim(false)}
        disabled={isThumbedDown}
        title={isThumbedDown ? "Already thumbed down (D)" : "Dislike (D)"}
        style={{ opacity: isThumbedDown ? 0.45 : 1, cursor: isThumbedDown ? "default" : undefined }}>
        <ThumbsDown size={13} fill={isThumbedDown ? "currentColor" : "none"} />
        <ParticleBurst active={dislikeAnim} color="#f09060" />
      </button>
      <div style={{ width: "1px", height: "16px", background: "var(--border)", margin: "0 2px" }} />
      {onVibeLockToggle && (
        <button onClick={onVibeLockToggle} className="ctrl-btn ctrl-ter"
          style={{
            color: vibeLock?.active ? "#a870f0" : "var(--text-muted)",
          }}
          title={vibeLock?.active ? "Unlock vibe (V)" : "Lock to this song's vibe (V)"}>
          {vibeLock?.active ? <Lock size={13} /> : <Unlock size={13} />}
        </button>
      )}
    </div>
  );

  if (compact) return <>{seekBar}{controls}</>;

  return (
    <div className="space-y-3">
      {seekBar}
      {controls}
    </div>
  );
}

export default PlayerControls;
