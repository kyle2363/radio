import React, { useState, useEffect } from "react";
import * as api from "../api";

const MOOD_COLORS = {
  chill: "#60a5fa",
  neutral: "#9ca3af",
  energetic: "#fb923c",
  focus: "#34d399",
  melancholy: "#c084fc",
  discovery: "#22d3ee",
};

function MoodTimeline() {
  const [changes, setChanges] = useState([]);

  useEffect(() => {
    fetchHistory();
    const interval = setInterval(fetchHistory, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchHistory = async () => {
    try {
      const data = await api.fetchMoodHistory();
      setChanges(data.mood_changes || []);
    } catch (e) {}
  };

  if (changes.length === 0) return null;

  return (
    <div className="mood-timeline-card">
      <h3>Mood Journey</h3>
      <div className="mood-scroll">
        {changes.map((mc, i) => {
          const time = new Date(mc.timestamp);
          const timeStr = time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          return (
            <div key={i} className="mood-node">
              <div className="mood-dot" style={{ background: MOOD_COLORS[mc.mood] || "#9ca3af" }} title={`${mc.mood} (${mc.source})`} />
              <span className="mood-time">{timeStr}</span>
              <span className="mood-label">{mc.mood}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default MoodTimeline;