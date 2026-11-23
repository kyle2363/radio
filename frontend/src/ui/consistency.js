// Shared look-and-feel values so every screen draws moods and status the same way.

// One mood palette. Matches the mood buttons and the ambient glow.
export const MOODS = [
  { id: "neutral", label: "Neutral", color: "#7a8aaa" },
  { id: "chill", label: "Chill", color: "#4eccc4" },
  { id: "energetic", label: "Energy", color: "#ff7840" },
  { id: "focus", label: "Focus", color: "#b460f0" },
  { id: "melancholy", label: "Melancholy", color: "#7890f0" },
  { id: "discovery", label: "Discovery", color: "#50c870" },
];

export const MOOD_COLORS = Object.fromEntries(MOODS.map((m) => [m.id, m.color]));

export function moodColor(mood) {
  return MOOD_COLORS[String(mood || "").toLowerCase()] || MOOD_COLORS.neutral;
}

const HOSTING_TITLES = {
  available: "Remote hosting is available",
  starting_gateway: "Remote hosting is starting the secure gateway",
  starting_tunnel: "Remote hosting is starting the Cloudflare tunnel",
  verifying_endpoint: "Remote hosting is verifying the endpoint",
  reconnecting: "Remote hosting is reconnecting",
  stopping: "Remote hosting is stopping",
  discovery_error: "Remote hosting could not publish its address",
  error: "Remote hosting needs attention",
};

// Header pill for Cloudflare remote hosting. Hidden (null) while hosting is off.
export function remoteHostingPill(status) {
  const state = status?.state;
  if (!state || state === "off") return null;
  const tone = state === "available" ? "ok" : state === "error" || state === "discovery_error" ? "err" : "warn";
  return { tone, label: "Remote", title: HOSTING_TITLES[state] || `Remote hosting: ${state}` };
}
