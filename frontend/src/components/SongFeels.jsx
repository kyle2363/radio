import React, { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { historySummary } from "./feelsHelpers.js";

// Checkpoint 9E: feel history and suggestions on the song page.
export default function SongFeels({ songId, addToast }) {
  const [data, setData] = useState(null);
  const load = useCallback(() => {
    api.fetchSongFeels(songId).then(setData).catch(() => setData({ history: [], suggestions: [] }));
  }, [songId]);
  useEffect(load, [load]);
  if (!data) return null;
  const summary = historySummary(data.history);
  async function answer(feelId, value) {
    try { await api.rateFeel(feelId, songId, value); load(); } catch (e) { addToast?.(e.message, "error"); }
  }
  const chip = { padding: "3px 8px", borderRadius: "999px", fontSize: "11px", border: "1px solid var(--border)", background: "var(--bg-elevated)", color: "var(--text-secondary)", cursor: "pointer" };
  return (
    <div>
      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Feels</div>
      <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{summary || "Not in any feel yet."}</div>
      {data.suggestions.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px", alignItems: "center" }}>
          <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>Sounds like</span>
          {data.suggestions.map((s) => (
            <span key={s.feel.id} style={{ display: "inline-flex", gap: "4px", alignItems: "center" }}>
              <button style={{ ...chip, borderColor: s.feel.color }} onClick={() => answer(s.feel.id, 1)} title="Add to this feel">+ {s.feel.name}</button>
              <button style={chip} onClick={() => answer(s.feel.id, 0)} aria-label={`Not ${s.feel.name}`}>✕</button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
