import React from "react";
import HeaderStatusPill from "./HeaderStatusPill";

const LABELS = {
  explorer: "Explorer",
  comfort_listener: "Comfort",
  deep_listener: "Deep Listener",
  mood_listener: "Mood listener",
};

function ArchetypeBadge({ archetype }) {
  if (!archetype) return null;
  return (
    <HeaderStatusPill dotColor="#4eccc4">
      {LABELS[archetype] || "Listener"}
    </HeaderStatusPill>
  );
}

export default ArchetypeBadge;
