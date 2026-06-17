import React, { useState, useEffect, useCallback, useRef } from "react";
import * as api from "../api";

const PHASES = [
  { key: "raw", dbKey: "raw", apiPhase: "raw", label: "Scan & add", desc: "Scan music dir, add new songs, extract art" },
  { key: "analyze", dbKey: "analyzed", apiPhase: "analyze", label: "Analyze audio", desc: "Extract raw audio features (RMS, spectral, BPM)" },
  { key: "normalize", dbKey: null, apiPhase: "normalize", label: "Normalize", desc: "Normalize energy/valence across library" },
  { key: "describe", dbKey: "described", apiPhase: "describe", label: "LLM tags", desc: "Generate emotional/genre/instrument tags" },
  { key: "embed", dbKey: "embedded", apiPhase: "embed", label: "Text embeddings", desc: "Embed song metadata for similarity search" },
  { key: "audio_embed", dbKey: null, apiPhase: "audio_embed", label: "Audio embeddings", desc: "MuQ-MuLan audio embeddings (optional)" },
  { key: "cluster", dbKey: "clustered", apiPhase: "cluster", label: "Cluster", desc: "Group songs into KMeans clusters" },
  { key: "lyrics_timing", dbKey: null, apiPhase: "lyrics_timing", label: "Lyrics timing", desc: "WhisperX forced alignment for karaoke" },
];

const PHASE_PROGRESS_MAP = {
  raw: "raw",
  analyzed: "analyze",
  normalizing: "normalize",
  described: "describe",
  embedded: "embed",
  audio_embedded: "audio_embed",
  clustered: "cluster",
  lyrics_timed: "lyrics_timing",
  "reprocess:analyze": "reprocess",
  "reprocess:normalize": "reprocess",
  "reprocess:recovery": "reprocess",
  "reprocess:embed": "reprocess",
  "reprocess:cluster": "reprocess",
  "reprocess:done": "reprocess",
};

function IngestPanel({ addToast }) {
  const [phaseStatus, setPhaseStatus] = useState(null);
  const [progress, setProgress] = useState(null);
  const [runningPhase, setRunningPhase] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [runningAll, setRunningAll] = useState(false);
  const [duplicates, setDuplicates] = useState([]);
  const [showDuplicates, setShowDuplicates] = useState(false);
  const [failures, setFailures] = useState([]);
  const [showFailures, setShowFailures] = useState(false);
  const [reembedding, setReembedding] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocReembed, setReprocReembed] = useState(true);
  const [reprocRecluster, setReprocRecluster] = useState(true);
  const [showReprocessConfirm, setShowReprocessConfirm] = useState(false);
  const wasRunningRef = useRef(false);

  const total = phaseStatus ? Object.values(phaseStatus).reduce((a, b) => a + b, 0) : 0;

  const fetchData = useCallback(async () => {
    try {
      const [statusData, progressData] = await Promise.all([
        api.fetchIngestPhaseStatus(),
        api.fetchIngestProgress(),
      ]);
      setPhaseStatus(statusData?.by_phase || null);
      setProgress(progressData);

      if (progressData?.running && progressData?.phase) {
        const phaseKey = PHASE_PROGRESS_MAP[progressData.phase] || null;
        setRunningPhase(phaseKey);
        wasRunningRef.current = true;
      } else {
        setRunningPhase(null);
        setRunningAll(false);
        if (wasRunningRef.current && reprocessing) {
          setReprocessing(false);
          addToast?.("Reprocess complete!", "success");
        }
        wasRunningRef.current = false;
      }
    } catch (e) {}
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, runningPhase ? 1000 : 5000);
    return () => clearInterval(interval);
  }, [fetchData, runningPhase]);

  useEffect(() => {
    api.fetchIngestFailures().then(setFailures).catch(() => {});
  }, []);

  async function handleScan() {
    setScanning(true);
    try {
      const data = await api.scanLibrary();
      addToast?.(`Scan complete: ${data.unique_files} unique, ${data.duplicates} duplicates`, "success");
      if (data.duplicate_details?.length > 0) {
        setDuplicates(data.duplicate_details);
        setShowDuplicates(true);
      }
      fetchData();
    } catch (e) {
      addToast?.("Scan failed: " + (e.message || "unknown error"), "error");
    } finally {
      setScanning(false);
    }
  }

  async function handleRunAll() {
    setRunningAll(true);
    try {
      await api.runIngest();
      addToast?.("Full ingest started", "success");
    } catch (e) {
      addToast?.("Ingest failed: " + (e.message || "unknown error"), "error");
      setRunningAll(false);
    }
  }

  async function handleReembed() {
    setReembedding(true);
    try {
      const data = await api.reembedAll();
      if (data.error) {
        addToast?.("Re-embed failed: " + data.error, "error");
      } else {
        addToast?.(`Re-embed complete: ${data.success}/${data.total} songs`, "success");
        fetchData();
      }
    } catch (e) {
      addToast?.("Re-embed failed: " + (e.message || "unknown error"), "error");
    } finally {
      setReembedding(false);
    }
  }

  async function handleReprocess() {
    setReprocessing(true);
    setShowReprocessConfirm(false);
    wasRunningRef.current = false;
    try {
      await api.reprocessEnergy(reprocReembed, reprocRecluster);
      addToast?.("Reprocess started (∼30-60 min) — progress shown below", "success");
      fetchData();
    } catch (e) {
      addToast?.("Reprocess failed: " + (e.message || "unknown error"), "error");
      setReprocessing(false);
    }
  }

  async function handleRunPhase(apiPhase) {
    try {
      const data = await api.runIngestPhase(apiPhase);
      if (data.status === "no_work") {
        addToast?.(`No songs ready for ${apiPhase}`, "info");
        return;
      }
      if (data.status === "already_running") {
        addToast?.("Another phase is already running", "info");
        return;
      }
      addToast?.(`Phase started: ${apiPhase}`, "success");
    } catch (e) {
      addToast?.(`Failed to start ${apiPhase}: ` + (e.message || "unknown error"), "error");
    }
  }

  const isRunning = runningPhase || runningAll || reembedding || reprocessing;

  return (
    <div>
      {/* Action buttons */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "10px", flexWrap: "wrap" }}>
        <button onClick={handleScan} disabled={isRunning}
          style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)", cursor: isRunning ? "default" : "pointer", opacity: isRunning ? 0.4 : 1, display: "flex", alignItems: "center", gap: "4px" }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
          {scanning ? "Scanning..." : "Scan"}
        </button>
        <button onClick={handleRunAll} disabled={isRunning || total === 0}
          style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: isRunning || total === 0 ? "default" : "pointer", opacity: isRunning || total === 0 ? 0.4 : 1, display: "flex", alignItems: "center", gap: "4px", fontWeight: 500 }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          {runningAll ? "Ingesting..." : "Ingest all"}
        </button>
        <button onClick={handleReembed} disabled={isRunning || total === 0}
          style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)", cursor: isRunning || total === 0 ? "default" : "pointer", opacity: isRunning || total === 0 ? 0.4 : 1, display: "flex", alignItems: "center", gap: "4px" }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="1 4 1 10 7 10"/><polyline points="23 20 23 14 17 14"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
          {reembedding ? "Re-embedding..." : "Re-embed all"}
        </button>
        <button onClick={() => setShowReprocessConfirm(true)} disabled={isRunning}
          style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: reprocessing ? "var(--accent-muted)" : "var(--bg-elevated)", border: `1px solid ${reprocessing ? "var(--accent)" : "var(--border)"}`, color: reprocessing ? "var(--accent)" : "var(--text-secondary)", cursor: isRunning ? "default" : "pointer", opacity: isRunning ? 0.4 : 1, display: "flex", alignItems: "center", gap: "4px", fontWeight: reprocessing ? 500 : 400 }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg>
          {reprocessing ? "Reprocessing..." : "Reprocess Energy"}
        </button>
      </div>

      {/* Reprocess confirmation dialog */}
      {showReprocessConfirm && (
        <div style={{ marginBottom: "10px", padding: "10px 14px", borderRadius: "8px", background: "rgba(226,75,74,0.08)", border: "1px solid rgba(226,75,74,0.25)" }}>
          <div style={{ fontSize: "12px", color: "var(--text)", marginBottom: "8px", lineHeight: 1.4 }}>
            <b>Reprocess Energy for all songs?</b>
            <br />
            <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>
              This will re-analyze all 1809 songs to extract improved energy features (∼30–60 min).
              LLM tagging and WhisperX are not run.
            </span>
          </div>
          <div style={{ display: "flex", gap: "12px", marginBottom: "10px", fontSize: "12px" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "4px", cursor: "pointer", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={reprocReembed} onChange={(e) => setReprocReembed(e.target.checked)} />
              Re-embed
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "4px", cursor: "pointer", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={reprocRecluster} onChange={(e) => setReprocRecluster(e.target.checked)} />
              Re-cluster
            </label>
          </div>
          <div style={{ display: "flex", gap: "6px" }}>
            <button onClick={handleReprocess}
              style={{ padding: "5px 14px", borderRadius: "5px", fontSize: "11px", background: "var(--danger)", border: "none", color: "#fff", cursor: "pointer", fontWeight: 500 }}>
              Start Reprocess
            </button>
            <button onClick={() => setShowReprocessConfirm(false)}
              style={{ padding: "5px 14px", borderRadius: "5px", fontSize: "11px", background: "transparent", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer" }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Phase rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
        {PHASES.map((phase) => {
          const count = phase.dbKey ? (phaseStatus?.[phase.dbKey] || 0) : null;
          const isPhaseRunning = runningPhase === phase.key;
          const progressPct = isPhaseRunning && progress?.total > 0
            ? Math.round((progress.processed / progress.total) * 100)
            : 0;

          return (
            <div key={phase.key} style={{
              display: "flex", alignItems: "center", gap: "8px",
              padding: "6px 8px", borderRadius: "4px",
              background: isPhaseRunning ? "rgba(93,173,226,0.06)" : "transparent",
              transition: "background 0.15s",
            }}>
              {/* Phase label */}
              <div style={{ minWidth: "110px", flexShrink: 0 }}>
                <div style={{ fontSize: "11px", color: isPhaseRunning ? "var(--accent)" : "var(--text-secondary)", fontWeight: isPhaseRunning ? 500 : 400 }}>
                  {phase.label}
                </div>
                <div style={{ fontSize: "9px", color: "var(--text-muted)", marginTop: "1px" }}>
                  {phase.desc}
                </div>
              </div>

              {/* Count or progress */}
              <div style={{ minWidth: "70px", flexShrink: 0, textAlign: "right" }}>
                {isPhaseRunning && progress ? (
                  <span style={{ fontSize: "10px", color: "var(--accent)" }}>
                    {progress.processed}/{progress.total}
                  </span>
                ) : count !== null ? (
                  <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>
                    {count}{phase.key === "cluster" && total > 0 ? ` / ${total}` : ""}
                  </span>
                ) : (
                  <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>—</span>
                )}
              </div>

              {/* Progress bar */}
              <div style={{ flex: 1, height: "3px", borderRadius: "2px", background: "var(--border-subtle)", overflow: "hidden", minWidth: "40px" }}>
                {isPhaseRunning && (
                  <div style={{
                    height: "100%", borderRadius: "2px",
                    width: `${progressPct}%`, background: "var(--accent)",
                    transition: "width 0.3s ease",
                  }} />
                )}
              </div>

              {/* Run button */}
              <button
                onClick={() => handleRunPhase(phase.apiPhase)}
                disabled={isRunning}
                style={{
                  fontSize: "9px", padding: "2px 8px", borderRadius: "3px",
                  background: isPhaseRunning ? "var(--accent-muted)" : "var(--bg-elevated)",
                  border: `1px solid ${isPhaseRunning ? "var(--accent)" : "var(--border)"}`,
                  color: isPhaseRunning ? "var(--accent)" : "var(--text-muted)",
                  cursor: isRunning ? "default" : "pointer",
                  opacity: isRunning && !isPhaseRunning ? 0.35 : 1,
                  flexShrink: 0, transition: "all 0.15s",
                }}>
                {isPhaseRunning ? "Running..." : "Run"}
              </button>
            </div>
          );
        })}
      </div>

      {/* Current song indicator */}
      {runningPhase && progress?.current_song && (
        <div style={{ marginTop: "6px", fontSize: "10px", color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {progress.current_song}
        </div>
      )}

      {/* Duplicates */}
      {duplicates.length > 0 && (
        <div style={{ marginTop: "8px" }}>
          <button onClick={() => setShowDuplicates(!showDuplicates)}
            style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "10px", color: "var(--warning)", background: "none", border: "none", cursor: "pointer", padding: "2px 0" }}>
            <span>{showDuplicates ? "▼" : "▶"}</span>
            {duplicates.length} duplicate{duplicates.length !== 1 ? "s" : ""} found
          </button>
          {showDuplicates && (
            <div style={{ marginTop: "4px", maxHeight: "120px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "2px" }}>
              {duplicates.map((dup, i) => (
                <div key={i} style={{ fontSize: "10px", color: "var(--text-muted)", padding: "3px 6px", borderRadius: "3px", background: "var(--bg-elevated)" }}>
                  <span style={{ color: "var(--danger)" }}>Skipped:</span> {dup.file?.split("\\").pop() || dup.file}
                  {dup.kept && <span style={{ color: "var(--accent)", marginLeft: "6px" }}>Kept: {dup.kept.split("\\").pop()}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Failures */}
      {failures.length > 0 && (
        <div style={{ marginTop: "8px" }}>
          <button onClick={() => setShowFailures(!showFailures)}
            style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "10px", color: "var(--danger)", background: "none", border: "none", cursor: "pointer", padding: "2px 0" }}>
            <span>{showFailures ? "▼" : "▶"}</span>
            {failures.length} ingest failure{failures.length !== 1 ? "s" : ""}
          </button>
          {showFailures && (
            <div style={{ marginTop: "4px", maxHeight: "120px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "2px" }}>
              {failures.map((f) => (
                <div key={f.id} style={{ fontSize: "10px", padding: "4px 6px", borderRadius: "3px", background: "rgba(226,75,74,0.1)", border: "1px solid rgba(226,75,74,0.2)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.artist || f.title || f.filepath?.split("\\").pop()}</span>
                    <span style={{ color: "var(--danger)", marginLeft: "6px", flexShrink: 0 }}>{f.ingest_phase}</span>
                  </div>
                  {f.ingest_error && <div style={{ color: "var(--text-muted)", marginTop: "1px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.ingest_error}</div>}
                </div>
              ))}
              <button onClick={async () => {
                try {
                  const data = await api.retryIngestFailures();
                  addToast?.("Retried " + data.count + " failed songs", "success");
                  const updated = await api.fetchIngestFailures();
                  setFailures(updated || []);
                  fetchData();
                } catch (e) { addToast?.("Retry failed", "error"); }
              }}
                style={{ marginTop: "4px", padding: "4px 8px", borderRadius: "4px", fontSize: "10px", background: "rgba(226,75,74,0.15)", border: "1px solid rgba(226,75,74,0.3)", color: "var(--danger)", cursor: "pointer" }}>
                Retry all failures
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default IngestPanel;
