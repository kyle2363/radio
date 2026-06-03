import React, { useState, useEffect, useCallback } from "react";
import * as api from "../api";

const PHASES = [
  { key: "raw", dbKey: "raw", label: "Raw" },
  { key: "analyze", dbKey: "analyzed", label: "Analyzed" },
  { key: "describe", dbKey: "described", label: "Described" },
  { key: "cluster", dbKey: "clustered", label: "Clustered" },
];

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

  const total = phaseStatus ? Object.values(phaseStatus).reduce((a, b) => a + b, 0) : 0;

  const fetchData = useCallback(async () => {
    try {
      const [statusData, progressData] = await Promise.all([
        api.fetchIngestPhaseStatus(),
        api.fetchIngestProgress(),
      ]);
      setPhaseStatus(statusData?.by_phase || null);
      setProgress({ ...progressData, total });

      if (progressData?.running && progressData?.phase) {
        const phaseKey = PHASES.find(p => progressData.phase.includes(p.key) || p.key === progressData.phase)?.key;
        setRunningPhase(phaseKey || null);
      } else {
        setRunningPhase(null);
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

  const isRunning = runningPhase || runningAll;
  const clustered = phaseStatus?.clustered || 0;

  return (
    <div>
      {/* Action buttons */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "10px" }}>
        <button onClick={handleScan} disabled={isRunning}
          style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)", cursor: isRunning ? "default" : "pointer", opacity: isRunning ? 0.4 : 1, display: "flex", alignItems: "center", gap: "4px" }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
          {scanning ? "Scanning..." : "Scan"}
        </button>
        <button onClick={handleRunAll} disabled={isRunning || total === 0}
          style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: isRunning || total === 0 ? "default" : "pointer", opacity: isRunning || total === 0 ? 0.4 : 1, display: "flex", alignItems: "center", gap: "4px", fontWeight: 500 }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          {runningAll ? "Ingesting..." : "Ingest"}
        </button>
      </div>

      {/* Phase progress bars */}
      <div className="prog-sec">
        {PHASES.map((phase) => {
          const count = phaseStatus?.[phase.dbKey] || 0;
          const isClustered = phase.key === "cluster";
          const fill = isClustered && total > 0 ? (clustered / total) * 100 : 0;
          const color = isClustered && fill >= 100 ? "var(--accent)" : "var(--border)";
          const labelColor = isClustered && fill >= 100 ? "var(--accent)" : "var(--text-muted)";
          const countColor = isClustered && fill >= 100 ? "var(--accent)" : "var(--text-muted)";

          return (
            <div key={phase.key} className="prog-item">
              <div className="prog-label">
                <span style={{ fontSize: "10px", color: labelColor }}>{phase.label}</span>
                {isClustered ? (
                  <span style={{ fontSize: "10px", color: "var(--accent)", fontWeight: 600 }}>
                    {count} / {total}
                  </span>
                ) : (
                  <span style={{ fontSize: "10px", color: countColor }}>{count}</span>
                )}
              </div>
              <div className="prog-track">
                <div className="prog-fill-bar" style={{ width: `${fill}%`, background: color }} />
              </div>
            </div>
          );
        })}
      </div>

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
