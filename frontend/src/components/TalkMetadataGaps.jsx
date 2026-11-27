import React, { useCallback, useEffect, useState } from "react";
import * as api from "../api";

const inputStyle = {
  flex: 1, minWidth: 0, padding: "3px 6px", borderRadius: 4, background: "var(--bg-surface)",
  border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: 11, outline: "none",
};

/** One catalog song with an empty artist or title; saving uses the normal song metadata update. */
function GapRow({ song, onSaved, addToast }) {
  const [artist, setArtist] = useState(song.artist);
  const [title, setTitle] = useState(song.title);
  const [saving, setSaving] = useState(false);
  const ready = artist.trim() && title.trim();

  const save = async () => {
    setSaving(true);
    try {
      const result = await api.updateSongMetadata(song.song_id, { artist: artist.trim(), title: title.trim() });
      if (result?.error) throw new Error(result.error);
      addToast?.("Song details saved", "success");
      onSaved();
    } catch (e) {
      addToast?.(e.message || "Failed to save song details", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ padding: "6px 0", borderBottom: "1px solid var(--border-subtle)" }}>
      <div style={{ fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        title={song.filename}>
        {song.filename || `Song ${song.song_id}`} · missing {song.missing.join(" and ")}
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
        <input aria-label="Artist" placeholder="Artist" value={artist} onChange={(e) => setArtist(e.target.value)} style={inputStyle} />
        <input aria-label="Title" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} style={inputStyle} />
        <button className="ui-btn" disabled={!ready || saving} onClick={save}
          style={{ fontSize: 10, padding: "3px 8px", opacity: !ready || saving ? 0.5 : 1 }}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

/**
 * Checkpoint 13C: songs the phone's connected talk couldn't name. Catalog songs can be fixed here; phone-only
 * titles come from unmatched file names and are listed for reference.
 */
export default function TalkMetadataGaps({ addToast }) {
  const [gaps, setGaps] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      setGaps(await api.fetchTalkMetadataGaps());
    } catch (e) {
      setError(e.message || "Couldn't load the list");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const catalog = gaps?.catalog || [];
  const phone = gaps?.phone || [];

  return (
    <div style={{ paddingTop: 12 }}>
      <div className="set-label" style={{ marginBottom: 2 }}>Songs the DJ couldn't name</div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 6 }}>
        Found while your phone's Radio played talk. Saving updates the song's tags and file name, like the song editor.
      </div>
      {error && <div role="alert" style={{ fontSize: 11, color: "var(--danger)" }}>{error}</div>}
      {!error && gaps === null && <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading…</div>}
      {gaps && catalog.length === 0 && phone.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Nothing to fix.</div>
      )}
      {catalog.map((song) => (
        <GapRow key={song.song_id} song={song} onSaved={load} addToast={addToast} />
      ))}
      {phone.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 2 }}>
            Phone-only songs (rename the file on the phone, or match it to your desktop library):
          </div>
          {phone.map((entry) => (
            <div key={entry.title || "untitled"} style={{ fontSize: 11, color: "var(--text-secondary)", padding: "2px 0" }}>
              {entry.title || "Untitled file"}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
