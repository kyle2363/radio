import React, { useState, useEffect } from "react";
import * as api from "../api";

const MOOD_COLORS = {
  chill: "#2dd4a0",
  neutral: "#7a8aaa",
  energetic: "#f09060",
  focus: "#5eadf5",
  melancholy: "#a870f0",
  discovery: "#f0b060",
};

function StatCard({ num, label, accent }) {
  return (
    <div className="stat-card">
      <div className={`stat-num ${accent ? "accent" : ""}`}>{num}</div>
      <div className="stat-desc">{label}</div>
    </div>
  );
}

function BarChart({ data, labelKey, valueKey, color, height = 12, maxVal }) {
  const mx = maxVal || Math.max(...data.map((d) => d[valueKey]), 1);
  return (
    <div className="flex flex-col gap-1">
      {data.map((d, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="text-xs text-gray-500 w-20 truncate flex-shrink-0">{d[labelKey]}</span>
          <div className="flex-1 h-3 bg-gray-800 rounded-sm overflow-hidden">
            <div
              className="h-full rounded-sm transition-all duration-500"
              style={{ width: `${(d[valueKey] / mx) * 100}%`, background: color || "var(--accent)" }}
            />
          </div>
          <span className="text-xs text-gray-600 tabular-nums w-8 text-right flex-shrink-0">{d[valueKey]}</span>
        </div>
      ))}
    </div>
  );
}

function VerticalBarChart({ data, labelKey, valueKey, height = 80 }) {
  const mx = Math.max(...data.map((d) => d[valueKey]), 1);
  return (
    <div>
      <div className="flex items-end gap-0.5" style={{ height }}>
        {data.map((d, i) => {
          const h = Math.max(2, (d[valueKey] / mx) * 100);
          return (
            <div key={i} className="flex-1 flex flex-col items-center gap-0.5">
              <div
                className="w-full rounded-sm transition-all duration-500"
                style={{ height: `${h}%`, background: "var(--accent)" }}
                title={`${d[labelKey]}: ${d[valueKey]}`}
              />
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-[9px] text-gray-600 mt-1">
        {data.filter((_, i) => i === 0 || i === Math.floor(data.length / 4) || i === Math.floor(data.length / 2) || i === Math.floor(3 * data.length / 4) || i === data.length - 1).map((d, _i, arr) => (
          <span key={_i}>{d[labelKey]}</span>
        ))}
      </div>
    </div>
  );
}

function LineChart({ data, labelKey, valueKey, height = 60 }) {
  const mx = Math.max(...data.map((d) => d[valueKey]), 1);
  const pts = data.map((d, i) => ({
    x: (i / Math.max(data.length - 1, 1)) * 100,
    y: ((mx - d[valueKey]) / mx) * height,
    label: typeof d[labelKey] === "string" ? d[labelKey].slice(5) : String(d[labelKey]),
    val: d[valueKey],
  }));
  const pathD = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" ");
  return (
    <div className="relative" style={{ height }}>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
        <path d={pathD} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between relative h-full">
        {pts.map((p, i) => (
          <div key={i} className="flex-1 flex flex-col justify-end items-center pb-1">
            <div
              className="w-2 h-2 rounded-full transition-all"
              style={{ background: "var(--accent)", opacity: 0.8 }}
              title={`${p.label}: ${p.val}`}
            />
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[9px] text-gray-600 mt-0.5">
        {pts.filter((_, i) => i === 0 || i === Math.floor(pts.length / 4) || i === Math.floor(pts.length / 2) || i === Math.floor(3 * pts.length / 4) || i === pts.length - 1).map((p, _i, arr) => (
          <span key={_i}>{p.label}</span>
        ))}
      </div>
    </div>
  );
}

function StatsDashboard() {
  const [daily, setDaily] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchAll();
  }, []);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [d, s] = await Promise.all([api.fetchDailyReport(), api.fetchStatsOverview()]);
      setDaily(d);
      setStats(s);
    } catch (e) {
      setError(e.message || "Failed to load stats");
    }
    setLoading(false);
  };

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
        <div className="text-gray-500 text-sm">Loading stats...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
        <div className="text-red-400 text-sm mb-2">{error}</div>
        <button onClick={fetchAll} className="px-3 py-1 rounded bg-gray-800 text-xs text-gray-400 hover:text-gray-300 transition">Retry</button>
      </div>
    );
  }

  const today = daily && daily.total_songs > 0 ? daily : null;
  const hasToday = !!today;

  return (
    <div className="space-y-5">
      {/* Today section */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800/50">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Today{hasToday ? ` — ${today.date}` : ""}
          </h3>
        </div>
        {!hasToday ? (
          <div className="p-5 text-center">
            <p className="text-sm text-gray-600">{daily?.message || "No listening today yet."}</p>
          </div>
        ) : (
          <div>
            <div className="stat-cards">
              <StatCard num={today.total_songs} label="Songs" />
              <StatCard num={`${today.listen_minutes}m`} label="Listen time" accent />
              <StatCard num={`${Math.round(today.completion_rate * 100)}%`} label="Completion" />
              <StatCard num={today.hearted} label="Hearts" accent />
              <StatCard num={today.skipped} label="Skipped" />
              {today.replayed > 0 && <StatCard num={today.replayed} label="Replays" />}
              {today.session_count > 0 && <StatCard num={today.session_count} label="Sessions" />}
              {today.unique_songs > 0 && <StatCard num={today.unique_songs} label="Unique" />}
            </div>
            {today.energy_avg != null && (
              <div className="px-4 pb-3">
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <span>Avg energy:</span>
                  <span className={`font-semibold ${today.energy_avg >= 0.66 ? "text-orange-400" : today.energy_avg >= 0.33 ? "text-yellow-400" : "text-blue-400"}`}>
                    {today.energy_avg}
                  </span>
                  <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden max-w-[120px]">
                    <div className="h-full rounded-full bg-gradient-to-r from-blue-400 via-yellow-400 to-orange-400" style={{ width: `${today.energy_avg * 100}%` }} />
                  </div>
                </div>
              </div>
            )}
            {today.top_artists?.length > 0 && (
              <div className="px-4 pb-3">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5">Top Artists Today</div>
                <div className="flex flex-wrap gap-1">
                  {today.top_artists.map((a, i) => (
                    <span key={i} className="px-2 py-0.5 rounded bg-gray-800 text-xs text-gray-400">{a.artist} ({a.plays})</span>
                  ))}
                </div>
              </div>
            )}
            {today.top_songs?.length > 0 && (
              <div className="px-4 pb-3">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5">Top Songs Today</div>
                <div className="space-y-0.5">
                  {today.top_songs.map((s, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs text-gray-400">
                      <span className="text-gray-600 tabular-nums">{i + 1}.</span>
                      <span className="flex-1 truncate">{s.artist} — {s.title}</span>
                      <span className="text-gray-600 tabular-nums">{s.plays}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {today.mood_distribution && Object.keys(today.mood_distribution).length > 0 && (
              <div className="px-4 pb-4">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1.5">Mood Today</div>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(today.mood_distribution).map(([mood, count]) => (
                    <span key={mood} className="px-2 py-0.5 rounded text-xs" style={{ background: `${MOOD_COLORS[mood] || "#7a8aaa"}20`, color: MOOD_COLORS[mood] || "#7a8aaa" }}>
                      {mood}: {count}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* All time section */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800/50">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">All Time</h3>
        </div>
        {!stats || stats.total_plays === 0 ? (
          <div className="p-5 text-center">
            <p className="text-sm text-gray-600">No listening data yet.</p>
          </div>
        ) : (
          <div>
            <div className="stat-cards">
              <StatCard num={stats.total_plays} label="Total plays" />
              <StatCard num={`${Math.round(stats.total_listen_secs / 60)}m`} label="Listen time" accent />
              <StatCard num={stats.unique_songs_played} label="Unique songs" />
              <StatCard num={`${Math.round(stats.completion_rate * 100)}%`} label="Completion" />
              <StatCard num={`${Math.round(stats.skip_rate * 100)}%`} label="Skip rate" />
              <StatCard num={stats.hearted} label="Hearts" accent />
              <StatCard num={stats.replayed || 0} label="Replays" />
              {stats.session_count > 0 && <StatCard num={stats.session_count} label="Sessions" />}
              {stats.avg_songs_per_session > 0 && <StatCard num={stats.avg_songs_per_session} label="Songs/session" />}
            </div>

            {/* Weekly trend */}
            {stats.weekly_plays?.length > 0 && (
              <div className="px-4 pb-4">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Weekly Trend</div>
                <LineChart data={stats.weekly_plays} labelKey="date" valueKey="count" height={56} />
              </div>
            )}

            {/* Mood distribution */}
            {stats.by_mood?.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 px-4 pb-4">
                <div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Mood Distribution</div>
                  <BarChart
                    data={stats.by_mood.map((m) => ({ ...m, label: m.mood }))}
                    labelKey="label"
                    valueKey="count"
                    color="var(--accent)"
                  />
                </div>

                {/* Energy distribution */}
                {stats.energy_distribution?.length > 0 && (
                  <div>
                    <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Energy Distribution</div>
                    <BarChart
                      data={stats.energy_distribution.map((e) => ({ ...e, label: e.bucket }))}
                      labelKey="label"
                      valueKey="pct"
                      height={10}
                      color="var(--accent)"
                    />
                  </div>
                )}
              </div>
            )}

            {/* Top artists + songs */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 px-4 pb-4">
              {stats.top_artists?.length > 0 && (
                <div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Top Artists</div>
                  <div className="space-y-0.5">
                    {stats.top_artists.map((a, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="text-gray-600 tabular-nums w-4">{i + 1}.</span>
                        <span className="flex-1 truncate text-gray-400">{a.artist}</span>
                        <span className="text-gray-600 tabular-nums">{a.plays}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {stats.top_songs?.length > 0 && (
                <div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Top Songs</div>
                  <div className="space-y-0.5">
                    {stats.top_songs.map((s, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="text-gray-600 tabular-nums w-4">{i + 1}.</span>
                        <span className="flex-1 truncate text-gray-400">{s.artist} — {s.title}</span>
                        <span className="text-gray-600 tabular-nums">{s.plays}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Listening by hour + genre distribution */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 px-4 pb-4">
              {stats.by_hour?.length > 0 && (
                <div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Listening by Hour</div>
                  <VerticalBarChart data={stats.by_hour} labelKey="hour" valueKey="count" height={72} />
                </div>
              )}
              {stats.top_genres?.length > 0 && (
                <div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Top Genres</div>
                  <BarChart
                    data={stats.top_genres.map((g) => ({ ...g, label: g.genre }))}
                    labelKey="label"
                    valueKey="pct"
                    color="#a78bfa"
                    maxVal={100}
                  />
                </div>
              )}
            </div>

            {/* Cluster mix */}
            {stats.cluster_distribution?.length > 0 && (
              <div className="px-4 pb-4">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Cluster Mix</div>
                <div className="flex flex-wrap gap-1.5">
                  {stats.cluster_distribution.map((c, i) => {
                    const hues = [160, 200, 280, 340, 40, 100, 220, 300];
                    const h = hues[i % hues.length];
                    return (
                      <span key={i} className="px-2 py-0.5 rounded text-xs" style={{ background: `hsla(${h}, 50%, 50%, 0.15)`, color: `hsl(${h}, 60%, 70%)` }}>
                        C{c.cluster_id}: {c.count}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {stats.avg_completion_rate > 0 && (
              <div className="px-4 pb-3">
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <span>Avg song completion:</span>
                  <span className="font-semibold text-gray-400">{stats.avg_completion_rate}%</span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default StatsDashboard;
