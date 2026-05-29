import React from "react";
import { AlertTriangle } from "lucide-react";

function SkipStreakIndicator({ skipStreak }) {
  if (!skipStreak || skipStreak < 2) return null;
  return (
    <div className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-[11px]"
      style={{ background: "rgba(240,176,96,0.08)", color: "#f0b060" }}>
      <AlertTriangle size={10} />
      <span>{skipStreak} skips</span>
    </div>
  );
}

export default SkipStreakIndicator;
