import React, { useState, useEffect, useCallback } from "react";
import { Plus, XCircle, RotateCcw, Pencil } from "lucide-react";
import * as api from "../api";
import { SkeletonRow } from "./Skeleton";

function SongList({ onSongSelect, currentSongId, addToast }) {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [sortBy, setSortBy] = useState("title");
  const [sortDir, setSortDir] = useState("asc");
  const [editingSong, setEditingSong] = useState(null);
  const [editTags, setEditTags] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [tab, setTab] = useState("library");
  const [recentlyPlayed, setRecentlyPlayed] = useState([]);

  const fetchSongs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.fetchSongs({ page: String(page), page_size: "50", search, sort_by: sortBy, sort_dir: sortDir });
      setSongs(data.items || []);
      setTotalPages(data.total_pages || 1);
    } catch (err) {}
    setLoading(false);
  }, [page, search, sortBy, sortDir]);

  const fetchRecentlyPlayed = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.fetchRecentlyPlayed();
      setRecentlyPlayed(data || []);
    } catch (err) {}
    setLoading(false);
  }, []);

  useEffect(() => {
    if (tab === "library") fetchSongs();
    else if (tab === "recent") fetchRecentlyPlayed();
  }, [tab, fetchSongs, fetchRecentlyPlayed]);

  const handlePlay = async (song) => {
    try {
      await api.playSong(song.id);
      onSongSelect?.(song);
      addToast?.(`Now playing: ${song.title}`, "success");
    } catch (err) {
      addToast?.("Failed to play song", "error");
    }
  };

  const handleAddToQueue = async (e, song) => {
    e.stopPropagation();
    try {
      await api.addToQueue(song.id);
      addToast?.(`Added to queue: ${song.title}`, "success");
    } catch (err) {
      addToast?.("Failed to add to queue", "error");
    }
  };

  const handleBlacklist = async (e, song) => {
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
  };

  const handleReprocess = async (e, song) => {
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
  };

  const handleEditTags = (e, song) => {
    e.stopPropagation();
    setEditingSong(song);
    try { setEditTags(JSON.parse(song.user_tags || "[]").join(", ")); } catch { setEditTags(""); }
    setEditNotes(song.user_notes || "");
  };

  const handleSaveTags = async () => {
    if (!editingSong) return;
    const tags = editTags.split(",").map((t) => t.trim()).filter(Boolean);
    try {
      await api.updateSongTags(editingSong.id, tags, editNotes);
      addToast?.("Tags saved", "success");
      setEditingSong(null);
      fetchSongs();
    } catch (err) {
      addToast?.("Failed to save tags", "error");
    }
  };

  const renderSongItem = (song) => (
    <div key={song.id} className={`flex items-center gap-3 px-5 py-3.5 cursor-pointer transition group ${currentSongId === song.id ? "bg-green-900/15" : "hover:bg-gray-800/50"}`} onClick={() => handlePlay(song)}>
      <span className="text-gray-600 flex-shrink-0">{currentSongId === song.id ? "\u25B6" : "\u{1F3B5}"}</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-gray-200 truncate">{song.title || "Unknown Title"}</div>
        <div className="text-xs text-gray-500 truncate">{song.artist || "Unknown Artist"}</div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {song.bpm && <span className="text-xs text-gray-600 tabular-nums">{song.bpm}</span>}
        {song.energy != null && <span className="text-xs text-gray-600 tabular-nums">{Math.round(song.energy * 100)}%</span>}
      </div>
      <div className="flex items-center gap-1 flex-shrink-0 opacity-0 group-hover:opacity-100 transition">
        <button onClick={(e) => handleAddToQueue(e, song)} className="p-2 rounded text-cyan-400 hover:bg-cyan-900/30 transition" title="Add to queue"><Plus size={14} /></button>
        <button onClick={(e) => handleBlacklist(e, song)} className="p-2 rounded text-red-400 hover:bg-red-900/30 transition" title="Blacklist"><XCircle size={14} /></button>
        <button onClick={(e) => handleReprocess(e, song)} className="p-2 rounded text-yellow-400 hover:bg-yellow-900/30 transition" title="Reprocess"><RotateCcw size={14} /></button>
        <button onClick={(e) => handleEditTags(e, song)} className="p-2 rounded text-gray-400 hover:bg-gray-700/50 transition" title="Edit tags"><Pencil size={14} /></button>
      </div>
    </div>
  );

  const displaySongs = tab === "recent" ? recentlyPlayed : songs;

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button onClick={() => { setTab("library"); setPage(1); }} className={`px-4 py-2 rounded-lg text-xs font-medium transition ${tab === "library" ? "bg-green-600 text-white" : "bg-gray-800 text-gray-400 hover:text-gray-300"}`}>Library</button>
        <button onClick={() => setTab("recent")} className={`px-4 py-2 rounded-lg text-xs font-medium transition ${tab === "recent" ? "bg-green-600 text-white" : "bg-gray-800 text-gray-400 hover:text-gray-300"}`}>Recently Played</button>
      </div>
      {tab === "library" && (
        <div className="flex items-center gap-3">
          <input type="text" placeholder="Search songs..." value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} className="flex-1 px-4 py-2.5 rounded-xl bg-gray-800 text-gray-200 text-sm focus:outline-none" />
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className="px-4 py-2.5 rounded-xl bg-gray-800 text-gray-200 text-sm focus:outline-none">
            <option value="title">Title</option>
            <option value="artist">Artist</option>
            <option value="energy">Energy</option>
            <option value="valence">Mood</option>
            <option value="bpm">BPM</option>
          </select>
          <button onClick={() => setSortDir(sortDir === "asc" ? "desc" : "asc")} className="px-4 py-2.5 rounded-xl bg-gray-800 text-gray-400 text-sm transition" title={sortDir === "asc" ? "Ascending" : "Descending"}>
            {sortDir === "asc" ? "\u2191" : "\u2193"}
          </button>
        </div>
      )}
          <div className="rounded-xl bg-gray-900/50 overflow-hidden">
        {loading ? (
          <SkeletonRow count={8} />
        ) : displaySongs.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <div className="text-gray-700 text-3xl mb-2">
              {tab === "recent" ? "\u{1F3B5}" : search ? "\u{1F50D}" : "\u{1F4E6}"}
            </div>
            <p className="text-sm text-gray-500">
              {tab === "recent" ? "No songs played yet" : search ? `No songs match "${search}"` : "No songs found"}
            </p>
            {search && <button onClick={() => setSearch("")} className="mt-2 text-xs text-green-500 hover:text-green-400 transition">Clear search</button>}
          </div>
        ) : (
          <div className="divide-y divide-gray-800">{displaySongs.map((song) => renderSongItem(song))}</div>
        )}
      </div>
      {tab === "library" && totalPages > 1 && (
        <div className="flex justify-center gap-2">
          <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1} className="px-3 py-1.5 rounded-lg bg-gray-800 text-gray-400 text-xs transition disabled:opacity-30">Prev</button>
          <span className="px-3 py-1.5 text-xs text-gray-500">{page} / {totalPages}</span>
          <button onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page === totalPages} className="px-3 py-1.5 rounded-lg bg-gray-800 text-gray-400 text-xs transition disabled:opacity-30">Next</button>
        </div>
      )}
      {editingSong && (
        <div className="fixed inset-0 bg-gray-950/90 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-gray-900 rounded-xl p-7 w-full max-w-md space-y-4">
            <h3 className="text-lg font-bold text-white">{editingSong.title || "Unknown Title"}</h3>
            <p className="text-sm text-gray-500">{editingSong.artist || "Unknown Artist"}</p>
            <div>
              <label className="block text-xs text-gray-400 mb-1">User Tags (comma-separated)</label>
              <input type="text" value={editTags} onChange={(e) => setEditTags(e.target.value)} placeholder="e.g. favorite, workout, relaxing" className="w-full px-4 py-2.5 rounded-xl bg-gray-800 text-gray-200 text-sm focus:outline-none" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Notes</label>
              <textarea value={editNotes} onChange={(e) => setEditNotes(e.target.value)} placeholder="Personal notes about this song..." rows={3} className="w-full px-4 py-2.5 rounded-xl bg-gray-800 text-gray-200 text-sm focus:outline-none resize-none" />
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setEditingSong(null)} className="px-5 py-2.5 rounded-xl bg-gray-800 text-gray-400 text-sm transition">Cancel</button>
              <button onClick={handleSaveTags} className="px-5 py-2.5 rounded-lg bg-green-600 text-white text-sm hover:bg-green-500 transition">Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default SongList;
