import React, { useState, useEffect } from "react";
import * as api from "../api";

const MOOD_COLORS = { chill: "bg-blue-500", neutral: "bg-gray-500", energetic: "bg-orange-500", focus: "bg-green-500", melancholy: "bg-purple-500", discovery: "bg-cyan-500" };

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
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Mood Journey</h3>
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {changes.map((mc, i) => {
          const time = new Date(mc.timestamp);
          const timeStr = time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          return (
            <div key={i} className="flex items-center gap-1 flex-shrink-0">
              <div className="flex flex-col items-center">
                <div className={`w-3 h-3 rounded-full ${MOOD_COLORS[mc.mood] || "bg-gray-500"}`} title={`${mc.mood} (${mc.source})`} />
                <span className="text-[9px] text-gray-600 mt-0.5">{timeStr}</span>
                <span className="text-[9px] text-gray-500">{mc.mood}</span>
              </div>
              {i < changes.length - 1 && <div className="w-4 h-px bg-gray-700 flex-shrink-0" />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default MoodTimeline;
