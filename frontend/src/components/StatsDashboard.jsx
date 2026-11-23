import React, { useState, useEffect } from "react";
import { RefreshCw } from "lucide-react";
import * as api from "../api";
import { moodColor } from "../ui/consistency.js";

function StatCard({ num, label, accent }) {
  return (
    <div className="stat-card">
      <div className={`stat-num ${accent ? "accent" : ""}`}>{num}</div>
      <div className="stat-desc">{label}</div>
    </div>
  );
}

function Card({ title, children, wide }) {
  return (
    <section className="card" style={wide ? { gridColumn: "1 / -1" } : undefined}>
      <div className="card-title">{title}</div>
      {children}
    </section>
  );
}

function BarList({ data, labelKey, valueKey, colorOf, maxVal, suffix = "" }) {
  const mx = maxVal || Math.max(...data.map((d) => d[valueKey]), 1);
  return (
    <div className="flex flex-col gap-1.5">
      {data.map((d, i) => (
        <div key={i} className="hbar">
          <span className="hbar-label" style={{ textTransform: "capitalize" }}>{d[labelKey]}</span>
          <div className="hbar-track">
            <div className="hbar-fill" style={{ width: `${(d[valueKey] / mx) * 100}%`, background: colorOf?.(d) || "var(--accent)" }} />
          </div>
          <span className="hbar-num">{d[valueKey]}{suffix}</span>
        </div>
      ))}
    </div>
  );
}

function RankList({ items, label, count }) {
  return (
    <ol className="flex flex-col gap-1">
      {items.map((item, i) => (
        <li key={i} className="flex items-center gap-2 text-xs">
          <span className="tabular-nums w-5 text-right" style={{ color: "var(--text-muted)" }}>{i + 1}</span>
          <span className="flex-1 truncate" style={{ color: "var(--text-primary)" }}>{label(item)}</span>
          <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>{count(item)}</span>
        </li>
      ))}
    </ol>
  );
}

function axisLabels(items) {
  const n = items.length;
  return items.filter((_, i) => i === 0 || i === Math.floor(n / 4) || i === Math.floor(n / 2) || i === Math.floor(3 * n / 4) || i === n - 1);
}

function HourChart({ data, height = 80 }) {
  const mx = Math.max(...data.map((d) => d.count), 1);
  return (
    <div>
      <div className="flex items-end gap-0.5" style={{ height }}>
        {data.map((d, i) => (
          <div key={i} className="flex-1 rounded-sm transition-all duration-500"
            style={{ height: `${Math.max(2, (d.count / mx) * 100)}%`, background: "var(--accent)", opacity: d.count ? 0.85 : 0.25 }}
            title={`${d.hour}:00 — ${d.count} plays`} />
        ))}
      </div>
      <div className="flex justify-between text-[10px]" style={{ color: "var(--text-muted)", marginTop: 4 }}>
        {axisLabels(data).map((d, i) => <span key={i}>{d.hour}:00</span>)}
      </div>
    </div>
  );
}

function TrendChart({ data, height = 64 }) {
  const mx = Math.max(...data.map((d) => d.count), 1);
  const pts = data.map((d, i) => ({
    x: (i / Math.max(data.length - 1, 1)) * 100,
    y: 4 + ((mx - d.count) / mx) * (height - 8),
    label: typeof d.date === "string" ? d.date.slice(5) : String(d.date),
    val: d.count,
  }));
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" ");
  const area = `${line} L100 ${height} L0 ${height} Z`;
  return (
    <div>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
        <path d={area} fill="var(--accent-muted)" stroke="none" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {pts.map((p, i) => <title key={i}>{`${p.label}: ${p.val}`}</title>)}
      </svg>
      <div className="flex justify-between text-[10px]" style={{ color: "var(--text-muted)", marginTop: 4 }}>
        {axisLabels(pts).map((p, i) => <span key={i}>{p.label}</span>)}
      </div>
    </div>
  );
}

function Chips({ items }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map(({ key, label, color }) => (
        <span key={key} className="badge b-dim" style={{ fontWeight: 500, textTransform: "capitalize" }}>
          {color && <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }} />}
          {label}
        </span>
      ))}
    </div>
  );
}

function StatsDashboard() {
  const [daily, setDaily] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

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

  useEffect(() => { fetchAll(); }, []);

  const today = daily && daily.total_songs > 0 ? daily : null;
  const hasAllTime = stats && stats.total_plays > 0;

  return (
    <div className="page">
      <div className="page-hero">
        <div>
          <div className="page-eyebrow">Your listening</div>
          <h3>Stats</h3>
          <p>What you played today and over time. Mood, energy and cluster data come from the desktop Radio.</p>
        </div>
        <button type="button" className="ui-btn sm" onClick={fetchAll} disabled={loading}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {error && (
        <div className="card" style={{ marginBottom: 10 }}>
          <p className="ui-error" style={{ marginBottom: 8 }}>{error}</p>
          <button type="button" className="ui-btn sm" onClick={fetchAll}>Retry</button>
        </div>
      )}

      {loading && !daily && !stats ? (
        <div className="card"><div className="card-empty">Loading stats…</div></div>
      ) : (
        <div className="flex flex-col gap-6">
          {/* Today */}
          <div>
            <div className="card-title" style={{ marginBottom: 8 }}>Today{today ? ` · ${today.date}` : ""}</div>
            {!today ? (
              <div className="card"><div className="card-empty">{daily?.message || "No listening today yet."}</div></div>
            ) : (
              <>
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
                <div className="page-grid">
                  {today.top_songs?.length > 0 && (
                    <Card title="Top songs today">
                      <RankList items={today.top_songs} label={(s) => `${s.artist || ""} — ${s.title || ""}`} count={(s) => s.plays} />
                    </Card>
                  )}
                  {today.top_artists?.length > 0 && (
                    <Card title="Top artists today">
                      <RankList items={today.top_artists} label={(a) => a.artist} count={(a) => a.plays} />
                    </Card>
                  )}
                  {(today.mood_distribution && Object.keys(today.mood_distribution).length > 0) || today.energy_avg != null ? (
                    <Card title="Mood and energy today">
                      {today.mood_distribution && (
                        <Chips items={Object.entries(today.mood_distribution).map(([mood, count]) => ({ key: mood, label: `${mood} · ${count}`, color: moodColor(mood) }))} />
                      )}
                      {today.energy_avg != null && (
                        <div className="hbar" style={{ marginTop: 12 }}>
                          <span className="hbar-label">Avg energy</span>
                          <div className="hbar-track"><div className="hbar-fill" style={{ width: `${today.energy_avg * 100}%`, background: "linear-gradient(to right, var(--info), var(--warning), var(--danger))" }} /></div>
                          <span className="hbar-num">{today.energy_avg}</span>
                        </div>
                      )}
                    </Card>
                  ) : null}
                </div>
              </>
            )}
          </div>

          {/* All time */}
          <div>
            <div className="card-title" style={{ marginBottom: 8 }}>All time</div>
            {!hasAllTime ? (
              <div className="card"><div className="card-empty">No listening data yet.</div></div>
            ) : (
              <>
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
                <div className="page-grid">
                  {stats.weekly_plays?.length > 0 && (
                    <Card title="Plays per day" wide><TrendChart data={stats.weekly_plays} /></Card>
                  )}
                  {stats.by_mood?.length > 0 && (
                    <Card title="Moods">
                      <BarList data={stats.by_mood} labelKey="mood" valueKey="count" colorOf={(m) => moodColor(m.mood)} />
                    </Card>
                  )}
                  {stats.energy_distribution?.length > 0 && (
                    <Card title="Energy">
                      <BarList data={stats.energy_distribution} labelKey="bucket" valueKey="pct" suffix="%" />
                    </Card>
                  )}
                  {stats.top_artists?.length > 0 && (
                    <Card title="Top artists">
                      <RankList items={stats.top_artists} label={(a) => a.artist} count={(a) => a.plays} />
                    </Card>
                  )}
                  {stats.top_songs?.length > 0 && (
                    <Card title="Top songs">
                      <RankList items={stats.top_songs} label={(s) => `${s.artist || ""} — ${s.title || ""}`} count={(s) => s.plays} />
                    </Card>
                  )}
                  {stats.by_hour?.length > 0 && (
                    <Card title="Listening by hour"><HourChart data={stats.by_hour} /></Card>
                  )}
                  {stats.top_genres?.length > 0 && (
                    <Card title="Top genres">
                      <BarList data={stats.top_genres} labelKey="genre" valueKey="pct" maxVal={100} suffix="%" />
                    </Card>
                  )}
                  {(stats.cluster_distribution?.length > 0 || stats.avg_completion_rate > 0) && (
                    <Card title="Sound clusters" wide>
                      {stats.cluster_distribution?.length > 0 && (
                        <Chips items={stats.cluster_distribution.map((c) => ({ key: c.cluster_id, label: `Cluster ${c.cluster_id} · ${c.count}` }))} />
                      )}
                      {stats.avg_completion_rate > 0 && (
                        <p className="ui-hint" style={{ marginTop: 10 }}>Average song completion: {stats.avg_completion_rate}%</p>
                      )}
                    </Card>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default StatsDashboard;
