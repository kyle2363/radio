import React from "react";

const VIEW = "0 0 180 180";

// All colors driven by CSS variables defined in global.css under :root and
// [data-theme="light"]. This lets the icons auto-adapt to the app's theme
// (and, via the favicon media query, to the OS theme in browser tabs).

export const ICON_STYLES = [
  { id: "drift",    label: "Drift",    desc: "One wave, infinite feeling" },
  { id: "orbit",    label: "Orbit",    desc: "Music in perpetual motion" },
  { id: "pulse",    label: "Pulse",    desc: "Broadcasting your frequency" },
  { id: "spectrum", label: "Spectrum", desc: "Every mood, its own frequency" },
];

export const DEFAULT_ICON_STYLE = "drift";

export function Drift({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox={VIEW} xmlns="http://www.w3.org/2000/svg" aria-label="Drift" style={{ display: "block" }}>
      <path
        d="M 30 90 C 50 62, 70 62, 90 90 C 110 118, 130 118, 150 90"
        style={{ stroke: "var(--brand-drift)", strokeOpacity: 0.15 }}
        strokeWidth="16" fill="none" strokeLinecap="round"
      />
      <path
        d="M 30 90 C 50 62, 70 62, 90 90 C 110 118, 130 118, 150 90"
        style={{ stroke: "var(--brand-drift)" }}
        strokeWidth="5" fill="none" strokeLinecap="round"
      />
    </svg>
  );
}

export function Orbit({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox={VIEW} xmlns="http://www.w3.org/2000/svg" aria-label="Orbit" style={{ display: "block" }}>
      <g transform="rotate(-22 90 90)">
        <ellipse cx="90" cy="90" rx="44" ry="15"
          style={{ stroke: "var(--brand-orbit)", strokeOpacity: 0.3 }}
          strokeWidth="3" fill="none" />
        <ellipse cx="90" cy="90" rx="26" ry="9"
          style={{ stroke: "var(--brand-orbit)" }}
          strokeWidth="4" fill="none" />
      </g>
      <circle cx="90" cy="90" r="10" style={{ fill: "var(--brand-orbit)" }} />
    </svg>
  );
}

export function Pulse({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox={VIEW} xmlns="http://www.w3.org/2000/svg" aria-label="Pulse" style={{ display: "block" }}>
      <circle cx="90" cy="90" r="40"
        style={{ stroke: "var(--brand-pulse-1)" }} strokeWidth="3" fill="none" />
      <circle cx="90" cy="90" r="18"
        style={{ stroke: "var(--brand-pulse-2)" }} strokeWidth="3.5" fill="none" />
      <circle cx="90" cy="90" r="6" style={{ fill: "var(--brand-pulse-3)" }} />
    </svg>
  );
}

export function Spectrum({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox={VIEW} xmlns="http://www.w3.org/2000/svg" aria-label="Spectrum" style={{ display: "block" }}>
      <rect x="21"  y="65" width="18" height="50"  rx="9" style={{ fill: "var(--brand-spectrum)" }} />
      <rect x="51"  y="50" width="18" height="80"  rx="9" style={{ fill: "var(--brand-spectrum)" }} />
      <rect x="81"  y="40" width="18" height="100" rx="9" style={{ fill: "var(--brand-spectrum)" }} />
      <rect x="111" y="55" width="18" height="70"  rx="9" style={{ fill: "var(--brand-spectrum)" }} />
      <rect x="141" y="70" width="18" height="40"  rx="9" style={{ fill: "var(--brand-spectrum)" }} />
    </svg>
  );
}

export const BRAND_ICONS = {
  drift: Drift,
  orbit: Orbit,
  pulse: Pulse,
  spectrum: Spectrum,
};

export function BrandIcon({ style, size = 24 }) {
  const C = BRAND_ICONS[style] || Drift;
  return <C size={size} />;
}
