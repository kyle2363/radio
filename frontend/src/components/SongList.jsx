import React, { useState, useEffect, useCallback, useRef } from "react";
import { Plus, Heart, MoreHorizontal, Music, Play, Bolt, ChevronUp, ChevronDown, XCircle, RotateCcw, Pencil, Edit3 } from "lucide-react";
import * as api from "../api";
import { SkeletonRow } from "./Skeleton";
import StatsDashboard from "./StatsDashboard";
import SessionHistory from "./SessionHistory";
import MoodTimeline from "./MoodTimeline";

const COLUMNS = [
  { key: null, label: "", sortable: false },
  { key: "title", label: "Title", sortable: true },
  { key: "bpm", label: "BPM", sortable: true },
  { key: "energy", label: "E", sortable: true },
  { key: "valence", label: "V", sortable: true },
  { key: "play_count", label: "", sortable: true },
  { key: "avg_completion", label: "Cmp", sortable: true },
];

function SongList({ addToast, currentSongId, onPlay }) {
  const [tab, setTab] = useState("library");
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [sortBy, setSortBy] = useState("title");
  const [sortDir, setSortDir] = useState("asc");
  const [stats, setStats] = useState(null);
  const [recentlyPlayed, setRecentlyPlayed] = useState([]);
  const [editingSong, setEditingSong] = useState(null);
  const [editTags, setEditTags] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [editingMetadata, setEditingMetadata] = useState(null);
  const [metaForm, setMetaForm] = useState({
    artist: "", title: "", album: "", bpm: "",
    keyscale: "", timesignature: "", vocal_language: "", lyrics: "", caption: "",
  });
  const [savingMetadata, setSavingMetadata] = useState(false);
  const [missingMetadata, setMissingMetadata] = useState(false);
  const [missingCaption, setMissingCaption] = useState(false);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [flashedId, setFlashedId] = useState(null);
  const menuRef = useRef(null);

  useEffect(() => {
    fetchStats();
  }, []);

  useEffect(() => {
    if (tab === "library") fetchSongs();
    else if (tab === "recent") fetchRecentlyPlayed();
  }, [tab, page, search, sortBy, sortDir, missingMetadata, missingCaption]);

  useEffect(() => {
    const handleClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpenMenuId(null);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const fetchStats = async () => {
    try {
      const data = await api.fetchStatsOverview();
      setStats(data);
    } catch (e) {}
  };

  const fetchSongs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.fetchSongs({ page: String(page), page_size: "50", search, sort_by: sortBy, sort_dir: sortDir, missing_metadata: String(missingMetadata), missing_caption: String(missingCaption) });
      setSongs(data.items || []);
      setTotalPages(data.total_pages || 1);
    } catch (err) {}
    setLoading(false);
  }, [page, search, sortBy, sortDir, missingMetadata]);

  const fetchRecentlyPlayed = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.fetchRecentlyPlayed();
      setRecentlyPlayed(data || []);
    } catch (err) {}
    setLoading(false);
  }, []);

  const handleColumnClick = (colKey) => {
    if (!colKey) return;
    if (sortBy === colKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(colKey);
      setSortDir("asc");
    }
    setPage(1);
  };

  const handlePlay = useCallback(async (song) => {
    try {
      await api.playSong(song.id);
      onPlay?.(song);
      addToast?.(`Now playing: ${song.title}`, "success");
    } catch (err) {
      addToast?.("Failed to play song", "error");
    }
  }, [onPlay, addToast]);

  const handleAddToQueue = useCallback(async (e, song) => {
    e.stopPropagation();
    try {
      await api.addToQueue(song.id);
      setFlashedId(song.id);
      setTimeout(() => setFlashedId(null), 1500);
      addToast?.(`Added to queue: ${song.title}`, "success");
    } catch (err) {
      addToast?.("Failed to add to queue", "error");
    }
  }, [addToast]);

  const handleBlacklist = useCallback(async (e, song) => {
    e.stopPropagation();
    try {
      const res = await api.blacklistSong(song.id);
      if (res.ok !== false) {
        addToast?.(`Blacklisted: ${song.title}`, "success");
        fetchSongs();
      }
    } catch (err) {
      addToast?.("Failed to blacklist song", "error");
    }
  }, [addToast, fetchSongs]);

  const handleReprocess = useCallback(async (e, song) => {
    e.stopPropagation();
    try {
      const data = await api.reprocessSong(song.id);
      if (data.status === "reprocessed") {
        addToast?.(`Reprocessed: ${song.title} (E:${(data.energy * 100).toFixed(0)}% V:${(data.valence * 100).toFixed(0)}%)`, "success");
      } else {
        addToast?.(data.error || "Reprocess failed", "error");
      }
    } catch (err) {
      addToast?.("Failed to reprocess song", "error");
    }
  }, [addToast, fetchSongs]);

  const handleEditTags = useCallback((e, song) => {
    e.stopPropagation();
    setOpenMenuId(null);
    setEditingSong(song);
    try { setEditTags(JSON.parse(song.user_tags || "[]").join(", ")); } catch { setEditTags(""); }
    setEditNotes(song.user_notes || "");
  }, []);

  const handleSaveTags = useCallback(async () => {
    if (!editingSong) return;
    const tags = editTags.split(",").map((t) => t.trim()).filter(Boolean);
    const notes = editNotes.trim();
    if (tags.length === 0 && !notes) {
      addToast?.("Enter at least a tag or note", "error");
      return;
    }
    try {
      await api.updateSongTags(editingSong.id, tags, notes);
      addToast?.("Tags saved", "success");
      setEditingSong(null);
      fetchSongs();
    } catch (err) {
      addToast?.("Failed to save tags", "error");
    }
  }, [editingSong, editTags, editNotes, addToast, fetchSongs]);

  const handleOpenMetadata = useCallback(async (e, song) => {
    e.stopPropagation();
    setOpenMenuId(null);
    setEditingMetadata(song);
    setMetaForm({
      artist: song.artist || "",
      title: song.title || "",
      album: song.album || "",
      bpm: song.bpm ?? "",
      keyscale: song.keyscale || "",
      timesignature: song.timesignature || "",
      vocal_language: song.vocal_language || "",
      lyrics: song.lyrics || "",
      caption: song.ai_description || "",
    });
  }, []);

  const handleSaveMetadata = useCallback(async () => {
    if (!editingMetadata) return;
    setSavingMetadata(true);
    try {
      const payload = {};
      if (metaForm.artist?.trim()) payload.artist = metaForm.artist.trim();
      if (metaForm.title?.trim()) payload.title = metaForm.title.trim();
      if (metaForm.album?.trim()) payload.album = metaForm.album.trim();
      if (metaForm.bpm !== "") payload.bpm = parseInt(metaForm.bpm, 10);
      if (metaForm.keyscale?.trim()) payload.keyscale = metaForm.keyscale.trim();
      if (metaForm.timesignature?.trim()) payload.timesignature = metaForm.timesignature.trim();
      if (metaForm.vocal_language?.trim()) payload.vocal_language = metaForm.vocal_language.trim();
      if (metaForm.lyrics !== undefined) payload.lyrics = metaForm.lyrics;
      if (metaForm.caption !== undefined) payload.caption = metaForm.caption;

      await api.updateSongMetadata(editingMetadata.id, payload);
      const title = metaForm.title.trim() || editingMetadata.title || "Song";
      addToast?.(`Metadata saved for "${title}"`, "success");
      setEditingMetadata(null);
      fetchSongs();
    } catch (err) {
      addToast?.("Failed to save metadata", "error");
    } finally {
      setSavingMetadata(false);
    }
  }, [editingMetadata, metaForm, addToast, fetchSongs]);

  const handleHeart = useCallback(async (e, song) => {
    e.stopPropagation();
    try {
      await api.playSong(song.id);
      await api.heartSong();
      addToast?.(`Hearted: ${song.title}`, "success");
    } catch (err) {
      addToast?.("Failed to heart song", "error");
    }
  }, [addToast]);

  const handleTabChange = (newTab) => {
    setTab(newTab);
    if (newTab === "library") { setPage(1); setSearch(""); setMissingMetadata(false); setMissingCaption(false); }
  };

  const displaySongs = tab === "recent" ? recentlyPlayed : songs;

  return (
    <div>
      {/* Subtabs */}
      <div className="subtab-row">
        <div className={`subtab ${tab === "library" ? "on" : ""}`} onClick={() => handleTabChange("library")}>Library</div>
        <div className={`subtab ${tab === "recent" ? "on" : ""}`} onClick={() => handleTabChange("recent")}>Recently Played</div>
      </div>

      {/* Library tab */}
      {tab === "library" && (
        <>
          {/* Stat cards */}
          {stats && (
            <div className="stat-cards">
              <div className="stat-card">
                <div className="stat-num">{stats.total_songs_in_library || 0}</div>
                <div className="stat-desc">total songs</div>
              </div>
              <div className="stat-card">
                <div className="stat-num accent">{stats.avg_completion_rate || 0}%</div>
                <div className="stat-desc">avg completion</div>
              </div>
              <div className="stat-card">
                <div className="stat-num" style={{ fontSize: "15px", paddingTop: "4px", color: "var(--text-primary)" }}>
                  {stats.most_completed?.title || "—"}
                </div>
                <div className="stat-desc">most completed</div>
              </div>
            </div>
          )}

          {/* Search bar */}
          <div className="search-bar">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
            </svg>
            <input className="search-input" placeholder="Search songs, artists…" value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
            <select className="sort-sel" value={missingMetadata ? "__missing__" : missingCaption ? "__missing_caption__" : `${sortBy}-${sortDir}`}
              onChange={(e) => {
                setMissingMetadata(false);
                setMissingCaption(false);
                if (e.target.value === "__missing__") {
                  setMissingMetadata(true);
                } else if (e.target.value === "__missing_caption__") {
                  setMissingCaption(true);
                } else {
                  const [k, d] = e.target.value.split("-");
                  setSortBy(k);
                  setSortDir(d);
                }
                setPage(1);
              }}>
              <option value="title-asc">Title</option>
              <option value="artist-asc">Artist</option>
              <option value="bpm-asc">BPM</option>
              <option value="energy-desc">Energy</option>
              <option value="avg_completion-desc">Completion</option>
              <option value="instrumental-asc">Instrumental</option>
              <option disabled>──────────</option>
              <option value="__missing__">Missing Metadata</option>
              <option value="__missing_caption__">Missing Caption</option>
            </select>
          </div>

          {/* Column headers */}
          <div className="col-hdr">
            <div></div>
            <div className={`col-hdr-cell ${sortBy === "title" ? "sorted" : ""}`} onClick={() => handleColumnClick("title")}>
              Title
              {sortBy === "title" ? (
                sortDir === "asc" ? <ChevronUp size={10} style={{ color: "var(--accent)" }} /> : <ChevronDown size={10} style={{ color: "var(--accent)" }} />
              ) : null}
            </div>
            <div className={`col-hdr-cell ${sortBy === "bpm" ? "sorted" : ""}`} onClick={() => handleColumnClick("bpm")}>BPM</div>
            <div className={`col-hdr-cell ${sortBy === "energy" ? "sorted" : ""}`} onClick={() => handleColumnClick("energy")} title="Energy">
              E <Bolt size={9} style={{ color: "var(--accent)" }} />
            </div>
            <div className={`col-hdr-cell ${sortBy === "valence" ? "sorted" : ""}`} onClick={() => handleColumnClick("valence")} title="Valence">
              V <Heart size={9} style={{ color: "#5eadf5" }} />
            </div>
            <div className={`col-hdr-cell ${sortBy === "play_count" ? "sorted" : ""}`} onClick={() => handleColumnClick("play_count")} title="Plays">
              <Play size={10} />
            </div>
            <div className={`col-hdr-cell ${sortBy === "avg_completion" ? "sorted" : ""}`} onClick={() => handleColumnClick("avg_completion")}>Cmp</div>
          </div>

          {/* Song rows */}
          <div>
            {loading ? (
              <SkeletonRow count={8} />
            ) : displaySongs.length === 0 ? (
              <div style={{ padding: "24px 14px", textAlign: "center" }}>
                <div style={{ fontSize: "24px", color: "var(--text-muted)", marginBottom: "8px" }}>
                  {search ? "\u{1F50D}" : "\u{1F4E6}"}
                </div>
                <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                  {search ? `No songs match "${search}"` : missingMetadata ? "No songs with missing metadata" : missingCaption ? "No songs with missing captions" : "No songs found"}
                </p>
                {(search || missingMetadata || missingCaption) && (
                  <button onClick={() => { setSearch(""); setMissingMetadata(false); setMissingCaption(false); }}
                    style={{ marginTop: "8px", fontSize: "11px", color: "var(--accent)", background: "var(--accent-muted)", padding: "4px 10px", borderRadius: "5px", border: "none", cursor: "pointer" }}>
                    Clear filters
                  </button>
                )}
              </div>
            ) : (
              displaySongs.map((song) => (
                <SongRow key={song.id} song={song} isPlaying={currentSongId === song.id}
                  flashed={flashedId === song.id}
                  onPlay={handlePlay} onAddToQueue={handleAddToQueue}
                  onBlacklist={handleBlacklist} onReprocess={handleReprocess}
                  onEditTags={handleEditTags} onEditMetadata={handleOpenMetadata} onHeart={handleHeart}
                  openMenuId={openMenuId} setOpenMenuId={setOpenMenuId} menuRef={menuRef} />
              ))
            )}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div style={{ display: "flex", justifyContent: "center", gap: "8px", padding: "12px 14px", borderTop: "1px solid var(--border-subtle)" }}>
              <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1}
                style={{ padding: "4px 10px", borderRadius: "5px", fontSize: "11px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: page === 1 ? "var(--text-muted)" : "var(--text-secondary)", cursor: page === 1 ? "default" : "pointer", opacity: page === 1 ? 0.4 : 1 }}>
                Prev
              </button>
              <span style={{ padding: "4px 10px", fontSize: "11px", color: "var(--text-muted)" }}>{page} / {totalPages}</span>
              <button onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page === totalPages}
                style={{ padding: "4px 10px", borderRadius: "5px", fontSize: "11px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: page === totalPages ? "var(--text-muted)" : "var(--text-secondary)", cursor: page === totalPages ? "default" : "pointer", opacity: page === totalPages ? 0.4 : 1 }}>
                Next
              </button>
            </div>
          )}
        </>
      )}

      {/* Recently played tab */}
      {tab === "recent" && (
        <div>
          <div className="col-hdr">
            <div></div>
            <div className="col-hdr-cell">Title</div>
            <div className="col-hdr-cell">BPM</div>
            <div className="col-hdr-cell" title="Energy">E</div>
            <div className="col-hdr-cell" title="Valence">V</div>
            <div className="col-hdr-cell"><Play size={10} /></div>
            <div className="col-hdr-cell">Cmp</div>
          </div>
          <div>
            {loading ? (
              <SkeletonRow count={5} />
            ) : displaySongs.length === 0 ? (
              <div style={{ padding: "24px 14px", textAlign: "center" }}>
                <div style={{ fontSize: "24px", color: "var(--text-muted)", marginBottom: "8px" }}>{"\u{1F3B5}"}</div>
                <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>No songs played yet</p>
              </div>
            ) : (
              displaySongs.map((song) => (
                <SongRow key={song.id} song={song} isPlaying={currentSongId === song.id}
                  flashed={flashedId === song.id}
                  onPlay={handlePlay} onAddToQueue={handleAddToQueue}
                  onBlacklist={handleBlacklist} onReprocess={handleReprocess}
                  onEditTags={handleEditTags} onEditMetadata={handleOpenMetadata} onHeart={handleHeart}
                  openMenuId={openMenuId} setOpenMenuId={setOpenMenuId} menuRef={menuRef} />
              ))
            )}
          </div>
        </div>
      )}

      {/* Stats tab */}
      {tab === "stats" && <StatsDashboard />}

      {/* History tab */}
      {tab === "history" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <MoodTimeline />
          <SessionHistory />
        </div>
      )}

      {/* Edit tags modal */}
      {editingSong && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "var(--bg-overlay)" }}>
          <div style={{ background: "var(--bg-surface)", borderRadius: "12px", padding: "24px", width: "100%", maxWidth: "420px", display: "flex", flexDirection: "column", gap: "16px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-primary)" }}>{editingSong.title || "Unknown Title"}</h3>
            <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>{editingSong.artist || "Unknown Artist"}</p>
            <div>
              <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>User Tags (comma-separated)</label>
              <input type="text" value={editTags} onChange={(e) => setEditTags(e.target.value)}
                placeholder="e.g. favorite, workout, relaxing"
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none" }} />
            </div>
            <div>
              <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Notes</label>
              <textarea value={editNotes} onChange={(e) => setEditNotes(e.target.value)}
                placeholder="Personal notes about this song…" rows={3}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", resize: "none" }} />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button onClick={() => setEditingSong(null)}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)", fontSize: "12px", cursor: "pointer" }}>
                Cancel
              </button>
              <button onClick={handleSaveTags}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", fontSize: "12px", fontWeight: 500, cursor: "pointer" }}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Metadata Editor Modal */}
      {editingMetadata && (
        <div style={{ position: "fixed", inset: 0, zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.6)" }}
          onClick={() => setEditingMetadata(null)}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--bg-surface)", borderRadius: "12px", border: "1px solid var(--border)", padding: "24px", width: "480px", maxHeight: "85vh", overflow: "auto", boxShadow: "0 8px 32px rgba(0,0,0,0.4)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Edit Metadata</h3>
              <button onClick={() => setEditingMetadata(null)} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "18px", padding: "4px" }}>×</button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div>
                <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Artist</label>
                <input type="text" value={metaForm.artist} onChange={(e) => setMetaForm({ ...metaForm, artist: e.target.value })}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", boxSizing: "border-box" }} />
              </div>
              <div>
                <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Title</label>
                <input type="text" value={metaForm.title} onChange={(e) => setMetaForm({ ...metaForm, title: e.target.value })}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", boxSizing: "border-box" }} />
              </div>
              <div>
                <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Album</label>
                <input type="text" value={metaForm.album} onChange={(e) => setMetaForm({ ...metaForm, album: e.target.value })}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", boxSizing: "border-box" }} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>BPM</label>
                  <input type="number" value={metaForm.bpm} onChange={(e) => setMetaForm({ ...metaForm, bpm: e.target.value })}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", boxSizing: "border-box" }} />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Key/Scale</label>
                  <input type="text" value={metaForm.keyscale} onChange={(e) => setMetaForm({ ...metaForm, keyscale: e.target.value })}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", boxSizing: "border-box" }} />
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Time Sig</label>
                  <input type="text" value={metaForm.timesignature} onChange={(e) => setMetaForm({ ...metaForm, timesignature: e.target.value })}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", boxSizing: "border-box" }} />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Language</label>
                  <input type="text" value={metaForm.vocal_language} onChange={(e) => setMetaForm({ ...metaForm, vocal_language: e.target.value })}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", boxSizing: "border-box" }} />
                </div>
              </div>
              <div>
                <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Caption (AI Description)</label>
                <textarea value={metaForm.caption} onChange={(e) => setMetaForm({ ...metaForm, caption: e.target.value })}
                  placeholder="acestep caption / AI description" rows={3}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", resize: "none", boxSizing: "border-box" }} />
              </div>
              <div>
                <label style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px" }}>Lyrics</label>
                <textarea value={metaForm.lyrics} onChange={(e) => setMetaForm({ ...metaForm, lyrics: e.target.value })}
                  placeholder="Song lyrics" rows={6}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: "12px", outline: "none", resize: "none", boxSizing: "border-box" }} />
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "20px" }}>
              <button onClick={() => setEditingMetadata(null)} disabled={savingMetadata}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)", fontSize: "12px", cursor: "pointer" }}>
                Cancel
              </button>
              <button onClick={handleSaveMetadata} disabled={savingMetadata}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", fontSize: "12px", fontWeight: 500, cursor: "pointer", opacity: savingMetadata ? 0.6 : 1 }}>
                {savingMetadata ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const SongRow = React.memo(({ song, isPlaying, flashed, onPlay, onAddToQueue, onBlacklist, onReprocess, onEditTags, onEditMetadata, onHeart, openMenuId, setOpenMenuId, menuRef }) => {
  const energyPct = Math.round((song.energy ?? 0.5) * 100);
  const valencePct = Math.round((song.valence ?? 0.5) * 100);
  const completionPct = Math.round(song.avg_completion ?? 0);
  const pctClass = completionPct >= 50 ? "pct-hi" : completionPct >= 40 ? "pct-mid" : "pct-lo";
  const isOpen = openMenuId === song.id;

  return (
    <div className={`song-row ${isPlaying ? "playing" : ""} ${flashed ? "add-flash" : ""}`} onClick={() => onPlay(song)}>
      <Music size={13} style={{ color: "var(--text-muted)" }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ color: "var(--text-primary)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "12px" }}>
          {song.title || "Unknown Title"}
        </div>
        <div style={{ color: "var(--text-muted)", fontSize: "10px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: "1px" }}>
          {song.artist || "Unknown Artist"}
        </div>
      </div>
      <div style={{ fontSize: "11px", textAlign: "center", color: "var(--text-secondary)" }}>{song.bpm || "—"}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: "3px", alignItems: "center" }}>
        <div className="energy-bar"><div className="energy-fill" style={{ width: `${energyPct}%` }} /></div>
        <div style={{ fontSize: "9px", color: "var(--text-muted)" }}>{energyPct}%</div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "3px", alignItems: "center" }}>
        <div className="energy-bar"><div className="energy-fill valence" style={{ width: `${valencePct}%` }} /></div>
        <div style={{ fontSize: "9px", color: "var(--text-muted)" }}>{valencePct}%</div>
      </div>
      <div style={{ fontSize: "11px", textAlign: "center", color: "var(--text-muted)" }}>{song.play_count || 0}</div>
      <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
        <span className={`pct-badge ${pctClass}`}>{completionPct}%</span>
        <div className="row-actions">
          <div className="act-btn" title="Add to queue" onClick={(e) => onAddToQueue(e, song)}>
            <Plus size={11} />
          </div>
          <div className="act-btn" title="Heart" onClick={(e) => onHeart(e, song)}>
            <Heart size={11} />
          </div>
          <div className="act-btn" title="More" onClick={(e) => { e.stopPropagation(); setOpenMenuId(isOpen ? null : song.id); }}
            style={{ position: "relative" }}>
            <MoreHorizontal size={11} />
            {isOpen && (
              <div ref={menuRef}
                style={{ position: "absolute", top: "100%", right: 0, marginTop: "4px", zIndex: 20,
                  background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "6px",
                  padding: "4px", minWidth: "120px", boxShadow: "0 4px 12px rgba(0,0,0,0.3)" }}>
                <div onClick={(e) => onEditTags(e, song)}
                  style={{ display: "flex", alignItems: "center", gap: "6px", padding: "6px 8px", borderRadius: "4px", fontSize: "11px", color: "var(--text-secondary)", cursor: "pointer" }}>
                  <Pencil size={11} /> Edit tags
                </div>
                <div onClick={(e) => onEditMetadata(e, song)}
                  style={{ display: "flex", alignItems: "center", gap: "6px", padding: "6px 8px", borderRadius: "4px", fontSize: "11px", color: "var(--text-secondary)", cursor: "pointer" }}>
                  <Edit3 size={11} /> Edit metadata
                </div>
                <div onClick={(e) => onReprocess(e, song)}
                  style={{ display: "flex", alignItems: "center", gap: "6px", padding: "6px 8px", borderRadius: "4px", fontSize: "11px", color: "var(--text-secondary)", cursor: "pointer" }}>
                  <RotateCcw size={11} /> Reprocess
                </div>
                <div style={{ borderTop: "1px solid var(--border-subtle)", margin: "2px 0" }} />
                <div onClick={(e) => onBlacklist(e, song)}
                  style={{ display: "flex", alignItems: "center", gap: "6px", padding: "6px 8px", borderRadius: "4px", fontSize: "11px", color: "var(--danger)", cursor: "pointer" }}>
                  <XCircle size={11} /> Blacklist
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});

SongRow.displayName = "SongRow";

export default SongList;
