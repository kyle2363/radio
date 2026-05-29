import React, { useEffect, useState, useCallback } from "react";
import useRadioState from "./hooks/useRadioState";
import useTransition from "./hooks/useTransition";
import * as api from "./api";
import { Radio, Loader2, Mic } from "lucide-react";

import NowPlaying from "./components/NowPlaying";
import PlayerControls from "./components/PlayerControls";
import MoodButtons from "./components/MoodButtons";
import CommandBar from "./components/CommandBar";
import QueuePreview from "./components/QueuePreview";
import VibeLockBadge from "./components/VibeLockBadge";
import SkipStreakIndicator from "./components/SkipStreakIndicator";
import ArchetypeBadge from "./components/ArchetypeBadge";
import IngestPanel from "./components/IngestPanel";
import ExplorationSlider from "./components/ExplorationSlider";
import SettingsModal from "./components/SettingsModal";
import VolumeControl from "./components/VolumeControl";
import Onboarding from "./components/Onboarding";
import SongList from "./components/SongList";
import ToastContainer from "./components/Toast";
import SessionHistory from "./components/SessionHistory";
import ListeningStats from "./components/ListeningStats";
import DailyReport from "./components/DailyReport";
import MoodTimeline from "./components/MoodTimeline";
import ShortcutsModal from "./components/ShortcutsModal";

function App() {
  const s = useRadioState();
  const [libraryTab, setLibraryTab] = useState("library");
  const libraryTrans = useTransition(s.showLibrary, 250);
  const settingsTrans = useTransition(s.showSettings, 200);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [commandExpanded, setCommandExpanded] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable) return;
      switch (e.key) {
        case " ": e.preventDefault(); s.isPlaying ? s.handlePause() : s.handlePlay(); break;
        case "n": case "N": s.handleNext(); break;
        case "h": case "H": s.handleHeart(); break;
        case "d": case "D": s.handleThumbsDown(); break;
        case "r": case "R": s.handleReplay(); break;
        case "v": case "V": s.handleVibeLockToggle(); break;
        case "ArrowUp": e.preventDefault(); s.handleVolumeChange(Math.min(100, s.volume + 5)); break;
        case "ArrowDown": e.preventDefault(); s.handleVolumeChange(Math.max(0, s.volume - 5)); break;
        case "ArrowLeft": e.preventDefault(); s.handleSeek(Math.max(0, s.positionMs - 10000)); break;
        case "ArrowRight": e.preventDefault(); s.handleSeek(Math.min(s.durationMs, s.positionMs + 10000)); break;
        case "m": case "M": {
          const moods = ["chill", "neutral", "energetic", "focus", "melancholy", "discovery"];
          s.handleMoodSet(moods[(moods.indexOf(s.mood) + 1) % moods.length]);
          break;
        }
        case "Escape":
          if (showShortcuts) setShowShortcuts(false);
          else if (s.showLibrary) s.setShowLibrary(false);
          else if (s.showSettings) s.setShowSettings(false);
          break;
        case "?":
          setShowShortcuts((v) => !v);
          break;
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [s]);

  const handlePromoteSong = useCallback(async (songId) => {
    const order = s.queue.map((item) => item.song_id).filter((id) => id !== null);
    const idx = order.indexOf(songId);
    if (idx <= 0) return;
    order.splice(idx, 1);
    order.unshift(songId);
    try {
      await api.reorderQueue(order);
    } catch (e) {
      s.addToast("Failed to reorder queue", "error");
    }
  }, [s.queue, s.addToast]);

  if (s.loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-500 text-lg">Loading...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="px-5 py-3 sticky top-0 z-10" style={{ borderBottom: "1px solid var(--border-subtle)", background: "color-mix(in srgb, var(--bg-page) 85%, transparent)", backdropFilter: "blur(12px)" }}>
        <div className="max-w-4xl mx-auto flex items-center gap-3">
          <h1 className="text-lg font-bold tracking-tight" style={{ color: "var(--text-primary)" }}>Radio</h1>
          <span className="text-[11px] font-semibold px-3 py-1 rounded-full"
            style={{ background: "rgba(72,187,120,0.12)", color: "#4ade80", border: "1px solid rgba(72,187,120,0.2)" }}>
            {s.currentMode === "radio" ? "Radio" : "Normal"}
          </span>

          <div className="flex items-center gap-2 ml-2">
            {s.currentMode === "radio" && <ArchetypeBadge archetype={s.archetype} />}
            {s.currentMode === "radio" && <VibeLockBadge vibeLock={s.vibeLock} currentSong={s.currentSong} />}
            {s.currentMode === "radio" && <SkipStreakIndicator skipStreak={s.skipStreak} />}
            {s.currentMode === "radio" && (
              <>
                {/* Talk show generating spinner */}
                {s.talkGenerating && (
                  <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
                    style={{
                      background: "rgba(168,85,247,0.1)",
                      border: "1px solid rgba(168,85,247,0.25)",
                      color: "#c084fc",
                    }}>
                    <Loader2 size={10} className="animate-spin" />
                    Talk
                  </span>
                )}
                {/* Talk segment playing indicator */}
                {s.talkSegmentActive && (
                  <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
                    style={{
                      background: "rgba(96,165,250,0.1)",
                      border: "1px solid rgba(96,165,250,0.25)",
                      color: "#60a5fa",
                    }}>
                    <Mic size={10} />
                    {s.talkSegmentActive.segment_type === "caller" ? "Caller"
                      : s.talkSegmentActive.segment_type === "dj_monologue" ? "Monologue"
                      : "Guest"}
                  </span>
                )}
                {/* DJ badge: 3 states */}
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
                  style={{
                    background: s.talkSegmentActive
                      ? "rgba(96,165,250,0.1)"
                      : s.ttsReady
                        ? "rgba(72,187,120,0.1)"
                        : "rgba(255,255,255,0.04)",
                    border: `1px solid ${s.talkSegmentActive
                      ? "rgba(96,165,250,0.25)"
                      : s.ttsReady
                        ? "rgba(72,187,120,0.25)"
                        : "rgba(255,255,255,0.08)"}`,
                    color: s.talkSegmentActive
                      ? "#60a5fa"
                      : s.ttsReady
                        ? "#4ade80"
                        : "rgba(255,255,255,0.3)",
                  }}>
                  <span style={{
                    display: "inline-block",
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: s.talkSegmentActive
                      ? "#60a5fa"
                      : s.ttsReady
                        ? "#4ade80"
                        : "rgba(255,255,255,0.15)",
                    boxShadow: s.talkSegmentActive
                      ? "0 0 6px rgba(96,165,250,0.5)"
                      : s.ttsReady
                        ? "0 0 6px rgba(72,187,120,0.5)"
                        : "none",
                    animation: s.talkSegmentActive ? "pulse 1.5s ease-in-out infinite" : "none",
                  }} />
                  DJ
                </span>
              </>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button onClick={s.toggleTheme} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center gap-1.5 btn-muted"
              title={`Switch to ${s.theme === "dark" ? "light" : "dark"} theme`}>
              {s.theme === "dark" ? "\u2600" : "\u263E"}
            </button>
            <button onClick={() => s.setShowLibrary(true)} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center gap-1.5 btn-muted">
              Library
            </button>
            <button onClick={() => setShowShortcuts(true)} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center btn-muted"
              title="Keyboard shortcuts (?)">?</button>
            <button onClick={() => s.setShowSettings(true)} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center gap-1.5 btn-muted">
              Settings
            </button>
          </div>
        </div>
      </header>

      <main className={`max-w-6xl mx-auto px-6 py-8 flex flex-col lg:flex-row gap-6`} style={{ "--section-gap": s.sectionSpacing === "6" ? "1.5rem" : s.sectionSpacing === "8" ? "2rem" : s.sectionSpacing === "12" ? "3rem" : "2.5rem" }}>
        {/* Left column: player + controls */}
        <div className="flex-1 min-w-0 flex flex-col" style={{ gap: "var(--section-gap)" }}>
          <section><NowPlaying key={s.currentSong?.id || "empty"} song={s.currentSong} isPlaying={s.isPlaying} whyReason={s.whyReason} queue={s.queue} audioRms={s.audioRms} talkSegmentActive={s.talkSegmentActive} positionMs={s.positionMs} durationMs={s.durationMs} /></section>
          <section><PlayerControls isPlaying={s.isPlaying} onPlay={s.handlePlay} onPause={s.handlePause} onNext={s.handleNext} onHeart={s.handleHeart} onThumbsDown={s.handleThumbsDown} onReplay={s.handleReplay} onClearQueue={s.handleClearQueue} positionMs={s.positionMs} durationMs={s.durationMs} onSeek={s.handleSeek} vibeLock={s.vibeLock} onVibeLockToggle={s.handleVibeLockToggle} /></section>
          {s.currentMode === "radio" && (
            <>
              <section><CommandBar onCommandSent={s.handleCommandSent} compact onFocus={() => setCommandExpanded(true)} /></section>
              {commandExpanded && (
                <section><CommandBar onCommandSent={s.handleCommandSent} /></section>
              )}
              <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <VolumeControl volume={s.volume} onChange={s.handleVolumeChange} />
                <ExplorationSlider level={s.explorationLevel} onChange={s.handleExplorationChange} />
              </section>
              <section><MoodButtons activeMood={s.mood} onMoodSet={s.handleMoodSet} /></section>
            </>
          )}
          {s.currentMode === "normal" && (
            <div className="text-center py-8 text-gray-500 text-sm">
              <p>Normal mode — no AI features.</p>
              <p className="mt-1">Play songs manually or switch to Radio mode for AI recommendations.</p>
            </div>
          )}
        </div>

        {/* Right column: queue (sticky on desktop) */}
        {s.currentMode === "radio" && (
          <div className="w-full lg:w-80 xl:w-96 flex-shrink-0">
            <div className="lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto">
              <section><QueuePreview queue={s.queue} isRebuilding={s.queueRebuilding} onPromoteSong={handlePromoteSong} /></section>
            </div>
          </div>
        )}
      </main>

      {s.showOnboarding && <Onboarding onComplete={s.handleOnboardingComplete} onSkip={s.handleOnboardingSkip} addToast={s.addToast} />}

      {libraryTrans.shouldRender && (
        <div className={`fixed inset-0 z-50 flex flex-col slide-in-right ${libraryTrans.animClass === "exit-active" ? "slide-out-right" : ""}`}>
          <div className="absolute inset-0 bg-gray-950/95 backdrop-blur-sm" onClick={() => s.setShowLibrary(false)} />
          <div className="relative flex flex-col h-full bg-gray-950/95 backdrop-blur-sm">
          <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
            <div className="flex items-center gap-4">
              <h2 className="text-lg font-bold text-white">Library</h2>
              <div className="flex gap-1">
                {[
                  { id: "library", label: "Library" },
                  { id: "stats", label: "Stats" },
                  { id: "history", label: "History" },
                ].map((tab) => (
                  <button key={tab.id} onClick={() => setLibraryTab(tab.id)}
                    className={`px-3 py-1 rounded-lg text-xs font-medium transition ${libraryTab === tab.id ? "bg-green-600 text-white" : "text-gray-400 hover:text-gray-300"}`}>
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>
            <button onClick={() => s.setShowLibrary(false)} className="px-3 py-1.5 rounded-lg bg-gray-800 text-gray-400 text-sm hover:text-gray-300 transition">Close</button>
          </div>
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {libraryTab === "library" && <SongList addToast={s.addToast} />}
            {libraryTab === "stats" && (
              <div className="space-y-6">
                <DailyReport />
                <ListeningStats />
              </div>
            )}
            {libraryTab === "history" && (
              <div className="space-y-6">
                <MoodTimeline />
                <SessionHistory />
              </div>
            )}
          </div>
          </div>
        </div>
      )}

      <ToastContainer toasts={s.toasts} onRemove={s.removeToast} />
      <SettingsModal isOpen={s.showSettings} onClose={() => s.setShowSettings(false)} addToast={s.addToast} sectionSpacing={s.sectionSpacing} onSectionSpacingChange={s.setSectionSpacing} />
      <ShortcutsModal isOpen={showShortcuts} onClose={() => setShowShortcuts(false)} />
    </div>
  );
}

export default App;
