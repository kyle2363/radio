import React from "react";
import HeaderStatusPill from "./HeaderStatusPill";

function SkipStreakIndicator({ skipStreak }) {
  if (!skipStreak || skipStreak < 2) return null;
  return (
    <HeaderStatusPill dotColor="#f0b060">
      <span>{skipStreak} skips</span>
    </HeaderStatusPill>
  );
}

export default SkipStreakIndicator;
