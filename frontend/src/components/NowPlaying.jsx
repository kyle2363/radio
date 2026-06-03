import React, { useState, useEffect, useRef, useCallback } from "react";
import * as api from "../api";
import WaveformViz from "./WaveformViz";
import { ChevronRight, ChevronDown } from "lucide-react";

function MarqueeText({ text, className, style }) {
  const containerRef = useRef(null);
  const textRef = useRef(null);
  const [overflow, setOverflow] = useState(false);

  const checkOverflow = useCallback(() => {
    const c = containerRef.current, t = textRef.current;
    if (c && t) setOverflow(t.scrollWidth > c.clientWidth + 2);
  }, []);

  useEffect(() => { checkOverflow(); }, [text, checkOverflow]);
  useEffect(() => { window.addEventListener("resize", checkOverflow); return () => window.removeEventListener("resize", checkOverflow); }, [checkOverflow]);

  const tx = overflow ? (() => {
    const c = containerRef.current, t = textRef.current;
    if (!c || !t) return 0;
    const gap = 80;
    return -(t.scrollWidth - c.clientWidth + gap);
  })() : 0;

  const anim = overflow
    ? `marquee-slide ${Math.max(4, Math.abs(tx) / 35)}s linear 1s 2`
    : "none";

  return (
    <>
      <div ref={containerRef} className={className} style={{ ...style, overflow: "hidden", whiteSpace: "nowrap" }}>
        <span ref={textRef} style={{ display: "inline-block", animation: anim, "--tx": `${tx}px` }}>
          {text}
        </span>
      </div>
    </>
  );
}

const STYLES = ["orbit", "pulse", "float"];

function NowPlaying({ song, isPlaying, whyReason, queue = [], audioRms, talkSegmentActive, positionMs = 0, durationMs = 0, compact = false }) {
  const [showWhy, setShowWhy] = useState(false);
  const [trajectory, setTrajectory] = useState(null);
  const [similar, setSimilar] = useState([]);
  const [showSimilar, setShowSimilar] = useState(false);
  const [showLyrics, setShowLyrics] = useState(false);
  const [vizStyle, setVizStyle] = useState(0);
  const [animId, setAnimId] = useState(0);
  const lyricsRef = useRef(null);
  const activeLineRef = useRef(null);

  // Auto-scroll lyrics line based on playback position
  useEffect(() => {
    if (!showLyrics || !song?.lyrics || !durationMs) return;
    const lines = song.lyrics.split("\n").filter(Boolean);
    if (lines.length === 0) return;
    const msPerLine = durationMs / lines.length;
    const activeIndex = Math.min(
      Math.floor(positionMs / msPerLine),
      lines.length - 1
    );
    // Scroll active line into view
    if (activeLineRef.current) {
      activeLineRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [positionMs, showLyrics, song?.lyrics, durationMs]);

  useEffect(() => {
    if (!song?.id) return;
    api.fetchEnergyTrajectory().then(setTrajectory).catch(() => {});
    setAnimId((v) => v + 1);
  }, [song?.id]);

  const fetchSimilar = async () => {
    if (!song?.id) return;
    try {
      const data = await api.fetchSimilarSongs(song.id);
      if (Array.isArray(data)) setSimilar(data);
    } catch (e) {}
  };

  const toggleSimilar = () => {
    if (!showSimilar && similar.length === 0) fetchSimilar();
    setShowSimilar(!showSimilar);
  };

  const playSimilar = async (songId) => {
    try { await api.playSong(songId); } catch (e) {}
  };

  if (!song && !talkSegmentActive) {
    if (compact) {
      return (
        <div className="text-center py-10" style={{ color: "var(--text-muted)" }}>
          <div className="text-sm">Nothing playing</div>
        </div>
      );
    }
    return (
      <div className="rounded-xl border bg-surface/50 p-8 text-center" style={{ borderColor: "var(--border)" }}>
        <div className="text-lg mb-2" style={{ color: "var(--text-muted)" }}>Nothing playing</div>
        <div className="text-sm" style={{ color: "var(--text-muted)" }}>Scan your library to get started</div>
      </div>
    );
  }

  // Talk segment overlay (shown instead of song info when a segment is active)
  if (talkSegmentActive) {
    const segType = talkSegmentActive.segment_type;
    const labels = {
      caller: "Caller on the line",
      dj_monologue: "DJ Monologue",
      guest_takeover_intro: "Guest takeover",
      guest_takeover_outro: "Handing back",
    };
    const icons = {
      caller: "📞",
      dj_monologue: "🎙️",
      guest_takeover_intro: "🎧️",
      guest_takeover_outro: "🎧️",
    };
    const talkStyle = compact
      ? { borderTop: "1px solid var(--border)", paddingTop: "10px", marginTop: "auto" }
      : { background: "var(--bg-surface)", border: "1px solid var(--border)" };
    return (
      <div className={`fade-enter relative overflow-hidden ${compact ? '' : 'rounded-xl p-5'}`} style={talkStyle}>
        <div className="flex items-center gap-4">
          <div className="flex-shrink-0 w-14 h-14 rounded-full flex items-center justify-center text-2xl"
            style={{ background: "var(--accent-muted)" }}>
            {icons[segType] || "📢"}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
              {labels[segType] || "Radio Talk"}
            </div>
            <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
              {talkSegmentActive.reason || ""}
            </div>
            {talkSegmentActive.duration_secs > 0 && (
              <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                ~{Math.round(talkSegmentActive.duration_secs)}s
              </div>
            )}
          </div>
          <div className="flex-shrink-0">
            <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          </div>
        </div>
      </div>
    );
  }

  const TRAJ_ICONS = { rising: "↗", falling: "↘", stable: "→" };
  const TRAJ_COLORS = { rising: "text-orange-400", falling: "text-blue-400", stable: "text-gray-500" };

  const innerGrid = (
    <div className={compact ? "now-playing-grid" : "flex gap-4 items-start"}>
      {/* Left: canvas + pill style selector */}
      <div className="flex-shrink-0">
        <WaveformViz song={song} queue={queue} styleIndex={vizStyle} audioRms={audioRms} size={compact ? 168 : undefined} />
          <div className="flex justify-center mt-2">
            <div className="mood-seg" style={{ padding: "2px", gap: "2px" }}>
              {STYLES.map((name, i) => (
                <button key={i} onClick={() => setVizStyle(i)}
                  className={`mood-opt ${i === vizStyle ? "active" : ""}`}
                  style={{ padding: "4px 8px", fontSize: "10px", letterSpacing: "0.06em" }}>
                  {name}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Right: song info */}
        <div className="flex-1 min-w-0 flex flex-col gap-2 pt-1">
          <div className="space-y-0.5">
            <MarqueeText text={song.title || "Unknown Title"}
              className="text-2xl font-bold"
              style={{ color: "var(--text-primary)", maxWidth: "100%", lineHeight: "1.1", letterSpacing: "-0.02em" }} />
            <MarqueeText text={song.artist || "Unknown Artist"}
              className="text-sm"
              style={{ color: "var(--text-secondary)", maxWidth: "100%", fontWeight: 400 }} />
          </div>

          <div className="flex flex-wrap gap-1.5">
            {song.bpm && (
              <span className="badge b-dim">{song.bpm} BPM</span>
            )}
            {song.energy != null && (
              <span className="badge b-teal">E {(song.energy * 100).toFixed(0)}%</span>
            )}
            {song.valence != null && (
              <span className="badge b-blue">V {(song.valence * 100).toFixed(0)}%</span>
            )}
            {trajectory && (
              <span className="badge b-dim" style={{ borderColor: "rgba(255,255,255,0.08)" }}>
                {TRAJ_ICONS[trajectory.direction] || ""} {trajectory.direction}
              </span>
            )}
          </div>

          {song.ai_description && (
            <p className="text-xs leading-relaxed line-clamp-3" style={{ color: "var(--text-muted)" }}>{song.ai_description}</p>
          )}

          {song.lyrics && (
            <div>
              <button onClick={() => setShowLyrics(!showLyrics)} className="text-xs flex items-center gap-1 transition" style={{ color: "var(--text-muted)" }}>
                {showLyrics ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                Lyrics
              </button>
              {showLyrics && (
                <div ref={lyricsRef} className="mt-1.5 px-3 py-2 rounded-lg max-h-48 overflow-y-auto text-sm leading-relaxed" style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)", border: "1px solid var(--border-subtle)" }}>
                  {(() => {
                    const lines = song.lyrics.split("\n").filter(Boolean);
                    if (lines.length === 0) return <span className="text-xs italic">No lyrics available</span>;
                    const msPerLine = durationMs > 0 ? durationMs / lines.length : 0;
                    const currentLine = msPerLine > 0
                      ? Math.min(Math.floor(positionMs / msPerLine), lines.length - 1)
                      : -1;
                    return lines.map((line, i) => (
                      <div key={i} ref={i === currentLine ? activeLineRef : null}
                        className="transition-colors duration-200"
                        style={{
                          color: i === currentLine ? "var(--accent)" : "var(--text-secondary)",
                          fontWeight: i === currentLine ? 600 : 400,
                        }}>
                        {line}
                      </div>
                    ));
                  })()}
                </div>
              )}
            </div>
          )}

          <button onClick={toggleSimilar} className="text-xs flex items-center gap-1 transition self-start link-accent">
            {showSimilar ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Similar songs
          </button>

          {whyReason && (
            <div>
              <button onClick={() => setShowWhy(!showWhy)} className="text-xs flex items-center gap-1 transition" style={{ color: "var(--text-muted)" }}>
                {showWhy ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                Why this song?
              </button>
              {showWhy && (
                <div className="mt-1.5 px-3 py-2 rounded-lg text-sm" style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)", border: "1px solid var(--border-subtle)" }}>
                  {whyReason}
                </div>
              )}
            </div>
          )}

          {showSimilar && similar.length > 0 && (
            <div className="space-y-1 mt-1">
              {similar.map((s) => (
                <div key={s.song_id} className="flex items-center gap-2 px-2 py-1 rounded cursor-pointer transition group row-hover" onClick={() => playSimilar(s.song_id)}>
                  <span className="text-xs truncate flex-1" style={{ color: "var(--text-secondary)" }}>{s.artist} - {s.title}</span>
                  <span className="text-xs" style={{ color: "var(--text-muted)" }}>{Math.round(s.score * 100)}%</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );

  if (compact) return <>{innerGrid}</>;

  return (
    <div key={animId} className="rounded-xl p-5 song-enter song-enter-active relative overflow-hidden" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
      {innerGrid}
    </div>
  );
}

export default NowPlaying;
