import React, { useState, useEffect, useCallback } from "react";
import { Play, Clock } from "lucide-react";
import * as api from "../api";

function timeAgo(ts) {
  if (!ts) return "";
  const seconds = Math.max(0, (Date.now() / 1000 - new Date(ts).getTime() / 1000) | 0);
  if (seconds < 60) return "just now";
  const mins = seconds / 60 | 0;
  if (mins < 60) return `${mins}m ago`;
  const hrs = mins / 60 | 0;
  if (hrs < 24) return `${hrs}h ago`;
  return `${hrs / 24 | 0}d ago`;
}

const PriorSongs = React.memo(function PriorSongs({ currentSongId, compact = false }) {
  const [songs, setSongs] = useState([]);
  const [playing, setPlaying] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await api.fetchPriorSongs();
      setSongs(data || []);
    } catch (e) { /* silent */ }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    if (currentSongId) load();
  }, [currentSongId, load]);

  const sectionHeader = (
    <div style={{
      fontSize: compact ? 9 : 10.5,
      letterSpacing: ".1em",
      color: compact ? "#2dd4a0" : "var(--text-muted)",
      fontWeight: 700,
      marginBottom: compact ? 5 : 8,
      textTransform: "uppercase",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
    }}>
      <span>Recently Played</span>
      {songs.length > 0 && !compact && (
        <span style={{ fontSize: 10, padding: "1px 5px", borderRadius: "4px", background: "rgba(255,255,255,0.05)", color: "var(--text-muted)", fontWeight: 500 }}>{songs.length}</span>
      )}
    </div>
  );

  const handlePlay = async (id) => {
    setPlaying(id);
    try {
      await api.playSong(id);
    } catch (e) { /* silent */ }
    setPlaying(null);
  };

  const emptyNode = (
    <div style={{ fontSize: compact ? 10 : 12, textAlign: "center", padding: "10px 0", color: "var(--text-muted)" }}>
      No songs played yet
    </div>
  );

  const songList = songs.map((song, idx) => compact ? (
    <div key={song.id} onClick={() => handlePlay(song.id)}
      style={{ display: "flex", alignItems: "center", gap: 7, padding: "3px 0", borderBottom: idx < songs.length - 1 ? "1px solid var(--border-subtle)" : "none", cursor: "pointer" }}>
      <span style={{ fontSize: 10, color: "var(--text-muted)", width: 11, textAlign: "center", flexShrink: 0 }}>{idx + 1}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {song.artist} – {song.title}
        </div>
      </div>
      {song.bpm > 0 && <span style={{ fontSize: 9, color: "var(--text-muted)", flexShrink: 0 }}>{song.bpm} BPM</span>}
    </div>
  ) : (
    <div key={song.id}
      className="flex items-center gap-2.5 py-2 cursor-pointer rounded transition group row-hover"
      style={{ borderBottom: idx < songs.length - 1 ? "1px solid var(--border-subtle)" : "none" }}
      onClick={() => handlePlay(song.id)}>
      <span className="text-[10px] font-mono flex-shrink-0 mt-0.5 min-w-[14px] text-center" style={{ color: "var(--text-muted)" }}>
        {idx + 1}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium truncate" style={{ color: "var(--text-secondary)" }}>
          {song.artist} – {song.title}
        </div>
        <div className="flex items-center gap-1.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
          <Clock size={9} />
          <span>{timeAgo(song.last_played)}</span>
          {song.bpm && <span>· {song.bpm} BPM</span>}
        </div>
      </div>
      <span className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" style={{ color: "var(--accent)" }}>
        {playing === song.id ? <Clock size={14} className="animate-spin" /> : <Play size={13} />}
      </span>
    </div>
  ));

  if (compact) {
    return (
      <div>
        {sectionHeader}
        {songs.length === 0 ? emptyNode : songList}
      </div>
    );
  }

  return (
    <div className="rounded-xl p-5" style={{ background: "var(--bg-surface)" }}>
      {sectionHeader}
      {songs.length === 0 ? emptyNode : (
        <div className="space-y-0.5">{songList}</div>
      )}
    </div>
  );
});

export default PriorSongs;
