import React, { useState, useEffect, useCallback } from "react";
import * as api from "../api";
import BucketAnchor from "./BucketAnchor";

const BUCKETS = [
  { key: "hot", label: "Hot", emoji: "🔥", value: 0.875, color: "#e74c3c" },
  { key: "warm", label: "Warm", emoji: "🌤", value: 0.625, color: "#e67e22" },
  { key: "mellow", label: "Mellow", emoji: "🌫", value: 0.375, color: "#7c5cfc" },
  { key: "cold", label: "Cold", emoji: "❄", value: 0.125, color: "#3498db" },
];

function CalibrationPanel({ addToast }) {
  const [samples, setSamples] = useState([]);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [ratings, setRatings] = useState({});
  const [anchors, setAnchors] = useState({ hot: [], warm: [], mellow: [], cold: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const current = samples[currentIdx];
  const ratedCount = Object.keys(ratings).length;
  const total = samples.length;
  const done = currentIdx >= total;

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [sampleData, anchorData] = await Promise.all([
        api.fetchCalibrationSample(20),
        api.fetchCalibrationAnchors(),
      ]);
      const songs = sampleData.songs || [];
      setSamples(songs);
      setAnchors(anchorData);

      const prefill = {};
      songs.forEach((s) => {
        if (s.rated && s.user_energy !== null && s.user_energy !== undefined) {
          prefill[s.id] = s.user_energy;
        }
      });
      setRatings(prefill);

      // Find first unrated
      const firstUnrated = songs.findIndex((s) => !prefill[s.id]);
      setCurrentIdx(firstUnrated >= 0 ? firstUnrated : songs.length);
    } catch (e) {
      addToast?.("Failed to load calibration data: " + e.message, "error");
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function rateCurrent(bucket) {
    const song = samples[currentIdx];
    if (!song || saving) return;
    setSaving(true);
    try {
      await api.setUserEnergy(song.id, bucket.value);
      setRatings((prev) => ({ ...prev, [song.id]: bucket.value }));
      const next = currentIdx + 1;
      if (next < total) {
        setCurrentIdx(next);
      } else {
        setCurrentIdx(total);
      }
    } catch (e) {
      addToast?.("Failed to save rating: " + e.message, "error");
    } finally {
      setSaving(false);
    }
  }

  function skipCurrent() {
    if (currentIdx < total - 1) {
      setCurrentIdx(currentIdx + 1);
    } else {
      setCurrentIdx(total);
    }
  }

  async function handleDone() {
    try {
      await api.rebuildQueue();
      addToast?.(`Saved ${ratedCount} ratings. Queue adjusted.`, "success");
    } catch (e) {
      addToast?.("Saved ratings but queue rebuild failed: " + e.message, "info");
    }
  }

  function playSong(songId, artist, title) {
    api.playSong(songId);
    addToast?.("Playing: " + (artist || "") + " - " + (title || songId), "info");
  }

  if (loading) {
    return <div className="empty">Loading calibration...</div>;
  }

  if (!current) {
    return (
      <div>
        <div className="empty">No songs available for calibration.</div>
        <button onClick={fetchData} className="btn" style={{ marginTop: "10px" }}>Retry</button>
      </div>
    );
  }

  return (
    <div style={{ padding: "10px" }}>
      <div className="flex" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
        <div>
          <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)" }}>Energy Calibration</div>
          <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
            {done ? "All done!" : `${ratedCount} of ${total} rated`}
          </div>
        </div>
        <div style={{ display: "flex", gap: "6px" }}>
          <button onClick={fetchData}
            style={{ padding: "4px 10px", borderRadius: "4px", fontSize: "10px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer" }}>
            Reshuffle
          </button>
          <button onClick={handleDone} disabled={ratedCount === 0}
            style={{ padding: "4px 14px", borderRadius: "4px", fontSize: "10px", background: "var(--accent)", border: "none", color: "#fff", cursor: "pointer", opacity: ratedCount === 0 ? 0.4 : 1, fontWeight: 500 }}>
            Done
          </button>
        </div>
      </div>

      <div className="flex" style={{ gap: "14px", alignItems: "flex-start" }}>
        <div style={{ flex: "1", minWidth: "150px" }}>
          {BUCKETS.map((b) => (
            <BucketAnchor
              key={b.key}
              bucket={b.key}
              songs={anchors[b.key] || []}
              onPlay={playSong}
            />
          ))}
        </div>

        <div style={{ flex: "2", minWidth: "200px" }}>
          <div style={{ background: "var(--card)", borderRadius: "8px", border: "1px solid var(--border)", padding: "12px 16px" }}>
            <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "6px" }}>
              Cluster {current.cluster_id} · {current.bpm ? `BPM ${current.bpm}` : ""} · E={current.energy?.toFixed(3)}
            </div>
            <div style={{ fontSize: "14px", fontWeight: 500, color: "var(--text)", marginBottom: "2px" }}>
              {current.artist || "?"} — {current.title || "?"}
            </div>

            <div style={{ height: "40px", margin: "10px 0", background: "var(--bg)", borderRadius: "4px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "10px", color: "var(--text-muted)" }}>
              <span>Click a bucket below to rate</span>
            </div>

            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              {BUCKETS.map((b) => {
                const isRated = ratings[current.id] === b.value;
                return (
                  <button
                    key={b.key}
                    onClick={() => rateCurrent(b)}
                    disabled={saving || done}
                    style={{
                      flex: 1, minWidth: "70px", padding: "8px 6px", borderRadius: "6px",
                      fontSize: "11px", fontWeight: isRated ? 600 : 400,
                      background: isRated ? b.color : "var(--bg-elevated)",
                      border: `1px solid ${isRated ? b.color : "var(--border)"}`,
                      color: isRated ? "#fff" : "var(--text-secondary)",
                      cursor: saving || done ? "default" : "pointer",
                      opacity: saving ? 0.5 : 1,
                      transition: "all 0.12s",
                    }}
                  >
                    {b.emoji} {b.label}
                  </button>
                );
              })}
            </div>

            <div style={{ display: "flex", gap: "6px", marginTop: "8px" }}>
              <button onClick={skipCurrent} disabled={saving || done}
                style={{ flex: 1, padding: "5px", borderRadius: "4px", fontSize: "10px", background: "transparent", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer" }}>
                Skip
              </button>
            </div>
          </div>
        </div>
      </div>

      {done && (
        <div style={{ marginTop: "12px", padding: "10px 14px", borderRadius: "8px", background: "rgba(74,222,128,0.08)", border: "1px solid rgba(74,222,128,0.2)", fontSize: "12px", color: "var(--text)", textAlign: "center" }}>
          Rated {ratedCount} of {total} songs. <button onClick={handleDone} style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", textDecoration: "underline", fontSize: "12px" }}>Done</button>
        </div>
      )}
    </div>
  );
}

export default CalibrationPanel;
