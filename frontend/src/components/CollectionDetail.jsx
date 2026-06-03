import React, { useState, useEffect, useCallback } from "react";
import { ArrowLeft, Trash2, Search } from "lucide-react";
import * as api from "../api";

export default function CollectionDetail({ collection, onBack, addToast }) {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [allSongs, setAllSongs] = useState([]);
  const [showAdd, setShowAdd] = useState(false);

  const loadSongs = useCallback(async () => {
    try {
      const data = await api.fetchCollectionSongs(collection.id);
      setSongs(data.songs || []);
    } catch (e) {}
    setLoading(false);
  }, [collection.id]);

  useEffect(() => { loadSongs(); }, [loadSongs]);

  const fetchAllSongs = async () => {
    try {
      const data = await api.fetchSongs({ page: "1", page_size: "200", sort_by: "title" });
      setAllSongs(data.items || []);
    } catch (e) {}
  };

  const handleRemove = async (e, songId) => {
    e.stopPropagation();
    try {
      await api.removeFromCollection(collection.id, songId);
      loadSongs();
    } catch (e) {
      addToast?.("Failed to remove song", "error");
    }
  };

  const handleAdd = async (songId) => {
    try {
      await api.addToCollection(collection.id, songId);
      loadSongs();
      addToast?.("Added to collection", "success");
    } catch (e) {
      addToast?.("Failed to add song", "error");
    }
  };

  const getPct = (song) => song.avg_completion != null ? Math.round(song.avg_completion * 100) : 0;
  const pctClass = (pct) => pct >= 50 ? "pct-hi" : pct >= 40 ? "pct-mid" : "pct-lo";
  const songIds = new Set(songs.map((s) => s.id));

  const filteredAllSongs = allSongs.filter((s) => !songIds.has(s.id) && (search ? `${s.title} ${s.artist}`.toLowerCase().includes(search.toLowerCase()) : true));

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <button onClick={onBack} style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--text-muted)" }}>
          <ArrowLeft size={14} />
        </button>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{collection.name}</div>
          {collection.description && <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{collection.description}</div>}
        </div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--text-muted)", padding: "2px 6px", borderRadius: 3, background: "var(--bg-elevated)" }}>{songs.length} song{songs.length !== 1 ? "s" : ""}</span>
      </div>

      {showAdd && (
        <div style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 6, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
            <Search size={12} style={{ color: "var(--text-muted)" }} />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search songs to add..."
              style={{ flex: 1, padding: "4px 6px", borderRadius: 4, background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: 11, outline: "none" }} />
            {search && <button onClick={() => setSearch("")} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", fontSize: 10 }}>Clear</button>}
          </div>
          <div style={{ maxHeight: 200, overflowY: "auto" }}>
            {filteredAllSongs.length === 0 && <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "8px 0", textAlign: "center" }}>{search ? "No songs match" : "Loading..."}</div>}
            {filteredAllSongs.slice(0, 30).map((s) => (
              <div key={s.id} onClick={() => handleAdd(s.id)}
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 6px", cursor: "pointer", borderRadius: 4, fontSize: 11, color: "var(--text-primary)" }}>
                <span style={{ fontSize: 10, color: "var(--text-muted)", width: 16 }}>{s.bpm || "—"}</span>
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.artist} – {s.title}</span>
                <span style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer" }}>Add</span>
              </div>
            ))}
          </div>
          <button onClick={() => fetchAllSongs()} style={{ fontSize: 10, color: "var(--accent)", background: "none", border: "none", cursor: "pointer", padding: "4px 0", marginTop: 4 }}>Load all songs</button>
        </div>
      )}

      <button onClick={async () => { setShowAdd(!showAdd); if (!showAdd && allSongs.length === 0) fetchAllSongs(); }}
        style={{ marginBottom: 8, fontSize: 10, padding: "4px 10px", borderRadius: 4, background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: "pointer" }}>
        {showAdd ? "Cancel" : "+ Add songs"}
      </button>

      {loading ? (
        <div style={{ padding: "14px", fontSize: 12, color: "var(--text-muted)" }}>Loading...</div>
      ) : songs.length === 0 ? (
        <div style={{ padding: "20px 14px", textAlign: "center", fontSize: 12, color: "var(--text-muted)" }}>No songs in this collection yet.</div>
      ) : (
        songs.map((song) => {
          const pct = getPct(song);
          return (
            <div key={song.id} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 8px", borderBottom: "1px solid var(--border-subtle)", fontSize: 11 }}>
              <span style={{ width: 20, fontSize: 10, color: "var(--text-muted)", textAlign: "center" }}>{song.position != null ? song.position + 1 : "—"}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-primary)" }}>{song.title}</div>
                <div style={{ fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{song.artist}</div>
              </div>
              <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{song.bpm || "—"}</span>
              <span className={`pct-badge ${pctClass(pct)}`} style={{ fontSize: 9 }}>{pct}%</span>
              <button onClick={(e) => handleRemove(e, song.id)} title="Remove"
                style={{ background: "none", border: "none", cursor: "pointer", padding: 2, color: "var(--danger)" }}>
                <Trash2 size={10} />
              </button>
            </div>
          );
        })
      )}
    </div>
  );
}
