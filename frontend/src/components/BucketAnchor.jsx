import React from "react";

const BUCKET_STYLES = {
  hot: { color: "#e74c3c", bg: "rgba(231,76,60,0.08)" },
  warm: { color: "#e67e22", bg: "rgba(230,126,34,0.08)" },
  mellow: { color: "#7c5cfc", bg: "rgba(124,92,252,0.08)" },
  cold: { color: "#3498db", bg: "rgba(52,152,219,0.08)" },
};

const BUCKET_LABELS = {
  hot: "Hot (0.875)",
  warm: "Warm (0.625)",
  mellow: "Mellow (0.375)",
  cold: "Cold (0.125)",
};

function BucketAnchor({ bucket, songs, onPlay }) {
  const s = BUCKET_STYLES[bucket] || BUCKET_STYLES.mellow;
  return (
    <div style={{ marginBottom: "10px" }}>
      <div style={{ fontSize: "11px", fontWeight: 600, color: s.color, marginBottom: "3px" }}>
        {BUCKET_LABELS[bucket] || bucket}
      </div>
      {songs.length === 0 ? (
        <div style={{ fontSize: "10px", color: "var(--text-muted)", padding: "2px 0" }}>
          No anchors yet
        </div>
      ) : songs.map((song) => (
        <button
          key={song.id}
          onClick={() => onPlay(song.id, song.artist, song.title)}
          style={{
            display: "block", width: "100%", textAlign: "left", padding: "3px 6px",
            fontSize: "10px", borderRadius: "3px", background: s.bg,
            border: "none", color: "var(--text-secondary)", cursor: "pointer",
            marginBottom: "2px", overflow: "hidden", textOverflow: "ellipsis",
            whiteSpace: "nowrap", transition: "background 0.12s",
          }}
          title="Click to play"
        >
          {song.artist || "?"} — {song.title || "?"}
        </button>
      ))}
    </div>
  );
}

export default BucketAnchor;
