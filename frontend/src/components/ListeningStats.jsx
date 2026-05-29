import React, { useState, useEffect } from "react";
import * as api from "../api";

function ListeningStats() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchStats(); }, []);

  const fetchStats = async () => {
    try { setStats(await api.fetchStatsOverview()); } catch (e) {}
    setLoading(false);
  };

  if (loading) return <div className="text-gray-500 text-sm">Loading stats...</div>;
  if (!stats) return <div className="text-gray-500 text-sm">No stats available</div>;

  const maxHourCount = Math.max(...(stats.by_hour || []).map((h) => h.count), 1);

  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800 space-y-4">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Listening Stats</h3>
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <div className="text-lg font-bold text-white">{stats.total_plays}</div>
          <div className="text-xs text-gray-500">Total plays</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <div className="text-lg font-bold text-green-400">{Math.round(stats.completion_rate * 100)}%</div>
          <div className="text-xs text-gray-500">Completion</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <div className="text-lg font-bold text-white">{stats.unique_songs_played}</div>
          <div className="text-xs text-gray-500">Unique songs</div>
        </div>
      </div>
      {stats.top_artists?.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2">Top Artists</div>
          <div className="space-y-1">
            {stats.top_artists.map((a, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-xs text-gray-400 flex-1 truncate">{a.artist}</span>
                <span className="text-xs text-gray-600 tabular-nums">{a.plays}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {stats.top_songs?.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2">Top Songs</div>
          <div className="space-y-1">
            {stats.top_songs.map((s, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-xs text-gray-400 flex-1 truncate">{s.artist} - {s.title}</span>
                <span className="text-xs text-gray-600 tabular-nums">{s.plays}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {stats.by_hour?.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2">Listening by Hour</div>
          <div className="flex items-end gap-0.5 h-16">
            {Array.from({ length: 24 }, (_, h) => {
              const entry = stats.by_hour.find((e) => e.hour === h);
              const count = entry ? entry.count : 0;
              const height = Math.max(2, (count / maxHourCount) * 100);
              return <div key={h} className="flex-1 flex flex-col items-center gap-0.5"><div className="w-full bg-green-600 rounded-sm" style={{ height: `${height}%` }} title={`${h}:00 - ${count} plays`} /></div>;
            })}
          </div>
          <div className="flex justify-between text-[9px] text-gray-600 mt-1"><span>0</span><span>6</span><span>12</span><span>18</span><span>23</span></div>
        </div>
      )}
      {stats.by_mood?.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2">Mood Distribution</div>
          <div className="flex flex-wrap gap-1.5">
            {stats.by_mood.map((m, i) => <span key={i} className="px-2 py-0.5 rounded bg-gray-800 text-xs text-gray-400">{m.mood}: {m.count}</span>)}
          </div>
        </div>
      )}
    </div>
  );
}

export default ListeningStats;
