import React, { useState, useEffect } from "react";
import * as api from "../api";

function SessionHistory() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [detail, setDetail] = useState(null);

  useEffect(() => { fetchSessions(); }, []);

  const fetchSessions = async () => {
    try { setSessions(await api.fetchSessions()); } catch (e) {}
    setLoading(false);
  };

  const fetchDetail = async (id) => {
    if (expandedId === id) { setExpandedId(null); setDetail(null); return; }
    try { setDetail(await api.fetchSessionDetail(id)); setExpandedId(id); } catch (e) {}
  };

  const formatDuration = (secs) => {
    if (!secs) return "?";
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  };

  const formatDate = (dt) => {
    if (!dt) return "?";
    const d = new Date(dt);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  if (loading) return <div className="text-gray-500 text-sm">Loading sessions...</div>;
  if (sessions.length === 0) return <div className="text-gray-500 text-sm">No sessions yet</div>;

  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-4">Session History</h3>
      <div className="space-y-2">
        {sessions.map((s) => (
          <div key={s.id} className="border border-gray-800 rounded-lg overflow-hidden">
            <div className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-gray-800/50 transition" onClick={() => fetchDetail(s.id)}>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-gray-200">{formatDate(s.started_at)}</div>
                <div className="text-xs text-gray-500">{s.song_count || 0} songs | {formatDuration(s.duration_secs)} | {s.initial_mood || "?"}</div>
              </div>
              <span className="text-xs text-gray-600">{expandedId === s.id ? "\u25BC" : "\u25B6"}</span>
            </div>
            {expandedId === s.id && detail?.id === s.id && (
              <div className="px-3 pb-3 space-y-2 border-t border-gray-800 pt-2">
                <div className="flex gap-4 text-xs text-gray-500">
                  <span>Completed: {detail.completed_count || 0}</span>
                  <span>Skipped: {detail.skip_count || 0}</span>
                </div>
                {detail.mood_changes?.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {detail.mood_changes.map((mc, i) => <span key={i} className="px-2 py-0.5 rounded bg-gray-800 text-xs text-gray-400">{mc.mood} ({mc.source})</span>)}
                  </div>
                )}
                {detail.events?.length > 0 && (
                  <div className="max-h-32 overflow-y-auto space-y-0.5">
                    {detail.events.map((e, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs text-gray-500">
                        <span className={`w-1.5 h-1.5 rounded-full ${e.signal === "completed" ? "bg-green-400" : e.signal === "hearted" ? "bg-red-400" : e.signal.includes("skip") ? "bg-yellow-400" : "bg-gray-500"}`} />
                        <span className="truncate flex-1">{e.artist} - {e.title}</span>
                        <span className="text-gray-600">{e.signal}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default SessionHistory;
