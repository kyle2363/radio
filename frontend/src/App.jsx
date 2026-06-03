import React, { useEffect, useState, useCallback, useRef } from "react";
import useTransition from "./hooks/useTransition";
import * as api from "./api";
import { Radio, Loader2, Mic, Filter, X } from "lucide-react";

import MiniPlayer from "./components/MiniPlayer";
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
import SettingsContent from "./components/SettingsContent";
import VolumeControl from "./components/VolumeControl";
import Onboarding from "./components/Onboarding";
import SongList from "./components/SongList";
import ToastContainer from "./components/Toast";
import SessionHistory from "./components/SessionHistory";
import StatsDashboard from "./components/StatsDashboard";
import MoodTimeline from "./components/MoodTimeline";
import ShortcutsModal from "./components/ShortcutsModal";
import PriorSongs from "./components/PriorSongs";
import SkipFeedback from "./components/SkipFeedback";
import SleepTimer from "./components/SleepTimer";
import WikiPage from "./components/WikiPage";
import CollectionsList from "./components/CollectionsList";
import CollectionDetail from "./components/CollectionDetail";

// Focused hooks
import { RadioProvider, useSharedRadio, useFetchStatus, useWebSocket, useOnboarding } from "./hooks/useRadioState";
import usePlaybackState from "./hooks/usePlaybackState";
import useQueueState from "./hooks/useQueueState";
import useMoodState from "./hooks/useMoodState";
import useUIState from "./hooks/useUIState";

function AppInner() {
  const { addToast, removeToast, toasts } = useSharedRadio();
  const fetchStatus = useFetchStatus();
  const {
    showOnboarding, setShowOnboarding,
    theme, setTheme,
    sectionSpacing, setSectionSpacing,
    rightRailWidth, setRightRailWidth,
    checkOnboarding,
  } = useOnboarding();
  // Playback state
  const playback = usePlaybackState();
  const {
    currentSong, isPlaying, positionMs, durationMs, volume, audioRms,
    undoState, showSkipFeedback, loading,
    currentSongRef,
    handleVolumeChange, handleSeek, handlePlay, handlePause, handleNext,
    handleHeart, handleThumbsDown, handleReplay, handleClearQueue,
    doUndo, setUndoState, setShowSkipFeedback,
    applyPlaybackStatus,
    setCurrentSong, setIsPlaying, setPositionMs, setDurationMs, setAudioRms,
  } = playback;

  // Queue state
  const { queue, queueRebuilding, whyReason, handlePromoteSong, setQueue, setQueueRebuilding, setWhyReason } = useQueueState(currentSongRef);

  // Rebuild queue with HTTP polling (no WebSocket dependency)
  const handleRebuild = useCallback(async () => {
    rebuildGenRef.current += 1;
    setQueueRebuilding(true);
    try {
      await api.rebuildQueue();
      const currentIds = queue?.map(s => s.song_id) || [];
      const result = await api.waitForQueue(60000, 1000, currentIds);
      if (result && Array.isArray(result)) {
        setQueue(result);
      }
    } catch (e) {
      console.error("Rebuild failed:", e);
    }
    setQueueRebuilding(false);
  }, [setQueueRebuilding, setQueue, queue]);

  // Mood state
  const mood = useMoodState(currentSongRef, fetchStatus);
  const {
    mood: moodState, vibeLock, skipStreak, archetype, archetypeDesc, explorationLevel,
    handleMoodSet, handleVibeLockToggle, handleExplorationChange,
    applyStatus, setMood, setVibeLock, setSkipStreak,
  } = mood;

  const refreshStatus = useCallback(async () => {
    const data = await fetchStatus();
    if (data) {
      applyPlaybackStatus(data);
      applyStatus(data);
    }
    return data;
  }, [fetchStatus, applyPlaybackStatus, applyStatus]);

  const { wsConnected, connectWebSocket } = useWebSocket(refreshStatus);

  // UI state
  const ui = useUIState(fetchStatus);
  const {
    showSettings, setShowSettings,
    showLibrary, setShowLibrary,
    currentMode: uiMode, setCurrentMode: setUiMode,
    talkSegmentActive, setTalkSegmentActive,
    talkGenerating, setTalkGenerating,
    ttsReady, setTtsReady,
    sleepTimer, setSleepTimer,
    handleModeToggle, handleSkipFeedback, handleSleepTimer,
    handleCancelSleepTimer, handleCommandSent, toggleTheme,
    handleOnboardingComplete, handleOnboardingSkip, handleReseedTaste,
  } = ui;

 // Total songs count
  const [totalSongs, setTotalSongs] = useState(342);
  const [queueLoading, setQueueLoading] = useState(true);
  const rebuildGenRef = useRef(0);

  useEffect(() => {
    api.fetchSongs({ page: "1", page_size: "1" }).then((d) => {
      if (d?.total) setTotalSongs(d.total);
    }).catch(() => {});
  }, []);

  // Fetch queue on app load with retry
  useEffect(() => {
    const id = setTimeout(async () => {
      setQueueLoading(true);
      try {
        const result = await api.waitForQueue(45000, 3000);
        if (result && Array.isArray(result)) setQueue(result);
      } catch (e) {}
      setQueueLoading(false);
    }, 3000);
    return () => clearTimeout(id);
  }, [setQueue]);

  // Wrapped play handler — rebuilds queue if empty, then plays
  const wrappedHandlePlay = useCallback(async () => {
    if (!queue || queue.length === 0) {
      await handleRebuild();
    }
    handlePlay();
  }, [queue, handleRebuild, handlePlay]);

  // Refs for inter-hook sync
  const [showShortcuts, setShowShortcuts] = useState(false);
  const sRef = useRef({});
  sRef.current = {
    uiMode, theme, sectionSpacing, rightRailWidth,
    wsConnected,
    currentSong, isPlaying, positionMs, durationMs, volume, audioRms,
    undoState, setUndoState,
    queue, queueRebuilding, whyReason,
    mood: moodState, vibeLock, skipStreak, archetype, archetypeDesc, explorationLevel,
    showOnboarding, setShowOnboarding,
    showLibrary, setShowLibrary,
    toasts: [], addToast, removeToast,
    talkSegmentActive, talkGenerating, ttsReady,
    sleepTimer,
    loading,
    handlePlay, handlePause, handleNext, handleHeart, handleThumbsDown,
    wrappedHandlePlay,
    handleReplay, handleClearQueue, handleSeek, handleVolumeChange,
    handleVibeLockToggle, handleMoodSet, handleExplorationChange,
    handleSkipFeedback, handleCancelSleepTimer, handleCommandSent,
    toggleTheme,
    handleOnboardingComplete, handleOnboardingSkip, handleReseedTaste,
    doUndo,
    showShortcuts, setShowShortcuts,
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable) return;
      const st = sRef.current;
      switch (e.key) {
        case " ": e.preventDefault(); st.isPlaying ? st.handlePause() : st.wrappedHandlePlay(); break;
        case "n": case "N": st.handleNext(); break;
        case "h": case "H": st.handleHeart(); break;
        case "d": case "D": st.handleThumbsDown(); break;
        case "r": case "R": st.handleReplay(); break;
        case "v": case "V": st.handleVibeLockToggle(); break;
        case "ArrowUp": e.preventDefault(); st.handleVolumeChange(Math.min(100, st.volume + 5)); break;
        case "ArrowDown": e.preventDefault(); st.handleVolumeChange(Math.max(0, st.volume - 5)); break;
        case "ArrowLeft": e.preventDefault(); st.handleSeek(Math.max(0, st.positionMs - 10000)); break;
        case "ArrowRight": e.preventDefault(); st.handleSeek(Math.min(st.durationMs, st.positionMs + 10000)); break;
        case "m": case "M": {
          const moods = ["chill", "neutral", "energetic", "focus", "melancholy", "discovery"];
          st.handleMoodSet(moods[(moods.indexOf(st.mood) + 1) % moods.length]);
          break;
        }
        case "Escape":
          if (st.showShortcuts) st.setShowShortcuts(false);
          else if (st.showLibrary) st.setShowLibrary(false);
          break;
        case "?":
          setShowShortcuts((v) => !v);
          break;
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Electron mini mode
  const [miniMode, setMiniMode] = useState(false);
  const [isElectron, setIsElectron] = useState(false);
  useEffect(() => {
    checkOnboarding();
  }, []);

  useEffect(() => {
    if (window.electron?.getMiniMode) {
      window.electron.getMiniMode().then(setMiniMode).catch(() => {});
      setIsElectron(true);
    }
    const cleanup = window.electron?.onMiniModeChange?.((isMini) => setMiniMode(isMini));
    return () => cleanup?.();
  }, []);

  // Energy trajectory polling
  const [energyTrajectory, setEnergyTrajectory] = useState(null);
  useEffect(() => {
    if (!isElectron) return;
    const fetch = async () => {
      try {
        const data = await api.fetchEnergyTrajectory();
        setEnergyTrajectory(data?.direction || null);
      } catch (_) {}
    };
    fetch();
    const id = setInterval(fetch, 15000);
    return () => clearInterval(id);
  }, [isElectron]);

  // Folder filter
  const [activeFolderFilter, setActiveFolderFilter] = useState([]);
  useEffect(() => {
    api.fetchFolderFilter().then((data) => {
      setActiveFolderFilter(data.folders || []);
    }).catch(() => {});
  }, []);

  // Library tab
  const [libraryTab, setLibraryTab] = useState("library");
  const [selectedCollection, setSelectedCollection] = useState(null);
  const libraryTrans = useTransition(showLibrary, 250);
  const [commandExpanded, setCommandExpanded] = useState(false);

  // Polling — fetch play status + mood/archetype
  useEffect(() => {
    const id = setInterval(() => {
      refreshStatus();
    }, 3000);
    return () => clearInterval(id);
  }, [refreshStatus]);

  // WebSocket handlers — re-attach on reconnection (wsConnected change)
  useEffect(() => {
    if (!window._ws) return;
    window._ws._queueHandler = (data) => {
      console.log(">>> _queueHandler received:", JSON.stringify(data).substring(0, 200));
      if (data.queue) {
        if (data.gen == null || data.gen >= rebuildGenRef.current) {
          console.log(">>> _queueHandler setting queue, gen=", data.gen);
          setQueue(data.queue);
        } else {
          console.log(">>> _queueHandler IGNORING stale gen", data.gen, "<", rebuildGenRef.current);
        }
      }
    };
    window._ws._rebuildHandler = (data) => {
      console.log(">>> _rebuildHandler received:", JSON.stringify(data));
      if (data.rebuilding === true) {
        if (data.gen != null) {
          rebuildGenRef.current = data.gen;
        }
        setQueueRebuilding(true);
      } else if (data.rebuilding === false) {
        if (data.gen == null || data.gen >= rebuildGenRef.current) {
          setQueueRebuilding(false);
        }
      }
      setWhyReason(data.reason || "");
    };
    window._ws._songStartedHandler = async () => {
      console.log(">>> song_started handler called");
      setQueue([]);
      setQueueRebuilding(true);
      console.log(">>> queue cleared, rebuilding=true");
      try {
        console.log(">>> starting waitForQueue...");
        const result = await api.waitForQueue(60000, 1000);
        console.log(">>> waitForQueue returned:", result);
        if (result && Array.isArray(result)) {
          setQueue(result);
        }
      } catch (e) {
        console.error("waitForQueue after song_started failed:", e);
      } finally {
        setQueueRebuilding(false);
      }
    };
    window._ws._moodHandler = (state) => {
      setMood(state);
    };
    window._ws._vibeLockHandler = (vl) => {
      setVibeLock(vl);
    };
    window._ws._talkHandler = (data) => {
      setTalkSegmentActive(data);
    };
    window._ws._sleepTimerHandler = () => {
      setSleepTimer(null);
    };
    window._ws._positionHandler = (data) => {
      if (data.elapsed_ms != null) setPositionMs(data.elapsed_ms);
      if (data.duration_ms != null) setDurationMs(data.duration_ms);
    };
  }, [wsConnected, setQueue, setQueueRebuilding, setWhyReason, setMood, setVibeLock, setTalkSegmentActive, setSleepTimer, setPositionMs, setDurationMs]);

  // Handle skip feedback auto-dismiss
  useEffect(() => {
    if (showSkipFeedback) {
      const id = setTimeout(() => setShowSkipFeedback(false), 5000);
      return () => clearTimeout(id);
    }
  }, [showSkipFeedback]);

  // Undo state auto-dismiss
  useEffect(() => {
    if (undoState) {
      const id = setTimeout(() => setUndoState(null), undoState.expires_in_ms || 10000);
      return () => clearTimeout(id);
    }
  }, [undoState, setUndoState]);

  // Position update polling
  useEffect(() => {
    const id = setInterval(() => {
      if (window._ws?.readyState === window.WebSocket.OPEN) {
        window._ws.send("position_request");
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  if (miniMode) {
    return <MiniPlayer />;
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200 flex flex-col" data-theme={theme}>
      <header className="py-3 sticky top-0 z-10" style={{ borderBottom: "1px solid var(--border-subtle)", background: "color-mix(in srgb, var(--bg-page) 85%, transparent)", backdropFilter: "blur(12px)" }}>
        <div className="max-w-6xl mx-auto px-3 sm:px-5 flex items-center gap-3">
          <h1 className="text-lg font-bold tracking-tight" style={{ color: "var(--text-primary)" }}>Radio</h1>
          <span className="text-[11px] font-semibold px-3 py-1 rounded-full"
            style={{ background: "rgba(72,187,120,0.12)", color: "#4ade80", border: "1px solid rgba(72,187,120,0.2)" }}>
            {uiMode === "radio" ? "Radio" : "Normal"}
          </span>

          <div className="flex items-center gap-2 ml-2">
            <span style={{
              width: 6, height: 6, borderRadius: "50%",
              background: wsConnected ? "#4ade80" : "#f59e0b",
              boxShadow: wsConnected ? "0 0 4px rgba(74,222,128,0.4)" : "0 0 4px rgba(245,158,11,0.4)",
            }} title={wsConnected ? "Connected" : "Reconnecting..."} />
            {uiMode === "radio" && <ArchetypeBadge archetype={archetype} />}
            {uiMode === "radio" && <VibeLockBadge vibeLock={vibeLock} currentSong={currentSong} />}
            {uiMode === "radio" && <SkipStreakIndicator skipStreak={skipStreak} />}
            {uiMode === "radio" && activeFolderFilter.length > 0 && (
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium cursor-pointer"
                onClick={() => { api.clearFolderFilter(); setActiveFolderFilter([]); addToast?.("Folder filter cleared", "success"); }}
                style={{
                  background: "rgba(99,153,34,0.1)",
                  border: "1px solid rgba(99,153,34,0.25)",
                  color: "#8bc34a",
                }}>
                <Filter size={10} />
                {activeFolderFilter.length}f
                <X size={10} style={{ opacity: 0.5 }} />
              </span>
            )}
            {uiMode === "radio" && (
              <>
                {/* Talk show generating spinner */}
                {talkGenerating && (
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
                {talkSegmentActive && (
                  <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
                    style={{
                      background: "rgba(96,165,250,0.1)",
                      border: "1px solid rgba(96,165,250,0.25)",
                      color: "#60a5fa",
                    }}>
                    <Mic size={10} />
                    {talkSegmentActive.segment_type === "caller" ? "Caller"
                      : talkSegmentActive.segment_type === "dj_monologue" ? "Monologue"
                      : "Guest"}
                  </span>
                )}
                {/* DJ badge: 3 states */}
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
                  style={{
                    background: talkSegmentActive
                      ? "rgba(96,165,250,0.1)"
                      : ttsReady
                        ? "rgba(72,187,120,0.1)"
                        : "rgba(255,255,255,0.04)",
                    border: `1px solid ${talkSegmentActive
                      ? "rgba(96,165,250,0.25)"
                      : ttsReady
                        ? "rgba(72,187,120,0.25)"
                        : "rgba(255,255,255,0.08)"}`,
                    color: talkSegmentActive
                      ? "#60a5fa"
                      : ttsReady
                        ? "#4ade80"
                        : "rgba(255,255,255,0.3)",
                  }}>
                  <span style={{
                    display: "inline-block",
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: talkSegmentActive
                      ? "#60a5fa"
                      : ttsReady
                        ? "#4ade80"
                        : "rgba(255,255,255,0.15)",
                    boxShadow: talkSegmentActive
                      ? "0 0 6px rgba(96,165,250,0.5)"
                      : ttsReady
                        ? "0 0 6px rgba(72,187,120,0.5)"
                        : "none",
                    animation: talkSegmentActive ? "pulse 1.5s ease-in-out infinite" : "none",
                  }} />
                  DJ
                </span>
              </>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button onClick={() => toggleTheme(theme, setTheme)} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center gap-1.5 btn-muted"
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              {theme === "dark" ? "☀" : "☾"}
            </button>
            <button onClick={() => setShowLibrary(true)} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center gap-1.5 btn-muted">
              Library
            </button>
            <button onClick={() => setShowShortcuts(true)} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center btn-muted"
              title="Keyboard shortcuts (?)">?</button>
            <button onClick={() => { setShowLibrary(true); setLibraryTab("settings"); }} className="min-w-[84px] px-4 py-2.5 rounded-lg text-xs transition flex items-center justify-center gap-1.5 btn-muted">
              Settings
            </button>
            <SleepTimer timer={sleepTimer} onCancel={handleCancelSleepTimer} />
            {window.electron && (
              <button onClick={() => window.electron.toggleMiniPlayer()} className="min-w-[36px] px-3 py-2.5 rounded-lg text-xs transition flex items-center justify-center btn-muted"
                title="Mini Player">
                &#9632;
              </button>
            )}
          </div>
        </div>
      </header>

      <main className={`${isElectron ? '' : 'max-w-6xl mx-auto px-3 sm:px-6 py-8'} flex flex-col lg:flex-row gap-0 ${isElectron ? 'electron-layout' : ''}`} style={{ "--section-gap": sectionSpacing === "6" ? "1.5rem" : sectionSpacing === "8" ? "2rem" : sectionSpacing === "12" ? "3rem" : "2.5rem", "--right-rail-width": rightRailWidth + "px" }}>
        {/* Left column: player + controls */}
        <div className={`flex-1 min-w-0 flex flex-col ${isElectron ? 'left-col' : ''}`} style={{ gap: "var(--section-gap)" }}>
          <div className={isElectron ? 'npc-section' : ''} style={!isElectron ? { display: "flex", flexDirection: "column", gap: "var(--section-gap)" } : {}}>
            <section><NowPlaying key={currentSong?.id || "empty"} compact={isElectron} song={currentSong} isPlaying={isPlaying} whyReason={whyReason} queue={queue} audioRms={audioRms} talkSegmentActive={talkSegmentActive} positionMs={positionMs} durationMs={durationMs} /></section>
            <section><PlayerControls compact={isElectron} isPlaying={isPlaying} onPlay={wrappedHandlePlay} onPause={handlePause} onNext={handleNext} onHeart={handleHeart} onThumbsDown={handleThumbsDown} onReplay={handleReplay} onClearQueue={handleClearQueue} positionMs={positionMs} durationMs={durationMs} onSeek={handleSeek} vibeLock={vibeLock} onVibeLockToggle={handleVibeLockToggle} audioRms={audioRms} /></section>
          </div>
          {uiMode === "radio" && (
            <div className={isElectron ? 'rcb-section' : ''} style={!isElectron ? { display: "flex", flexDirection: "column", gap: "var(--section-gap)", background: "var(--bg-sunken)", padding: "12px", borderRadius: "12px" } : undefined}>
              {!commandExpanded && <section><CommandBar onCommandSent={handleCommandSent} compact onFocus={() => setCommandExpanded(true)} /></section>}
              {commandExpanded && (
                <section><CommandBar onCommandSent={handleCommandSent} onClose={() => setCommandExpanded(false)} /></section>
              )}
              <section><MoodButtons activeMood={moodState} onMoodSet={handleMoodSet} /></section>
              <section className="flex items-center gap-4 px-6 py-4" style={{ background: "var(--bg-surface)" }}>
                <VolumeControl volume={volume} onChange={handleVolumeChange} />
                <div style={{ width: "1px", height: "24px", background: "var(--border)", flexShrink: 0 }} />
                <ExplorationSlider level={explorationLevel} onChange={handleExplorationChange} />
              </section>
            </div>
          )}
          {uiMode === "normal" && (
            <div className={`text-center py-8 text-gray-500 text-sm ${isElectron ? 'rcb-section' : ''}`}>
              <p>Normal mode — no AI features.</p>
              <p className="mt-1">Play songs manually or switch to Radio mode for AI recommendations.</p>
            </div>
          )}
        </div>

        {/* Right column: queue + prior songs + session context (sticky on desktop) */}
        {uiMode === "radio" && (
          <div className={`${isElectron ? '' : 'w-full lg:w-80 xl:w-96'} flex-shrink-0 ${isElectron ? 'right-rail' : ''}`} style={isElectron ? { width: rightRailWidth } : undefined}>
            <div className={isElectron ? '' : 'lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto'} style={isElectron ? { display: "flex", flexDirection: "column", flex: 1, overflow: "hidden", minHeight: 0, height: "100%" } : { display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", flex: 1, overflow: "hidden" }}>
                <section className={isElectron ? 'queue-section' : ''}><QueuePreview queue={queue} isRebuilding={queueRebuilding} onPromoteSong={handlePromoteSong} onRebuild={handleRebuild} compact={isElectron} queueLoading={queueLoading} /></section>
                <section className={isElectron ? 'prior-section' : ''}><PriorSongs currentSongId={currentSong?.id} compact={isElectron} /></section>
              </div>
              <section className={isElectron ? 'context-section' : ''} style={{ background: "var(--bg-sunken)", borderTop: "1px solid var(--border)", padding: isElectron ? "8px 10px" : "8px 20px", borderRadius: isElectron ? 0 : "0 0 12px 12px" }}>
                <div className="flex items-center justify-between gap-2 flex-wrap w-full">
                  {moodState && <span className="badge b-teal">{moodState}</span>}
                  {archetype && <span className="badge b-blue">{archetype}</span>}
                  {currentSong?.cluster_id != null && (
                    <span className="badge b-dim">C{currentSong.cluster_id}</span>
                  )}
                  {energyTrajectory && (
                    <span className="badge b-dim">
                      {energyTrajectory === "rising" ? "↑" : energyTrajectory === "falling" ? "↓" : "—"} energy
                    </span>
                  )}
                  {skipStreak >= 2 && (
                    <span className="badge" style={{ background: "rgba(240,120,136,0.15)", color: "#f07888", border: "1px solid rgba(240,120,136,0.25)" }}>
                      skip×{skipStreak}
                    </span>
                  )}
                </div>
              </section>
            </div>
          </div>
        )}
      </main>

      {showOnboarding && <Onboarding onComplete={handleOnboardingComplete} onSkip={handleOnboardingSkip} addToast={addToast} />}

      {libraryTrans.shouldRender && (
        <div className={`fixed inset-0 z-50 flex flex-col slide-in-right ${libraryTrans.animClass === "exit-active" ? "slide-out-right" : ""}`}>
          <div className="absolute inset-0 bg-gray-950/95 backdrop-blur-sm" onClick={() => setShowLibrary(false)} />
          <div className="relative flex flex-col h-full bg-gray-950/95 backdrop-blur-sm">
          <div className="panel-topbar">
            {[
              { id: "library", label: "Library" },
              { id: "collections", label: "Collections" },
              { id: "settings", label: "Settings" },
              { id: "wiki", label: "Wiki" },
              { id: "stats", label: "Stats" },
              { id: "history", label: "History" },
            ].map((tab) => (
              <div key={tab.id} className={`panel-tab ${libraryTab === tab.id ? "on" : ""}`}
                onClick={() => setLibraryTab(tab.id)}>{tab.label}</div>
            ))}
            <div style={{ flex: 1 }} />
            <button onClick={() => setShowLibrary(false)} title="Close"
              style={{ marginLeft: "8px", display: "flex", alignItems: "center", justifyContent: "center", width: "24px", height: "24px", borderRadius: "6px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer", fontSize: "14px", lineHeight: 1 }}>
              ×
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {libraryTab === "library" && <SongList addToast={addToast} currentSongId={currentSong?.id} onPlay={(song) => { api.playSong(song.id) }} />}
            {libraryTab === "collections" && (
              selectedCollection
                ? <CollectionDetail collection={selectedCollection} onBack={() => setSelectedCollection(null)} addToast={addToast} />
                : <CollectionsList onSelect={(c) => setSelectedCollection(c)} />
            )}
            {libraryTab === "settings" && <SettingsContent addToast={addToast} sectionSpacing={sectionSpacing} onSectionSpacingChange={setSectionSpacing} rightRailWidth={rightRailWidth} setRightRailWidth={setRightRailWidth} />}
            {libraryTab === "wiki" && <WikiPage />}
            {libraryTab === "stats" && <div className="px-1 sm:px-2"><StatsDashboard /></div>}
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

      <SkipFeedback visible={showSkipFeedback} onReason={(reason) => handleSkipFeedback(reason, () => setShowSkipFeedback(false))} onDismiss={() => setShowSkipFeedback(false)} />
      <ToastContainer toasts={toasts} onRemove={removeToast} />
      <ShortcutsModal isOpen={showShortcuts} onClose={() => setShowShortcuts(false)} />
    </div>
  );
}

export default function App() {
  return (
    <RadioProvider>
      <AppInner />
    </RadioProvider>
  );
}
