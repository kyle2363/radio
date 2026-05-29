import React from "react";

const ICONS = {
  explorer: "\uD83E\uDDED",
  comfort_listener: "\uD83C\uDFE0",
  deep_listener: "\uD83C\uDFA7",
  mood_listener: "\uD83C\uDFAD",
};

const LABELS = {
  explorer: "Explorer",
  comfort_listener: "Comfort",
  deep_listener: "Deep Listener",
  mood_listener: "Mood Listener",
};

function ArchetypeBadge({ archetype }) {
  if (!archetype) return null;
  return (
    <div className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-[11px]"
      style={{ background: "rgba(255,255,255,0.03)", color: "var(--text-muted)" }}>
      <span className="text-xs">{ICONS[archetype] || "\uD83C\uDFB5"}</span>
      <span>{LABELS[archetype] || "Listener"}</span>
    </div>
  );
}

export default ArchetypeBadge;
