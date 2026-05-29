import React, { useState, useEffect } from "react";
import * as api from "../api";

function IngestPanel({ addToast }) {
  const [status, setStatus] = useState(null);
  const [progress, setProgress] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [running, setRunning] = useState(false);
  const [duplicates, setDuplicates] = useState([]);
  const [showDuplicates, setShowDuplicates] = useState(false);
  const [failures, setFailures] = useState([]);
  const [showFailures, setShowFailures] = useState(false);

  useEffect(() => {
    fetchStatus();
    fetchFailures();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);

    async function fetchStatus() {
      try {
        const [statusData, progressData] = await Promise.all([
          api.fetchIngestStatus(),
          api.fetchIngestProgress(),
        ]);
        setStatus(statusData);
        setProgress(progressData);
      } catch (e) { console.error("IngestPanel fetchStatus error:", e); }
    }
  }, []);

  async function fetchFailures() {
    try {
      const data = await api.fetchIngestFailures();
      setFailures(data || []);
    } catch (e) {}
  }

  async function handleRetryFailures() {
    try {
      const data = await api.retryIngestFailures();
      addToast?.("Retried " + data.count + " failed songs", "success");
      fetchFailures();
    } catch (e) {
      addToast?.("Retry failed: " + (e.message || "unknown error"), "error");
    }
  }

  async function handleScan() {
    setScanning(true);
    try {
      const data = await api.scanLibrary();
      addToast?.(`Scan complete: ${data.unique_files} unique, ${data.duplicates} duplicates`, "success");
      if (data.duplicate_details?.length > 0) {
        setDuplicates(data.duplicate_details);
        setShowDuplicates(true);
      }
    } catch (e) {
      addToast?.("Scan failed: " + (e.message || "unknown error"), "error");
    } finally {
      setScanning(false);
    }
  }

  async function handleRun() {
    setRunning(true);
    try {
      await api.runIngest();
      addToast?.("Ingest started", "success");
    } catch (e) {
      addToast?.("Ingest failed: " + (e.message || "unknown error"), "error");
      setRunning(false);
    }
  }

  if (!status) return <p className="text-gray-500 text-sm">Loading...</p>;

  const phases = ["raw", "analyzed", "described", "embedded", "clustered"];
  const total = Object.values(status).reduce((a, b) => a + b, 0);
  const isRunning = progress?.running;
  const currentPhase = progress?.phase;
  const processed = progress?.processed || 0;
  const totalToProcess = progress?.total || 0;
  const currentSong = progress?.current_song;

  return (
    <div className="bg-gray-900 rounded-xl p-3 sm:p-5 border border-gray-800">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 sm:mb-4">Library Status</h3>
      <div className="flex gap-2 mb-3 sm:mb-4">
        <button onClick={handleScan} disabled={isRunning} className="px-3 sm:px-4 py-1.5 sm:py-2 rounded-lg border border-gray-700 bg-gray-800 text-gray-300 text-xs sm:text-sm hover:bg-gray-700 transition disabled:opacity-40 disabled:cursor-not-allowed">
          {scanning ? "Scanning..." : "Scan"}
        </button>
        <button onClick={handleRun} disabled={isRunning || total === 0} className="px-3 sm:px-4 py-1.5 sm:py-2 rounded-lg bg-green-700 text-white text-xs sm:text-sm font-medium hover:bg-green-600 transition disabled:opacity-40 disabled:cursor-not-allowed">
          {isRunning ? "Running..." : "Ingest"}
        </button>
      </div>
      {isRunning && (
        <div className="mb-4 p-3 rounded-lg bg-green-900/20 border border-green-800/50">
          <div className="flex flex-col gap-1 mb-2">
            <span className="text-xs text-green-400 font-semibold uppercase">Phase: {currentPhase}</span>
            {currentSong && <span className="text-xs text-gray-400 italic">{currentSong}</span>}
            <span className="text-xs text-gray-500">{processed} / {totalToProcess}</span>
          </div>
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div className="h-full bg-gradient-to-r from-green-600 to-green-400 rounded-full transition-all duration-300" style={{ width: `${totalToProcess > 0 ? (processed / totalToProcess) * 100 : 0}%` }} />
          </div>
        </div>
      )}
      <div className="space-y-2">
        {phases.map((phase) => (
          <div key={phase} className="flex items-center gap-3">
            <span className="text-xs text-gray-500 w-20 capitalize">{phase}</span>
            <div className="flex-1 h-2 bg-gray-800 rounded-full overflow-hidden">
              <div className="h-full bg-green-600 rounded-full transition-all duration-500" style={{ width: `${total > 0 ? ((status[phase] || 0) / total) * 100 : 0}%` }} />
            </div>
            <span className="text-xs text-gray-400 w-8 text-right">{status[phase] || 0}</span>
          </div>
        ))}
      </div>
      {duplicates.length > 0 && (
        <div className="mt-4 border-t border-gray-800 pt-4">
          <button onClick={() => setShowDuplicates(!showDuplicates)} className="flex items-center gap-2 text-xs text-yellow-400 hover:text-yellow-300 transition">
            <span>{showDuplicates ? "\u25BC" : "\u25B6"}</span>
            <span>{duplicates.length} duplicate{duplicates.length !== 1 ? "s" : ""} found</span>
          </button>
          {showDuplicates && (
            <div className="mt-2 space-y-1 max-h-48 overflow-y-auto">
              {duplicates.map((dup, i) => (
                <div key={i} className="text-xs text-gray-500 px-2 py-1 rounded bg-gray-800/50">
                  <span className="text-red-400">Skipped:</span> {dup.file?.split("\\").pop() || dup.file}
                  {dup.kept && <span className="text-green-400 ml-2">Kept: {dup.kept.split("\\").pop()}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {failures.length > 0 && (
        <div className="mt-4 border-t border-gray-800 pt-4">
          <button onClick={() => setShowFailures(!showFailures)} className="flex items-center gap-2 text-xs text-red-400 hover:text-red-300 transition">
            <span>{showFailures ? "\u25BC" : "\u25B6"}</span>
            <span>{failures.length} ingest failure{failures.length !== 1 ? "s" : ""}</span>
          </button>
          {showFailures && (
            <div className="mt-2 space-y-1 max-h-48 overflow-y-auto">
              {failures.map((f) => (
                <div key={f.id} className="text-xs px-2 py-1.5 rounded bg-red-900/20 border border-red-900/40">
                  <div className="flex items-center justify-between">
                    <span className="text-gray-300 truncate">{f.artist || f.title || f.filepath?.split("\\").pop()}</span>
                    <span className="text-red-400 ml-2 shrink-0">{f.ingest_phase}</span>
                  </div>
                  {f.ingest_error && <div className="text-gray-500 mt-0.5 truncate">{f.ingest_error}</div>}
                </div>
              ))}
              <button onClick={handleRetryFailures} className="mt-2 px-3 py-1 rounded-lg text-xs bg-red-800/40 text-red-400 border border-red-800/50 hover:bg-red-800/60 transition">
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
