import React from "react";
import HeaderStatusPill from "./HeaderStatusPill";

function VibeLockBadge({ vibeLock, currentSong }) {
  if (!vibeLock?.active) return null;
  return (
    <HeaderStatusPill dotColor="#a870f0">
      <span>Locked</span>
      {currentSong && (
        <span className="truncate max-w-[160px] opacity-80"> – {currentSong.artist} – {currentSong.title}</span>
      )}
      {vibeLock.songs_remaining != null && (
        <span className="opacity-60 shrink-0"> ({vibeLock.songs_remaining})</span>
      )}
    </HeaderStatusPill>
  );
}

export default VibeLockBadge;
