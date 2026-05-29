import React, { useState, useEffect, useCallback } from "react";
import * as api from "../api";

function WaveformBars({ energy, isPlaying, isPlayingBar = false }) {
  return (
    <div className="flex items-end gap-0.5 h-4" style={{ opacity: isPlaying && isPlayingBar ? 1 : 0.4 }}>
      {[...Array(8)].map((_, i) => (
        <div key={i} className="w-0.5 rounded-full transition-all" style={{
          height: isPlaying && isPlayingBar ? `${10 + Math.sin(Date.now() / 200 + i * 0.8) * 40 + energy * 50}%` : `${15 + energy * 45}%`,
          backgroundColor: isPlaying && isPlayingBar ? "#10b981" : `hsl(${160 + energy * 60}, 60%, ${40 + energy * 20}%)`,
          animation: isPlaying && isPlayingBar ? `pulse 0.5s ease-in-out ${i * 0.05}s infinite alternate` : "none",
        }} />
      ))}
    </div>
  );
}

function SongCard({ song, selected, onSelect }) {
  const energyColor = song.energy > 0.6 ? "text-[#4eccc4]" : song.energy > 0.4 ? "text-yellow-400" : "text-blue-400";
  const valenceColor = song.valence > 0.6 ? "text-[#4eccc4]" : song.valence > 0.4 ? "text-yellow-400" : "text-blue-400";
  const [previewing, setPreviewing] = useState(false);

  const handlePreview = async (e) => {
    e.stopPropagation();
    try {
      await api.playSong(song.id);
      setPreviewing(true);
      setTimeout(() => setPreviewing(false), 10000);
    } catch (e) {}
  };

  return (
    <button onClick={() => onSelect(song.id)} className="relative p-3 rounded-xl border text-left transition-all duration-200"
      style={{
        borderColor: selected ? "rgba(78,204,196,0.4)" : "var(--border)",
        background: selected ? "rgba(78,204,196,0.08)" : "var(--bg-elevated)",
      }}>
      {selected && (
        <div className="absolute -top-2 -right-2 w-6 h-6 rounded-full flex items-center justify-center" style={{ background: "var(--accent)" }}>
          <svg className="w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>
        </div>
      )}
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white truncate">{song.title}</p>
          <p className="text-xs text-gray-400 truncate">{song.artist}</p>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={handlePreview} className="w-6 h-6 flex items-center justify-center rounded-full bg-gray-700 hover:bg-gray-600 text-xs transition" title="Preview">
            {previewing ? "\u25A0" : "\u25B6"}
          </button>
          <WaveformBars energy={song.energy} isPlaying={previewing} isPlayingBar={previewing} />
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs">
        <span className={energyColor}>Energy {song.energy?.toFixed(1)}</span>
        <span className="text-gray-600">|</span>
        <span className={valenceColor}>Mood {song.valence?.toFixed(1)}</span>
      </div>
    </button>
  );
}

export default function Onboarding({ onComplete, onSkip, addToast }) {
  const [songs, setSongs] = useState([]);
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.fetchOnboardingSongs()
      .then((data) => { setSongs(data.songs || []); setLoading(false); })
      .catch(() => { setLoading(false); });
  }, []);

  const toggleSong = useCallback((songId) => {
    setSelected((prev) => {
      if (prev.includes(songId)) return prev.filter((id) => id !== songId);
      if (prev.length >= 5) return prev;
      return [...prev, songId];
    });
  }, []);

  const handleSubmit = useCallback(async () => {
    if (selected.length === 0) return;
    setSubmitting(true);
    try {
      await api.completeOnboarding(selected);
      onComplete(selected);
      addToast?.("Taste seeds set!", "success");
    } catch (e) {
      addToast?.("Failed to set taste seeds", "error");
      setSubmitting(false);
    }
  }, [selected, onComplete, addToast]);

  const handleSkip = useCallback(async () => {
    try {
      await api.skipOnboarding();
      onSkip();
      addToast?.("Skipped onboarding", "success");
    } catch (e) {
      addToast?.("Failed to skip onboarding", "error");
      onSkip();
    }
  }, [onSkip, addToast]);

  if (loading) {
    return (
      <div className="fixed inset-0 bg-gray-950/95 backdrop-blur-sm flex items-center justify-center z-50">
        <div className="text-gray-500 text-lg">Loading your taste seeds...</div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-gray-950/95 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold text-white mb-2">Seed Your Taste</h2>
          <p className="text-gray-400 text-sm">Pick {selected.length}/5 songs you like to help us understand your style</p>
        </div>
        <div className="flex justify-center gap-1.5 mb-6">
          {[...Array(5)].map((_, i) => (
            <div key={i} className={`w-2 h-2 rounded-full transition-all`} style={{ background: i < selected.length ? "var(--accent)" : "var(--border)" }} />
          ))}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-8">
          {songs.map((song) => <SongCard key={song.id} song={song} selected={selected.includes(song.id)} onSelect={toggleSong} />)}
        </div>
        <div className="flex items-center justify-between">
          <button onClick={handleSkip} className="text-gray-500 text-sm hover:text-gray-300 transition">Skip for now</button>
          <button onClick={handleSubmit} disabled={selected.length === 0 || submitting} className="px-6 py-2.5 rounded-lg font-medium text-sm transition-all"
            style={{
              background: selected.length > 0 && !submitting ? "var(--accent)" : "var(--bg-elevated)",
              color: selected.length > 0 && !submitting ? "var(--text-inverse)" : "var(--text-muted)",
              opacity: selected.length > 0 && !submitting ? 1 : 0.5,
              cursor: selected.length > 0 && !submitting ? "pointer" : "not-allowed",
            }}>
            {submitting ? "Setting up..." : `Done (${selected.length}/5)`}
          </button>
        </div>
      </div>
    </div>
  );
}
