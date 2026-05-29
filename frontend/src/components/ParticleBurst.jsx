import React, { useState, useEffect } from "react";

const PARTICLE_COUNT = 6;

function randomBetween(min, max) {
  return Math.random() * (max - min) + min;
}

export default function ParticleBurst({ active, color = "#ec4899", onComplete }) {
  const [particles, setParticles] = useState([]);

  useEffect(() => {
    if (!active) return;
    const newParticles = Array.from({ length: PARTICLE_COUNT }, (_, i) => ({
      id: i,
      px: randomBetween(-24, 24),
      py: randomBetween(-28, -4),
      size: randomBetween(3, 6),
      delay: randomBetween(0, 80),
    }));
    setParticles(newParticles);
    const timer = setTimeout(() => {
      setParticles([]);
      onComplete?.();
    }, 600);
    return () => clearTimeout(timer);
  }, [active, onComplete]);

  if (particles.length === 0) return null;

  return (
    <span className="absolute inset-0 pointer-events-none overflow-visible">
      {particles.map((p) => (
        <span
          key={p.id}
          className="particle"
          style={{
            "--px": `${p.px}px`,
            "--py": `${p.py}px`,
            width: p.size,
            height: p.size,
            backgroundColor: color,
            left: "50%",
            top: "50%",
            animationDelay: `${p.delay}ms`,
          }}
        />
      ))}
    </span>
  );
}
