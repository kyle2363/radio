import React, { useEffect, useState } from "react";
import * as api from "../api";

// Checkpoint 9D: "Your feels" row next to the mood buttons. A feel station narrows
// desktop Radio to that feel's songs and neighbours; normal Radio rules still apply.
export default function FeelStations({ addToast }) {
  const [feels, setFeels] = useState([]);
  const [active, setActive] = useState(null);
  useEffect(() => {
    Promise.all([api.fetchFeels(), api.fetchFeelStation()])
      .then(([f, s]) => { setFeels((f.feels || []).filter((x) => x.members > 0)); setActive(s.feel_id); })
      .catch(() => {});
  }, []);
  if (!feels.length) return null;
  async function pick(id) {
    const next = id === active ? null : id;
    try { await api.setFeelStation(next); setActive(next); }
    catch (e) { addToast?.("Could not change station: " + e.message, "error"); }
  }
  return (
    <div style={{ display: "flex", gap: "6px", overflowX: "auto", padding: "6px 12px", alignItems: "center" }} aria-label="Your feels">
      <span style={{ fontSize: "11px", color: "var(--text-muted)", flexShrink: 0 }}>Your feels</span>
      {feels.map((f) => {
        const on = f.id === active;
        return (
          <button key={f.id} onClick={() => pick(f.id)} aria-pressed={on} title={on ? "Back to normal Radio" : `Play a ${f.name} station`}
            style={{ flexShrink: 0, padding: "4px 10px", borderRadius: "999px", fontSize: "11px", cursor: "pointer",
              border: `1px solid ${on ? f.color : "var(--border)"}`, background: on ? f.color : "var(--bg-elevated)",
              color: on ? "#111" : "var(--text-secondary)", fontWeight: on ? 600 : 400 }}>
            {f.name}
          </button>
        );
      })}
    </div>
  );
}
