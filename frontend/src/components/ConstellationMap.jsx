import React, { useRef, useEffect, useState, useCallback, useMemo } from "react";
import { Play, Plus, X, Sparkles } from "lucide-react";
import * as api from "../api";

const MOOD_HUES = { energetic: 28, melancholy: 215, chill: 168, focus: 275, neutral: 210, discovery: 140 };
const MOOD_LABELS = { energetic: "Energetic", melancholy: "Melancholy", chill: "Chill", focus: "Focus", neutral: "Neutral", discovery: "Discovery" };

function moodColor(mood, alpha = 1) {
  const h = MOOD_HUES[mood] ?? 200;
  return `hsla(${h}, 70%, 65%, ${alpha})`;
}

function moodColorRgb(mood) {
  const h = MOOD_HUES[mood] ?? 200;
  return `hsl(${h}, 70%, 65%)`;
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function hsla(h, s, l, a) {
  return `hsla(${h}, ${s}%, ${l}%, ${a})`;
}

function PocketTooltip({ pocket, x, y }) {
  if (!pocket) return null;
  return (
    <div className="constellation-tooltip" style={{ left: x + 14, top: y - 10 }}>
      <div style={{ fontWeight: 600, marginBottom: 4, color: moodColorRgb(pocket.primary_mood) }}>
        {MOOD_LABELS[pocket.primary_mood] || pocket.primary_mood}
      </div>
      <div style={{ color: "var(--text-secondary)", fontSize: 11, lineHeight: 1.6 }}>
        <div>{pocket.song_count} songs</div>
        <div>Energy: {pocket.energy_avg.toFixed(2)} · Valence: {pocket.valence_avg.toFixed(2)}</div>
        <div>Signals: {pocket.signal_count} · Weight: {(pocket.weight * 100).toFixed(0)}%</div>
      </div>
    </div>
  );
}

function SongListPanel({ pocket, songs, loading, onPlaySong, onAddToQueue, onClose }) {
  if (!pocket) return null;
  return (
    <div style={{
      width: 320, flexShrink: 0, display: "flex", flexDirection: "column",
      background: "var(--bg-surface)", borderLeft: "1px solid var(--border)",
      height: "100%", overflow: "hidden",
    }}>
      <div style={{
        padding: "16px 16px 12px", borderBottom: "1px solid var(--border-subtle)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14, color: moodColorRgb(pocket.primary_mood) }}>
            {MOOD_LABELS[pocket.primary_mood] || pocket.primary_mood} Pocket
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {pocket.song_count} songs · E {pocket.energy_avg.toFixed(2)} · V {pocket.valence_avg.toFixed(2)}
          </div>
        </div>
        <button onClick={onClose} style={{
          background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer",
          padding: 4, borderRadius: 4, display: "flex",
        }}>
          <X size={16} />
        </button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
        {loading && (
          <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
            Loading songs...
          </div>
        )}
        {!loading && songs.length === 0 && (
          <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
            No songs in this pocket
          </div>
        )}
        {songs.map((song) => (
          <div key={song.id} className="constellation-song-row" style={{ padding: "6px 16px" }}>
            <button
              onClick={() => onPlaySong(song.id)}
              style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", padding: 2, display: "flex", flexShrink: 0 }}
              title="Play now"
            >
              <Play size={14} />
            </button>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, color: "var(--text-primary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {song.title || "Unknown"}
              </div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {song.artist || "Unknown"}
              </div>
            </div>
            <div style={{ display: "flex", gap: 3, flexShrink: 0, alignItems: "center" }}>
              <div style={{ width: 24 }}>
                <div style={{ height: 2, borderRadius: 1, background: "var(--bg-muted)", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${(song.energy || 0.5) * 100}%`, background: "#fb923c", borderRadius: 1 }} />
                </div>
              </div>
              <div style={{ width: 24 }}>
                <div style={{ height: 2, borderRadius: 1, background: "var(--bg-muted)", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${(song.valence || 0.5) * 100}%`, background: "#60a5fa", borderRadius: 1 }} />
                </div>
              </div>
            </div>
            <button
              onClick={() => onAddToQueue(song.id)}
              style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 2, display: "flex", flexShrink: 0 }}
              title="Add to queue"
            >
              <Plus size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ConstellationMap({ constellations, onPlaySong, onAddToQueue, onClose }) {
  const canvasRef = useRef(null);
  const tickRef = useRef(0);
  const rafRef = useRef(null);
  const animStateRef = useRef(new Map());
  const mouseRef = useRef({ x: 0, y: 0 });
  const [hoveredPocket, setHoveredPocket] = useState(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [selectedPocket, setSelectedPocket] = useState(null);
  const [selectedSongs, setSelectedSongs] = useState([]);
  const [songsLoading, setSongsLoading] = useState(false);

  const pockets = constellations?.pockets || [];
  const similarityMatrix = constellations?.similarity_matrix || [];
  const activePocketId = constellations?.active_pocket;

  const EDGE_THRESHOLD = 0.25;

  const pocketPositions = useMemo(() => {
    const map = new Map();
    pockets.forEach((p) => {
      map.set(p.id, {
        targetX: 0.12 + p.energy_avg * 0.76,
        targetY: 0.88 - p.valence_avg * 0.76,
        targetAlpha: 1,
        targetRadius: 18 + p.weight * 80,
      });
    });
    return map;
  }, [pockets]);

  useEffect(() => {
    const state = animStateRef.current;
    const existingIds = new Set(pockets.map((p) => p.id));

    for (const [id, pos] of pocketPositions) {
      if (!state.has(id)) {
        const cx = pockets.length > 1
          ? pockets.reduce((s, p) => s + (0.12 + p.energy_avg * 0.76), 0) / pockets.length
          : 0.5;
        const cy = pockets.length > 1
          ? pockets.reduce((s, p) => s + (0.88 - p.valence_avg * 0.76), 0) / pockets.length
          : 0.5;
        state.set(id, {
          x: cx, y: cy, alpha: 0, radius: 0,
          targetX: pos.targetX, targetY: pos.targetY,
          targetAlpha: pos.targetAlpha, targetRadius: pos.targetRadius,
        });
      } else {
        const s = state.get(id);
        s.targetX = pos.targetX;
        s.targetY = pos.targetY;
        s.targetAlpha = pos.targetAlpha;
        s.targetRadius = pos.targetRadius;
      }
    }

    for (const [id] of state) {
      if (!existingIds.has(id)) {
        const s = state.get(id);
        s.targetAlpha = 0;
      }
    }
  }, [pockets, pocketPositions]);

  const loadPocketSongs = useCallback(async (pocket) => {
    if (!pocket?.song_ids?.length) {
      setSelectedSongs([]);
      return;
    }
    setSongsLoading(true);
    try {
      const results = await Promise.all(
        pocket.song_ids.slice(0, 50).map((id) => api.fetchSong(id).catch(() => null))
      );
      setSelectedSongs(results.filter(Boolean));
    } catch {
      setSelectedSongs([]);
    }
    setSongsLoading(false);
  }, []);

  const handlePocketClick = useCallback((pocket) => {
    setSelectedPocket(pocket);
    loadPocketSongs(pocket);
  }, [loadPocketSongs]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.parentElement.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      canvas.style.width = rect.width + "px";
      canvas.style.height = rect.height + "px";
    };
    resize();
    window.addEventListener("resize", resize);

    const draw = () => {
      const state = animStateRef.current;
      const w = canvas.width / (window.devicePixelRatio || 1);
      const h = canvas.height / (window.devicePixelRatio || 1);
      ctx.clearRect(0, 0, w, h);

      ctx.fillStyle = "var(--bg-page)";
      ctx.fillRect(0, 0, w, h);

      for (const [id, s] of state) {
        s.x = lerp(s.x, s.targetX, 0.06);
        s.y = lerp(s.y, s.targetY, 0.06);
        s.alpha = lerp(s.alpha, s.targetAlpha, 0.06);
        s.radius = lerp(s.radius, s.targetRadius, 0.06);
      }

      ctx.lineWidth = 1;
      for (let i = 0; i < pockets.length; i++) {
        for (let j = i + 1; j < pockets.length; j++) {
          const sim = similarityMatrix[i]?.[j] ?? 0;
          if (sim < EDGE_THRESHOLD) continue;
          const si = state.get(pockets[i].id);
          const sj = state.get(pockets[j].id);
          if (!si || !sj) continue;
          const edgeAlpha = ((sim - EDGE_THRESHOLD) / (1 - EDGE_THRESHOLD)) * 0.35;
          const edgeWidth = 1 + sim * 1.5;
          const ci = MOOD_HUES[pockets[i].primary_mood] ?? 200;
          const cj = MOOD_HUES[pockets[j].primary_mood] ?? 200;
          const blendH = (ci + cj) / 2;
          ctx.strokeStyle = hsla(blendH, 60, 55, edgeAlpha * Math.min(si.alpha, sj.alpha));
          ctx.lineWidth = edgeWidth;
          ctx.beginPath();
          ctx.moveTo(si.x * w, si.y * h);
          ctx.lineTo(sj.x * w, sj.y * h);
          ctx.stroke();
        }
      }

      const now = tickRef.current;
      const mouse = mouseRef.current;
      let newHovered = null;

      for (const pocket of pockets) {
        const s = state.get(pocket.id);
        if (!s || s.alpha < 0.01) continue;
        const cx = s.x * w;
        const cy = s.y * h;
        const isActive = pocket.id === activePocketId;

        const dx = mouse.x - cx;
        const dy = mouse.y - cy;
        if (dx * dx + dy * dy < s.radius * s.radius) {
          newHovered = pocket;
        }

        if (isActive) {
          const pulse = Math.sin(now * 2.5) * 0.3 + 0.5;
          const glowR = s.radius + 10 + Math.sin(now * 2) * 6;
          const glow = ctx.createRadialGradient(cx, cy, s.radius * 0.5, cx, cy, glowR);
          const h = MOOD_HUES[pocket.primary_mood] ?? 200;
          glow.addColorStop(0, hsla(h, 75, 60, pulse * 0.35 * s.alpha));
          glow.addColorStop(0.6, hsla(h, 70, 55, pulse * 0.12 * s.alpha));
          glow.addColorStop(1, hsla(h, 65, 50, 0));
          ctx.beginPath();
          ctx.arc(cx, cy, glowR, 0, Math.PI * 2);
          ctx.fillStyle = glow;
          ctx.fill();
        }

        const h = MOOD_HUES[pocket.primary_mood] ?? 200;
        const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, s.radius);
        grad.addColorStop(0, hsla(h, 75, 65, 0.85 * s.alpha));
        grad.addColorStop(0.5, hsla(h, 65, 58, 0.45 * s.alpha));
        grad.addColorStop(1, hsla(h, 55, 50, 0));
        ctx.beginPath();
        ctx.arc(cx, cy, s.radius, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();

        ctx.beginPath();
        ctx.arc(cx, cy, s.radius * 0.3, 0, Math.PI * 2);
        ctx.fillStyle = hsla(h, 80, 75, 0.9 * s.alpha);
        ctx.fill();

        if (isActive) {
          ctx.beginPath();
          ctx.arc(cx, cy, s.radius * 0.3, 0, Math.PI * 2);
          ctx.strokeStyle = hsla(h, 80, 80, 0.6 * s.alpha);
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }

        ctx.font = "600 10px 'DM Sans', sans-serif";
        ctx.textAlign = "center";
        ctx.fillStyle = hsla(h, 65, 70, 0.9 * s.alpha);
        ctx.fillText(MOOD_LABELS[pocket.primary_mood] || pocket.primary_mood, cx, cy + s.radius + 14);
        ctx.font = "10px 'DM Sans', sans-serif";
        ctx.fillStyle = hsla(h, 50, 60, 0.7 * s.alpha);
        ctx.fillText(`${pocket.song_count} songs`, cx, cy + s.radius + 26);
      }

      setHoveredPocket(newHovered);
      tickRef.current += 0.016;
      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);

    return () => {
      window.removeEventListener("resize", resize);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [pockets, similarityMatrix, activePocketId]);

  const handleMouseMove = useCallback((e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    setTooltipPos({ x: e.clientX, y: e.clientY });
  }, []);

  const handleClick = useCallback((e) => {
    if (hoveredPocket) {
      handlePocketClick(hoveredPocket);
    } else {
      setSelectedPocket(null);
      setSelectedSongs([]);
    }
  }, [hoveredPocket, handlePocketClick]);

  return (
    <div style={{ display: "flex", height: "100%", width: "100%" }}>
      <div style={{ flex: 1, position: "relative", minWidth: 0 }}>
        <div style={{
          position: "absolute", top: 12, left: 16, zIndex: 2,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <Sparkles size={16} style={{ color: "var(--accent)" }} />
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
            Taste Constellations
          </span>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {pockets.length} pocket{pockets.length !== 1 ? "s" : ""}
          </span>
        </div>
        <button onClick={onClose} style={{
          position: "absolute", top: 12, right: 16, zIndex: 2,
          background: "var(--bg-elevated)", border: "1px solid var(--border)",
          borderRadius: 6, padding: "4px 8px", cursor: "pointer",
          color: "var(--text-muted)", fontSize: 12, display: "flex", alignItems: "center", gap: 4,
        }}>
          <X size={14} />
        </button>
        <canvas
          ref={canvasRef}
          onMouseMove={handleMouseMove}
          onClick={handleClick}
          style={{ display: "block", width: "100%", height: "100%", cursor: hoveredPocket ? "pointer" : "default" }}
        />
        {hoveredPocket && (
          <PocketTooltip pocket={hoveredPocket} x={tooltipPos.x} y={tooltipPos.y} />
        )}
        <div style={{
          position: "absolute", bottom: 16, left: 16, zIndex: 2,
          display: "flex", gap: 12, alignItems: "center",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: moodColorRgb("neutral"), opacity: 0.7 }} />
            <span style={{ fontSize: 9, color: "var(--text-muted)" }}>Low</span>
          </div>
          <div style={{ fontSize: 9, color: "var(--text-muted)" }}>Energy →</div>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 14, height: 14, borderRadius: "50%", background: moodColorRgb("neutral"), opacity: 0.7 }} />
            <span style={{ fontSize: 9, color: "var(--text-muted)" }}>High</span>
          </div>
          <div style={{ width: 1, height: 12, background: "var(--border)" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 20, height: 2, background: "var(--text-muted)", opacity: 0.4 }} />
            <span style={{ fontSize: 9, color: "var(--text-muted)" }}>Sim ≥ 0.25</span>
          </div>
          <div style={{ width: 1, height: 12, background: "var(--border)" }} />
          <div style={{ fontSize: 9, color: "var(--text-muted)" }}>↑ Valence</div>
        </div>
      </div>
      <SongListPanel
        pocket={selectedPocket}
        songs={selectedSongs}
        loading={songsLoading}
        onPlaySong={onPlaySong}
        onAddToQueue={onAddToQueue}
        onClose={() => { setSelectedPocket(null); setSelectedSongs([]); }}
      />
    </div>
  );
}
