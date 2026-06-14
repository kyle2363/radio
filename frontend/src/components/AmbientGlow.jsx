import React, { useMemo } from "react";

const MOOD_GLOWS = {
  energetic:  { top: [255, 160, 60],  bottom: [200, 80, 40],  opacity: 0.10 },
  melancholy: { top: [80, 120, 220],  bottom: [40, 60, 140],  opacity: 0.08 },
  chill:      { top: [60, 200, 180],  bottom: [30, 120, 140], opacity: 0.07 },
  focus:      { top: [140, 80, 200],  bottom: [80, 40, 140],  opacity: 0.06 },
  neutral:    { top: [60, 140, 200],  bottom: [30, 80, 140],  opacity: 0.05 },
  discovery:  { top: [100, 200, 100], bottom: [60, 140, 80],  opacity: 0.07 },
};

function AmbientGlow({ song, mood = "neutral" }) {
  const palette = MOOD_GLOWS[mood] || MOOD_GLOWS.neutral;
  const energy = song?.energy ?? 0.5;
  const intensity = Math.max(0.02, palette.opacity * (0.5 + energy * 0.8));

  const topGrad = useMemo(
    () => `radial-gradient(ellipse 700px 400px at 50% -120px, rgba(${palette.top.join(",")},${intensity}) 0%, transparent 70%)`,
    [palette.top, intensity]
  );
  const botGrad = useMemo(
    () => `radial-gradient(ellipse 500px 300px at 50% 100%, rgba(${palette.bottom.join(",")},${intensity * 0.7}) 0%, transparent 70%)`,
    [palette.bottom, intensity]
  );

  const prefersReducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <div className="fixed inset-0 pointer-events-none z-0" style={{ transition: prefersReducedMotion ? "none" : "opacity 0.8s ease" }}>
      <div className="absolute inset-0" style={{ background: topGrad }} />
      <div className="absolute inset-0" style={{ background: botGrad }} />
    </div>
  );
}

export default AmbientGlow;
