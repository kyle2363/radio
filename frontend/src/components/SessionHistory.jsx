import React, { useState, useEffect, useRef } from "react";
import { Search, ChevronRight } from "lucide-react";
import * as api from "../api";

const MOOD_DOT_COLORS = {
  chill: "#60a5fa",
  neutral: "#9ca3af",
  energetic: "#fb923c",
  focus: "#34d399",
  melancholy: "#c084fc",
  discovery: "#22d3ee",
};

function SessionHistory() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [search, setSearch] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => { fetchSessions(); }, []);

  const fetchSessions = async () => {
    try { setSessions(await api.fetchSessions(50)); } catch (e) {}
    setLoading(false);
  };

  const fetchDetail = async (id) => {
    if (expandedId === id) { setExpandedId(null); setDetail(null); return; }
    setDetailLoading(true);
    try { setDetail(await api.fetchSessionDetail(id)); setExpandedId(id); } catch (e) {}
    setDetailLoading(false);
  };

  const formatDuration = (start, end) => {
    if (!start || !end) return "—";
    const s = new Date(start);
    const e = new Date(end);
    const secs = Math.floor((e - s) / 1000);
    if (secs < 60) return `${secs}s`;
    const m = Math.floor(secs / 60);
    const h = Math.floor(m / 60);
    return h > 0 ? `${h}h ${m % 60}m` : `${m}m`;
  };

  const formatDate = (dt) => {
    if (!dt) return "—";
    const d = new Date(dt);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  const signalColor = (signal) => {
    if (signal === "completed") return "var(--accent)";
    if (signal === "hearted") return "#f87171";
    if (signal === "thumbed_down") return "#fb923c";
    if (signal?.includes("skip")) return "#eab308";
    if (signal === "replayed") return "#c084fc";
    return "var(--text-muted)";
  };

  const filtered = sessions.filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      formatDate(s.started_at).toLowerCase().includes(q) ||
      (s.initial_mood || "").toLowerCase().includes(q)
    );
  });

  const totalSongs = sessions.reduce((sum, s) => sum + (s.song_count || 0), 0);
  const totalHours = sessions.reduce((sum, s) => {
    if (!s.started_at || !s.ended_at) return sum;
    return sum + (new Date(s.ended_at) - new Date(s.started_at)) / 3600000;
  }, 0);
  const avgCompletion = sessions.length > 0
    ? Math.round(sessions.reduce((sum, s) => {
        const total = (s.completed_count || 0) + (s.skip_count || 0);
        return sum + (total > 0 ? (s.completed_count || 0) / total : 0);
      }, 0) / sessions.length * 100)
    : 0;

  if (loading) return <div style={{ padding: "24px 14px", textAlign: "center", fontSize: "13px", color: "var(--text-muted)" }}>Loading sessions...</div>;
  if (sessions.length === 0) return <div style={{ padding: "24px 14px", textAlign: "center", fontSize: "13px", color: "var(--text-muted)" }}>No sessions yet</div>;

  return (
    <div>
      {/* Stat cards */}
      <div className="session-stat-cards">
        <div className="session-stat-card">
          <div className="session-stat-num">{sessions.length}</div>
          <div className="session-stat-desc">Sessions</div>
        </div>
        <div className="session-stat-card">
          <div className="session-stat-num accent">{totalSongs}</div>
          <div className="session-stat-desc">Songs played</div>
        </div>
        <div className="session-stat-card">
          <div className="session-stat-num">{totalHours < 1 ? `${Math.round(totalHours * 60)}m` : `${Math.round(totalHours)}h`}</div>
          <div className="session-stat-desc">Total time</div>
        </div>
        <div className="session-stat-card">
          <div className="session-stat-num">{avgCompletion}%</div>
          <div className="session-stat-desc">Avg completion</div>
        </div>
      </div>

      {/* Search bar */}
      <div className="search-bar">
        <Search size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
        <input className="search-input" placeholder="Filter sessions by date or mood…" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {/* Column headers */}
      <div className="col-hdr-session">
        <div></div>
        <div className="col-hdr-cell">Date</div>
        <div className="col-hdr-cell" style={{ justifyContent: "center" }}>Songs</div>
        <div className="col-hdr-cell" style={{ justifyContent: "center" }}>Duration</div>
        <div className="col-hdr-cell" style={{ justifyContent: "center" }}>Mood</div>
        <div className="col-hdr-cell" style={{ justifyContent: "center" }}>Done</div>
        <div className="col-hdr-cell" style={{ justifyContent: "center" }}>Skip</div>
        <div></div>
      </div>

      {/* Session rows */}
      {filtered.length === 0 ? (
        <div style={{ padding: "24px 14px", textAlign: "center", fontSize: "13px", color: "var(--text-muted)" }}>
          No sessions match "{search}"
          <div style={{ marginTop: "8px" }}>
            <button onClick={() => setSearch("")} style={{ fontSize: "11px", color: "var(--accent)", cursor: "pointer" }}>Clear filter</button>
          </div>
        </div>
      ) : (
        <div>
          {filtered.map((s) => {
            const isOpen = expandedId === s.id;
            return (
              <div key={s.id}>
                <div className="session-row" onClick={() => fetchDetail(s.id)}>
                  <div className={`session-chevron ${isOpen ? "open" : ""}`}>
                    <ChevronRight size={12} style={{ color: "var(--text-muted)" }} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {formatDate(s.started_at)}
                    </div>
                  </div>
                  <div style={{ fontSize: "12px", textAlign: "center", color: "var(--text-secondary)" }}>{s.song_count || 0}</div>
                  <div style={{ fontSize: "12px", textAlign: "center", color: "var(--text-secondary)" }}>{formatDuration(s.started_at, s.ended_at)}</div>
                  <div style={{ fontSize: "12px", textAlign: "center", color: "var(--text-secondary)" }}>
                    {s.initial_mood ? (
                      <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                        <span style={{ width: "7px", height: "7px", borderRadius: "50%", background: MOOD_DOT_COLORS[s.initial_mood] || "#9ca3af", flexShrink: 0 }} />
                        {s.initial_mood}
                      </span>
                    ) : "—"}
                  </div>
                  <div style={{ fontSize: "12px", textAlign: "center" }}>
                    <span className="pct-badge pct-hi">{s.completed_count || 0}</span>
                  </div>
                  <div style={{ fontSize: "12px", textAlign: "center" }}>
                    <span className="pct-badge pct-lo">{s.skip_count || 0}</span>
                  </div>
                  <div></div>
                </div>

                {/* Expanded detail */}
                {isOpen && (
                  <div className="session-detail">
                    {detailLoading ? (
                      <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>Loading...</div>
                    ) : detail?.id === s.id ? (
                      <>
                        <div className="session-detail-grid">
                          <div className="session-detail-stat">Completed: <strong>{detail.events?.filter((e) => e.signal === "completed").length || 0}</strong></div>
                          <div className="session-detail-stat">Skipped: <strong>{detail.events?.filter((e) => e.signal?.includes("skip")).length || 0}</strong></div>
                          <div className="session-detail-stat">Hearted: <strong>{detail.events?.filter((e) => e.signal === "hearted").length || 0}</strong></div>
                          <div className="session-detail-stat">Type: <strong>{detail.session_type || "normal"}</strong></div>
                        </div>

                        {detail.mood_changes?.length > 0 && (
                          <div className="mood-block">
                            {detail.mood_changes.map((mc, i) => (
                              <span key={i} className="mood-tag">
                                <span className="mood-tag-dot" style={{ background: MOOD_DOT_COLORS[mc.mood] || "#9ca3af" }} />
                                {mc.mood} <span style={{ color: "var(--text-muted)", fontSize: "9px" }}>({mc.source})</span>
                              </span>
                            ))}
                          </div>
                        )}

                        {detail.events?.length > 0 && (
                          <div className="event-list">
                            {detail.events.map((e, i) => (
                              <div key={i} className="event-row">
                                <span className="event-dot" style={{ background: signalColor(e.signal) }} />
                                <span className="event-name">{e.artist} — {e.title}</span>
                                <span className="event-signal">{e.signal}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    ) : null}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default SessionHistory;