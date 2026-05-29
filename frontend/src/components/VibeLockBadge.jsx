import React from "react";
import { Lock } from "lucide-react";

function VibeLockBadge({ vibeLock, currentSong }) {
  if (!vibeLock?.active) return null;
  return (
    <div className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-[11px]"
      style={{ background: "rgba(168,112,240,0.08)", color: "#a870f0" }}>
      <Lock size={10} />
      <span>Locked</span>
      {currentSong && (
        <span className="truncate max-w-[120px] opacity-80">{currentSong.artist} – {currentSong.title}</span>
      )}
      {vibeLock.songs_remaining != null && (
        <span className="opacity-60">({vibeLock.songs_remaining} left)</span>
      )}
    </div>
  );
}

export default VibeLockBadge;
