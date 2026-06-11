import React, { useState, useEffect, useCallback } from "react";
import { Search, Trash2, Music, ChevronDown, RefreshCw, CheckCircle, AlertTriangle, FolderOpen } from "lucide-react";
import * as api from "../api";

function LibraryVerify({ addToast }) {
  const [orphaned, setOrphaned] = useState([]);
  const [unregistered, setUnregistered] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [matchedResults, setMatchedResults] = useState(null);
  const [autoMatching, setAutoMatching] = useState(false);
  const [expandedSection, setExpandedSection] = useState("orphaned");
  const [manualMatchId, setManualMatchId] = useState(null);

  const fetchVerify = useCallback(async () => {
    setLoading(true);
    setMatchedResults(null);
    try {
      const data = await api.verifyLibrary();
      setOrphaned(data.orphaned || []);
      setUnregistered(data.unregistered || []);
      setStats(data.stats || {});
    } catch (err) {
      addToast?.("Failed to verify library: " + (err.message || "unknown"), "error");
    }
    setLoading(false);
  }, [addToast]);

  useEffect(() => {
    fetchVerify();
  }, [fetchVerify]);

  const handleAutoMatch = useCallback(async () => {
    setAutoMatching(true);
    try {
      const data = await api.autoMatchFiles();
      setMatchedResults(data);
      addToast?.(`Auto-matched ${data.match_count} song(s)`, "success");
      // Refresh orphaned/unregistered lists
      await fetchVerify();
      if (data.match_count > 0) {
        setExpandedSection("matched");
      }
    } catch (err) {
      addToast?.("Auto-match failed: " + (err.message || "unknown"), "error");
    }
    setAutoMatching(false);
  }, [fetchVerify, addToast]);

  const handleDelete = useCallback(async (songId, songTitle) => {
    try {
      await api.deleteSong(songId);
      addToast?.(`Deleted: ${songTitle}`, "success");
      fetchVerify();
    } catch (err) {
      addToast?.("Failed to delete: " + (err.message || "unknown"), "error");
    }
  }, [fetchVerify, addToast]);

  const handleAddUnregistered = useCallback(async (filepath) => {
    try {
      const data = await api.addUnregisteredSong(filepath);
      addToast?.(`Added: ${data.title || data.artist || "Song"}`, "success");
      fetchVerify();
    } catch (err) {
      addToast?.("Failed to add: " + (err.message || "unknown"), "error");
    }
  }, [fetchVerify, addToast]);

  const handleConfirmMatch = useCallback(async (orphanId, filepath, title) => {
    try {
      await api.confirmMatch(orphanId, filepath);
      addToast?.(`Matched: ${title}`, "success");
      setManualMatchId(null);
      fetchVerify();
    } catch (err) {
      addToast?.("Match failed: " + (err.message || "unknown"), "error");
    }
  }, [fetchVerify, addToast]);

  const handleDeleteAllOrphaned = useCallback(async () => {
    try {
      const data = await api.deleteAllOrphaned();
      addToast?.(`Deleted ${data.deleted} orphaned song(s)`, "success");
      fetchVerify();
    } catch (err) {
      addToast?.("Failed to delete orphaned: " + (err.message || "unknown"), "error");
    }
  }, [fetchVerify, addToast]);

  const formatSize = (bytes) => {
    if (!bytes) return "";
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatPath = (fp) => {
    if (!fp) return "";
    // Show just the relative part after audio_files/
    const match = fp.match(/audio_files[\\\/](.+)/);
    return match ? `audio_files/${match[1]}` : fp.split(/[/\\]/).slice(-2).join("/");
  };

  const hasIssues = orphaned.length > 0 || unregistered.length > 0;

  return (
    <div>
      {/* Summary bar */}
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginBottom: "12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", fontSize: "11px", color: "var(--text-muted)" }}>
          {stats && (
            <>
              <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                <span style={{ color: "var(--text-secondary)" }}>{stats.total_db_songs || 0}</span> in DB
              </span>
              <span style={{ opacity: 0.4 }}>|</span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                <span style={{ color: "var(--text-secondary)" }}>{stats.total_disk_files || 0}</span> on disk
              </span>
            </>
          )}
        </div>
        <div style={{ flex: 1 }} />
        <button
          onClick={fetchVerify}
          disabled={loading}
          style={{
            fontSize: "10px", padding: "4px 10px", borderRadius: "5px",
            background: "var(--bg-elevated)", border: "1px solid var(--border)",
            color: "var(--text-muted)", cursor: "pointer", display: "inline-flex",
            alignItems: "center", gap: "4px", opacity: loading ? 0.5 : 1,
          }}
        >
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {/* Stats badges */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "14px", flexWrap: "wrap" }}>
        {orphaned.length > 0 && (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: "5px",
            padding: "4px 10px", borderRadius: "5px", fontSize: "11px",
            background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.2)",
            color: "#ef4444",
          }}>
            <AlertTriangle size={11} />
            {orphaned.length} orphaned DB {orphaned.length === 1 ? "entry" : "entries"}
          </div>
        )}
        {unregistered.length > 0 && (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: "5px",
            padding: "4px 10px", borderRadius: "5px", fontSize: "11px",
            background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.2)",
            color: "#3b82f6",
          }}>
            <FolderOpen size={11} />
            {unregistered.length} unregistered {unregistered.length === 1 ? "file" : "files"}
          </div>
        )}
        {hasIssues && (
          <button
            onClick={handleAutoMatch}
            disabled={autoMatching}
            style={{
              fontSize: "10px", padding: "4px 12px", borderRadius: "5px",
              background: "var(--accent-muted)", border: "1px solid var(--border)",
              color: "var(--accent)", cursor: "pointer", fontWeight: 500,
              opacity: autoMatching ? 0.5 : 1,
            }}
          >
            {autoMatching ? "Matching..." : "Auto-Match"}
          </button>
        )}
        {orphaned.length > 0 && (
          <button
            onClick={handleDeleteAllOrphaned}
            style={{
              fontSize: "10px", padding: "4px 10px", borderRadius: "5px",
              background: "#1e0808", border: "1px solid #3a1010",
              color: "var(--danger)", cursor: "pointer",
            }}
          >
            Delete all orphaned
          </button>
        )}
        {!hasIssues && !loading && (
          <div style={{
            fontSize: "11px", color: "var(--text-muted)", padding: "4px 10px",
            display: "flex", alignItems: "center", gap: "5px",
          }}>
            <CheckCircle size={12} style={{ color: "#4ade80" }} />
            All files accounted for
          </div>
        )}
      </div>

      {/* Matched results */}
      {matchedResults && matchedResults.matched && matchedResults.matched.length > 0 && (
        <div style={{ marginBottom: "14px" }}>
          <div
            onClick={() => setExpandedSection(expandedSection === "matched" ? "" : "matched")}
            style={{
              display: "flex", alignItems: "center", gap: "6px", cursor: "pointer",
              fontSize: "11px", fontWeight: 500, color: "var(--text-primary)", marginBottom: "8px",
            }}
          >
            <ChevronDown size={12} style={{ transform: expandedSection === "matched" ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
            <CheckCircle size={12} style={{ color: "#4ade80" }} />
            Matched ({matchedResults.match_count})
          </div>
          {expandedSection === "matched" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "4px", paddingLeft: "18px" }}>
              {matchedResults.matched.map((m, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: "8px",
                  padding: "6px 10px", borderRadius: "5px",
                  background: "rgba(74,222,128,0.06)", border: "1px solid rgba(74,222,128,0.15)",
                  fontSize: "11px",
                }}>
                  <CheckCircle size={11} style={{ color: "#4ade80", flexShrink: 0 }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <span style={{ color: "var(--text-primary)" }}>{m.artist} {m.artist && m.title ? "- " : ""}{m.title || "Unknown"}</span>
                    <span style={{ color: "var(--text-muted)", marginLeft: "6px" }}>→</span>
                    <span style={{ color: "var(--text-secondary)", marginLeft: "6px" }}>{formatPath(m.new_filepath)}</span>
                  </div>
                  <span style={{ fontSize: "9px", color: "var(--text-muted)", flexShrink: 0 }}>{Math.round(m.score * 100)}% match</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Orphaned songs */}
      {orphaned.length > 0 && (
        <div style={{ marginBottom: "14px" }}>
          <div
            onClick={() => setExpandedSection(expandedSection === "orphaned" ? "" : "orphaned")}
            style={{
              display: "flex", alignItems: "center", gap: "6px", cursor: "pointer",
              fontSize: "11px", fontWeight: 500, color: "var(--text-primary)", marginBottom: "8px",
            }}
          >
            <ChevronDown size={12} style={{ transform: expandedSection === "orphaned" ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
            <AlertTriangle size={12} style={{ color: "#ef4444" }} />
            Orphaned DB entries ({orphaned.length})
          </div>
          {expandedSection === "orphaned" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "4px", paddingLeft: "18px" }}>
              {orphaned.map((song) => (
                <div key={song.id} style={{
                  display: "flex", alignItems: "center", gap: "8px",
                  padding: "6px 10px", borderRadius: "5px",
                  background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
                  fontSize: "11px",
                }}>
                  <Music size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ color: "var(--text-primary)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {song.artist || "Unknown Artist"} {song.artist && song.title ? "- " : ""}{song.title || "Unknown Title"}
                    </div>
                    <div style={{ color: "var(--text-muted)", fontSize: "9px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: "1px" }}>
                      {formatPath(song.filepath)}
                      {song.play_count > 0 && <span style={{ marginLeft: "6px", opacity: 0.7 }}>({song.play_count} plays)</span>}
                    </div>
                  </div>
                  {/* Manual match dropdown */}
                  <div style={{ position: "relative", flexShrink: 0 }}>
                    {manualMatchId === song.id ? (
                      <div style={{
                        position: "absolute", top: "100%", right: 0, marginTop: "4px", zIndex: 30,
                        background: "var(--bg-surface)", border: "1px solid var(--border)",
                        borderRadius: "6px", padding: "6px", minWidth: "220px",
                        boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
                      }}>
                        <div style={{ fontSize: "10px", color: "var(--text-muted)", marginBottom: "6px" }}>Match to file:</div>
                        {unregistered.length === 0 ? (
                          <div style={{ fontSize: "10px", color: "var(--text-muted)", padding: "4px 0" }}>No unregistered files available</div>
                        ) : (
                          <div style={{ maxHeight: "160px", overflowY: "auto" }}>
                            {unregistered.map((f, i) => (
                              <div
                                key={i}
                                onClick={() => handleConfirmMatch(song.id, f.filepath, song.title || f.filename)}
                                style={{
                                  display: "flex", alignItems: "center", gap: "6px",
                                  padding: "4px 6px", borderRadius: "4px", cursor: "pointer",
                                  fontSize: "10px", color: "var(--text-secondary)",
                                }}
                                onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg-elevated)"}
                                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                              >
                                <FolderOpen size={10} style={{ flexShrink: 0, color: "#3b82f6" }} />
                                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.filename}</span>
                              </div>
                            ))}
                          </div>
                        )}
                        <div
                          onClick={() => setManualMatchId(null)}
                          style={{ fontSize: "9px", color: "var(--text-muted)", cursor: "pointer", marginTop: "4px", textAlign: "center" }}
                        >
                          Cancel
                        </div>
                      </div>
                    ) : null}
                    <div
                      onClick={() => setManualMatchId(manualMatchId === song.id ? null : song.id)}
                      style={{
                        fontSize: "9px", padding: "3px 8px", borderRadius: "4px",
                        background: "var(--bg-elevated)", border: "1px solid var(--border)",
                        color: "var(--text-muted)", cursor: "pointer",
                        display: "inline-flex", alignItems: "center", gap: "3px",
                      }}
                    >
                      Match... <ChevronDown size={9} />
                    </div>
                  </div>
                  <div
                    onClick={() => handleDelete(song.id, song.title || song.artist || "Song")}
                    style={{
                      fontSize: "9px", padding: "3px 8px", borderRadius: "4px",
                      background: "#1e0808", border: "1px solid #3a1010",
                      color: "var(--danger)", cursor: "pointer",
                      display: "inline-flex", alignItems: "center", gap: "3px", flexShrink: 0,
                    }}
                  >
                    <Trash2 size={9} /> Delete
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Unregistered files */}
      {unregistered.length > 0 && (
        <div style={{ marginBottom: "14px" }}>
          <div
            onClick={() => setExpandedSection(expandedSection === "unregistered" ? "" : "unregistered")}
            style={{
              display: "flex", alignItems: "center", gap: "6px", cursor: "pointer",
              fontSize: "11px", fontWeight: 500, color: "var(--text-primary)", marginBottom: "8px",
            }}
          >
            <ChevronDown size={12} style={{ transform: expandedSection === "unregistered" ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
            <FolderOpen size={12} style={{ color: "#3b82f6" }} />
            Unregistered files ({unregistered.length})
          </div>
          {expandedSection === "unregistered" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "4px", paddingLeft: "18px" }}>
              {unregistered.map((f, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: "8px",
                  padding: "6px 10px", borderRadius: "5px",
                  background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
                  fontSize: "11px",
                }}>
                  <FolderOpen size={11} style={{ color: "#3b82f6", flexShrink: 0 }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ color: "var(--text-primary)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {f.filename}
                    </div>
                    <div style={{ color: "var(--text-muted)", fontSize: "9px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: "1px" }}>
                      {formatPath(f.filepath)}
                      {f.size > 0 && <span style={{ marginLeft: "6px", opacity: 0.7 }}>{formatSize(f.size)}</span>}
                    </div>
                  </div>
                  <div
                    onClick={() => handleAddUnregistered(f.filepath)}
                    style={{
                      fontSize: "9px", padding: "3px 8px", borderRadius: "4px",
                      background: "var(--accent-muted)", border: "1px solid var(--border)",
                      color: "var(--accent)", cursor: "pointer", flexShrink: 0,
                      display: "inline-flex", alignItems: "center", gap: "3px",
                    }}
                  >
                    <Music size={9} /> Add to library
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div style={{ fontSize: "11px", color: "var(--text-muted)", padding: "8px 0" }}>
          Scanning library...
        </div>
      )}
    </div>
  );
}

export default LibraryVerify;
