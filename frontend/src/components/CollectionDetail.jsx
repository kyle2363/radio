import React, { useState, useEffect, useCallback, useRef } from "react";
import { ArrowLeft, Trash2, Search, Edit3, Check, X } from "lucide-react";
import * as api from "../api";
import { completionPercent, songSearchParams } from "./collectionDetailHelpers";

export default function CollectionDetail({ collection, onBack, addToast }) {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [songsError, setSongsError] = useState("");
  const [search, setSearch] = useState("");
  const [allSongs, setAllSongs] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searchPage, setSearchPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const searchRequestId = useRef(0);
  const songsRequestId = useRef(0);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");

  const loadSongs = useCallback(async () => {
    const requestId = ++songsRequestId.current;
    setLoading(true);
    setSongsError("");
    try {
      const data = await api.fetchCollectionSongs(collection.id);
      if (requestId === songsRequestId.current) setSongs(data.songs || []);
    } catch (e) {
      if (requestId === songsRequestId.current) setSongsError(e.message || "Failed to load collection songs");
    } finally {
      if (requestId === songsRequestId.current) setLoading(false);
    }
  }, [collection.id]);

  useEffect(() => {
    loadSongs();
    return () => { songsRequestId.current += 1; };
  }, [loadSongs]);

  useEffect(() => {
    if (!showAdd) return undefined;
    const requestId = ++searchRequestId.current;
    setSearchLoading(true);
    setAllSongs([]);
    setSearchError("");
    const timer = setTimeout(async () => {
      try {
        const data = await api.fetchSongs(songSearchParams(search, searchPage));
        if (requestId !== searchRequestId.current) return;
        setAllSongs(data.items || []);
        setTotalPages(data.total_pages || 1);
      } catch (e) {
        if (requestId === searchRequestId.current) {
          setAllSongs([]);
          setSearchError(e.message || "Failed to search songs");
        }
      } finally {
        if (requestId === searchRequestId.current) setSearchLoading(false);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      if (requestId === searchRequestId.current) searchRequestId.current += 1;
    };
  }, [showAdd, search, searchPage]);

  useEffect(() => {
    setSearchPage(1);
  }, [search]);

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

  const startEdit = () => {
    setEditName(collection.name);
    setEditDesc(collection.description || "");
    setEditing(true);
  };

  const saveEdit = async () => {
    if (!editName.trim()) return;
    try {
      await api.updateCollection(collection.id, editName.trim(), editDesc.trim());
      collection.name = editName.trim();
      collection.description = editDesc.trim();
      setEditing(false);
      addToast?.("Collection updated", "success");
    } catch (e) {
      addToast?.("Failed to update collection", "error");
    }
  };

  const cancelEdit = () => {
    setEditing(false);
  };

  const getPct = completionPercent;
  const pctClass = (pct) => pct >= 50 ? "pct-hi" : pct >= 40 ? "pct-mid" : "pct-lo";
  const songIds = new Set(songs.map((s) => s.id));

  const filteredAllSongs = allSongs.filter((s) => !songIds.has(s.id));

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <button onClick={onBack} style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--text-muted)" }}>
          <ArrowLeft size={14} />
        </button>
        {editing ? (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
            <input value={editName} onChange={(e) => setEditName(e.target.value)} autoFocus
              style={{ padding: "4px 6px", borderRadius: 4, background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: 12, fontWeight: 700, outline: "none" }} />
            <input value={editDesc} onChange={(e) => setEditDesc(e.target.value)} placeholder="Description (optional)"
              style={{ padding: "4px 6px", borderRadius: 4, background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: 10, outline: "none" }} />
          </div>
        ) : (
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{collection.name}</div>
            {collection.description && <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{collection.description}</div>}
          </div>
        )}
        {editing ? (
          <>
            <button onClick={saveEdit} title="Save"
              style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--accent)" }}>
              <Check size={14} />
            </button>
            <button onClick={cancelEdit} title="Cancel"
              style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--text-muted)" }}>
              <X size={14} />
            </button>
          </>
        ) : (
          <button onClick={startEdit} title="Edit name/description"
            style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--text-muted)" }}>
            <Edit3 size={12} />
          </button>
        )}
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
            {searchLoading ? <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "8px 0", textAlign: "center" }}>Searching songs...</div> : null}
            {!searchLoading && searchError ? <div role="alert" style={{ fontSize: 11, color: "var(--danger)", padding: "8px 0", textAlign: "center" }}>{searchError}</div> : null}
            {!searchLoading && !searchError && filteredAllSongs.length === 0 && <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "8px 0", textAlign: "center" }}>{search ? "No songs match" : "No songs available"}</div>}
            {filteredAllSongs.map((s) => (
              <div key={s.id} onClick={() => handleAdd(s.id)}
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 6px", cursor: "pointer", borderRadius: 4, fontSize: 11, color: "var(--text-primary)" }}>
                <span style={{ fontSize: 10, color: "var(--text-muted)", width: 16 }}>{s.bpm || "—"}</span>
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.artist} – {s.title}</span>
                <span style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer" }}>Add</span>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, fontSize: 10, marginTop: 4 }}>
            <button disabled={searchPage <= 1 || searchLoading} onClick={() => setSearchPage((page) => Math.max(1, page - 1))} style={{ color: "var(--accent)", background: "none", border: "none", cursor: "pointer", opacity: searchPage <= 1 ? 0.45 : 1 }}>Previous</button>
            <span style={{ color: "var(--text-muted)" }}>Page {searchPage} of {totalPages}</span>
            <button disabled={searchPage >= totalPages || searchLoading} onClick={() => setSearchPage((page) => Math.min(totalPages, page + 1))} style={{ color: "var(--accent)", background: "none", border: "none", cursor: "pointer", opacity: searchPage >= totalPages ? 0.45 : 1 }}>Next</button>
          </div>
        </div>
      )}

      <button onClick={() => setShowAdd((value) => !value)}
        style={{ marginBottom: 8, fontSize: 10, padding: "4px 10px", borderRadius: 4, background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: "pointer" }}>
        {showAdd ? "Cancel" : "+ Add songs"}
      </button>

      {loading ? (
        <div style={{ padding: "14px", fontSize: 12, color: "var(--text-muted)" }}>Loading...</div>
      ) : songsError ? (
        <div role="alert" style={{ padding: "14px", fontSize: 12, color: "var(--danger)" }}>{songsError}</div>
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
