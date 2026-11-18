import React, { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import CalibrationPanel from "./CalibrationPanel";
import { ENERGY_FEEL_ID, SUGGESTION_MODES, pairOutcome, settledLabel, songLabel, validFeelName } from "./feelsHelpers.js";

// Checkpoint 9D–9E: the old Energy calibration grew into a Feels panel.
// Every action here becomes a shared feel event, so the phone receives it on its next sync.

const btn = { padding: "6px 12px", borderRadius: "6px", fontSize: "11px", background: "var(--bg-elevated)",
  border: "1px solid var(--border)", color: "var(--text-secondary)", cursor: "pointer" };
const primary = { ...btn, background: "var(--accent)", border: "none", color: "#fff", fontWeight: 600 };
const card = { background: "var(--card, var(--bg-surface))", border: "1px solid var(--border)", borderRadius: "8px", padding: "12px 14px" };

function Swatch({ color, size = 10 }) {
  return <span aria-hidden style={{ display: "inline-block", width: size, height: size, borderRadius: "50%", background: color, flexShrink: 0 }} />;
}

function FeelList({ data, onOpen, onReload, addToast }) {
  const [name, setName] = useState("");
  const [color, setColor] = useState("#93C5FD");
  const [mode, setMode] = useState("song_page");
  const [afterSession, setAfterSession] = useState([]);

  useEffect(() => {
    api.fetchFeelSettings().then((s) => setMode(s.suggestions)).catch(() => {});
  }, []);
  useEffect(() => {
    if (mode !== "after_session") { setAfterSession([]); return; }
    api.fetchSessionFeelSuggestions().then((r) => setAfterSession(r.songs || [])).catch(() => {});
  }, [mode, data]);

  async function act(fn, success) {
    try { await fn(); if (success) addToast?.(success, "success"); onReload(); }
    catch (e) { addToast?.(e.message || "Something went wrong", "error"); }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))", gap: "8px" }}>
        {data.feels.map((f) => (
          <button key={f.id} onClick={() => onOpen(f)} style={{ ...card, textAlign: "left", cursor: "pointer", display: "flex", flexDirection: "column", gap: "4px" }}>
            <span style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", fontWeight: 600, color: "var(--text)" }}>
              <Swatch color={f.color} /> {f.name}
            </span>
            {f.meaning && <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>{f.meaning}</span>}
            <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>{f.members} songs · {Math.round(f.settled)}% settled</span>
          </button>
        ))}
      </div>

      {data.starters.length > 0 && (
        <div>
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "6px" }}>Starter feels — add the ones that fit you</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {data.starters.map((s) => (
              <button key={s.key} title={s.meaning} style={{ ...btn, display: "flex", alignItems: "center", gap: "6px" }}
                onClick={() => act(() => api.adoptStarterFeel(s.key), `Added ${s.name}`)}>
                <Swatch color={s.color} /> {s.name}
              </button>
            ))}
          </div>
        </div>
      )}

      <form style={{ display: "flex", gap: "6px", alignItems: "center" }}
        onSubmit={(e) => { e.preventDefault(); if (validFeelName(name)) act(() => api.createFeel(name.trim(), color), `Created ${name.trim()}`).then(() => setName("")); }}>
        <input aria-label="New feel name" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} placeholder="New feel…"
          style={{ flex: 1, padding: "6px 10px", borderRadius: "6px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px" }} />
        <input aria-label="Feel colour" type="color" value={color} onChange={(e) => setColor(e.target.value)} style={{ width: 32, height: 28, border: "none", background: "none" }} />
        <button type="submit" style={primary} disabled={!validFeelName(name)}>Create</button>
      </form>

      <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "11px", color: "var(--text-muted)" }}>
        Feel suggestions
        <select value={mode} onChange={(e) => { const v = e.target.value; setMode(v); api.setFeelSuggestions(v).catch((err) => addToast?.(err.message, "error")); }}
          style={{ padding: "4px 6px", borderRadius: "4px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "11px" }}>
          {SUGGESTION_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </label>

      {afterSession.length > 0 && (
        <div style={card}>
          <div style={{ fontSize: "12px", fontWeight: 600, marginBottom: "6px", color: "var(--text)" }}>From your last session</div>
          {afterSession.map((s) => (
            <div key={s.song_id} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "11px", padding: "3px 0" }}>
              <span style={{ flex: 1, color: "var(--text-secondary)" }}>{songLabel(s)}</span>
              <Swatch color={s.feel.color} /> <span style={{ color: "var(--text-muted)" }}>{s.feel.name}?</span>
              <button style={btn} onClick={() => act(() => api.rateFeel(s.feel.id, s.song_id, 1), null)}>Yes</button>
              <button style={btn} onClick={() => act(() => api.rateFeel(s.feel.id, s.song_id, 0), null)}>No</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Sprint({ feel, addToast }) {
  const [deck, setDeck] = useState(null);
  const [index, setIndex] = useState(0);
  const load = useCallback(() => {
    setDeck(null); setIndex(0);
    api.fetchFeelSprint(feel.id, 20).then((r) => setDeck(r.songs || [])).catch((e) => addToast?.(e.message, "error"));
  }, [feel.id, addToast]);
  useEffect(load, [load]);
  if (!deck) return <div className="empty">Dealing cards…</div>;
  const song = deck[index];
  if (!song) return <div style={card}>Deck done. <button style={btn} onClick={load}>Deal another 20</button></div>;
  async function answer(value) {
    try { if (value !== null) await api.rateFeel(feel.id, song.song_id, value); setIndex(index + 1); }
    catch (e) { addToast?.(e.message, "error"); }
  }
  return (
    <div style={card}>
      <div style={{ fontSize: "10px", color: "var(--text-muted)" }}>Card {index + 1} of {deck.length}</div>
      <div style={{ fontSize: "14px", fontWeight: 600, margin: "6px 0 12px", color: "var(--text)" }}>{songLabel(song)}</div>
      <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "10px" }}>Is this <b>{feel.name}</b>?</div>
      <div style={{ display: "flex", gap: "6px" }}>
        <button style={primary} onClick={() => answer(1)}>Yes</button>
        <button style={btn} onClick={() => answer(0)}>No</button>
        <button style={btn} onClick={() => answer(null)}>Skip</button>
      </div>
    </div>
  );
}

function ThisOrThat({ feel, addToast }) {
  const [pairs, setPairs] = useState(null);
  const [index, setIndex] = useState(0);
  const load = useCallback(() => {
    setPairs(null); setIndex(0);
    api.fetchFeelPairs(feel.id, 10).then((r) => setPairs(r.pairs || [])).catch((e) => addToast?.(e.message, "error"));
  }, [feel.id, addToast]);
  useEffect(load, [load]);
  if (!pairs) return <div className="empty">Finding close calls…</div>;
  if (!pairs.length) return <div style={card}>Rate a few songs first — this-or-that needs at least two songs in the feel.</div>;
  const pair = pairs[index];
  if (!pair) return <div style={card}>Round done. <button style={btn} onClick={load}>Another 10 pairs</button></div>;
  async function choose(choice) {
    try { await api.compareFeel(feel.id, pair.a.song_id, pair.b.song_id, pairOutcome(choice)); setIndex(index + 1); }
    catch (e) { addToast?.(e.message, "error"); }
  }
  const side = (song, pick, unfit) => (
    <div style={{ ...card, flex: 1, display: "flex", flexDirection: "column", gap: "8px" }}>
      <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)" }}>{songLabel(song)}</div>
      <button style={primary} onClick={() => choose(pick)}>This one</button>
      <button style={{ ...btn, fontSize: "10px" }} onClick={() => choose(unfit)}>Doesn't fit</button>
    </div>
  );
  return (
    <div>
      <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "8px" }}>Pair {index + 1} of {pairs.length}: which is more <b>{feel.name}</b>?</div>
      <div style={{ display: "flex", gap: "8px" }}>{side(pair.a, "left", "unfitLeft")}{side(pair.b, "right", "unfitRight")}</div>
      <button style={{ ...btn, marginTop: "8px" }} onClick={() => choose("tie")}>Can't decide</button>
    </div>
  );
}

function Ranking({ feel, addToast }) {
  const [data, setData] = useState(null);
  useEffect(() => { api.fetchFeel(feel.id).then(setData).catch((e) => addToast?.(e.message, "error")); }, [feel.id, addToast]);
  if (!data) return <div className="empty">Loading ranking…</div>;
  const members = data.songs.filter((s) => s.member);
  return (
    <div>
      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "8px" }}>{settledLabel(data.settled)}</div>
      {members.length === 0 && <div className="empty">No songs yet. Try a sprint.</div>}
      <ol style={{ margin: 0, paddingLeft: "22px", fontSize: "12px", color: "var(--text-secondary)" }}>
        {members.map((s) => <li key={s.song_id} style={{ padding: "2px 0" }}>{songLabel(s)} <span style={{ color: "var(--text-muted)", fontSize: "10px" }}>{Math.round(s.confidence * 100)}% sure</span></li>)}
      </ol>
    </div>
  );
}

function Review({ feel, addToast, onRerank }) {
  const [songs, setSongs] = useState(null);
  const load = useCallback(() => { api.fetchFeelReview(feel.id).then((r) => setSongs(r.songs || [])).catch((e) => addToast?.(e.message, "error")); }, [feel.id, addToast]);
  useEffect(load, [load]);
  if (!songs) return <div className="empty">Loading…</div>;
  if (!songs.length) return <div className="empty">Nothing to review — every song was confirmed in the last six months.</div>;
  async function decide(song, value) {
    try { await api.rateFeel(feel.id, song.song_id, value); load(); } catch (e) { addToast?.(e.message, "error"); }
  }
  return (
    <div>
      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "8px" }}>Songs not confirmed for six months or more. Nothing is deleted from history.</div>
      {songs.map((s) => (
        <div key={s.song_id} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", padding: "3px 0" }}>
          <span style={{ flex: 1, color: "var(--text-secondary)" }}>{songLabel(s)}</span>
          <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>{new Date(s.confirmed_at * 1000).toLocaleDateString()}</span>
          <button style={btn} onClick={() => decide(s, 1)}>Keep</button>
          <button style={btn} onClick={() => decide(s, 0)}>Remove</button>
          <button style={btn} onClick={onRerank}>Re-rank</button>
        </div>
      ))}
    </div>
  );
}

function FeelDetail({ feel, onBack, onChanged, addToast }) {
  const isEnergy = feel.id === ENERGY_FEEL_ID;
  const [view, setView] = useState(isEnergy ? "buckets" : "sprint");
  const [name, setName] = useState(feel.name);
  const views = [...(isEnergy ? [["buckets", "Buckets"]] : [["sprint", "Sprint"]]), ["pairs", "This or that"], ["ranking", "Ranking"], ["review", "Review"]];
  async function save(changes, message) {
    try { await api.editFeel(feel.id, changes); addToast?.(message, "success"); onChanged(); }
    catch (e) { addToast?.(e.message, "error"); }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <button style={btn} onClick={onBack}>← Feels</button>
        <Swatch color={feel.color} size={12} />
        {isEnergy ? <b style={{ color: "var(--text)" }}>{feel.name}</b> : (
          <input aria-label="Feel name" value={name} maxLength={40} onChange={(e) => setName(e.target.value)}
            onBlur={() => validFeelName(name) && name.trim() !== feel.name && save({ name: name.trim() }, "Renamed")}
            style={{ fontWeight: 600, fontSize: "13px", background: "transparent", border: "1px solid transparent", color: "var(--text)", padding: "2px 4px" }} />
        )}
        {!isEnergy && <input aria-label="Feel colour" type="color" defaultValue={feel.color} onBlur={(e) => e.target.value.toUpperCase() !== feel.color && save({ color: e.target.value }, "Colour changed")} style={{ width: 28, height: 24, border: "none", background: "none" }} />}
        <span style={{ flex: 1 }} />
        {!isEnergy && <button style={btn} onClick={() => window.confirm(`Archive ${feel.name}? Its ratings are kept.`) && save({ archived: true }, "Archived").then(onBack)}>Archive</button>}
      </div>
      <div style={{ display: "flex", gap: "4px" }}>
        {views.map(([key, label]) => (
          <div key={key} className={`subtab ${view === key ? "on" : ""}`} onClick={() => setView(key)}>{label}</div>
        ))}
      </div>
      {view === "buckets" && <CalibrationPanel addToast={addToast} onRate={(songId, value) => api.rateFeel(ENERGY_FEEL_ID, songId, value)} />}
      {view === "sprint" && <Sprint feel={feel} addToast={addToast} />}
      {view === "pairs" && <ThisOrThat feel={feel} addToast={addToast} />}
      {view === "ranking" && <Ranking feel={feel} addToast={addToast} />}
      {view === "review" && <Review feel={feel} addToast={addToast} onRerank={() => setView("pairs")} />}
    </div>
  );
}

export default function FeelsPanel({ addToast }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);
  const reload = useCallback(() => {
    api.fetchFeels().then((d) => {
      setData(d);
      setOpen((current) => current && (d.feels.find((f) => f.id === current.id) || null));
    }).catch((e) => addToast?.("Could not load feels: " + e.message, "error"));
  }, [addToast]);
  useEffect(reload, [reload]);
  if (!data) return <div className="empty">Loading feels…</div>;
  return (
    <div style={{ padding: "10px" }}>
      {open ? <FeelDetail key={open.id} feel={open} onBack={() => { setOpen(null); reload(); }} onChanged={reload} addToast={addToast} />
        : <FeelList data={data} onOpen={setOpen} onReload={reload} addToast={addToast} />}
    </div>
  );
}
