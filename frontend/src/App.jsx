import React, { useEffect, useState, useCallback, useRef, useLayoutEffect } from "react";
import useTransition from "./hooks/useTransition";
import usePlaybackSession from './hooks/usePlaybackSession.js';
import PlaybackOutputSelector from './components/PlaybackOutputSelector.jsx';
import * as api from "./api";
import { Loader2, Mic, Sparkles, Sun, Moon, Library, HelpCircle, Settings } from "lucide-react";

import AmbientGlow from "./components/AmbientGlow";
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
import ConstellationMap from "./components/ConstellationMap";
import LyricsPanel from "./components/LyricsPanel";
import QuickSearch from "./components/QuickSearch";
import CommandPalette from "./components/CommandPalette";
import HeaderButton from "./components/HeaderButton";
import HeaderStatusPill from "./components/HeaderStatusPill";
import { BrandIcon, DEFAULT_ICON_STYLE } from "./components/BrandIcons";

// Focused hooks
import { RadioProvider, useSharedRadio, useFetchStatus, useWebSocket, useOnboarding } from "./hooks/useRadioState";
import usePlaybackState from "./hooks/usePlaybackState";
import useQueueState from "./hooks/useQueueState";
import useMoodState from "./hooks/useMoodState";
import useUIState from "./hooks/useUIState";

function AppInner({ outputPlayback }) {
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
    heartedThisPlay, thumbedDownThisPlay,
    currentSongRef,
    handleVolumeChange, handleSeek, handlePlay, handlePause, handleNext,
    handleHeart, handleThumbsDown, handleReplay, handleClearQueue,
    doUndo, setUndoState, setShowSkipFeedback,
    applyPlaybackStatus,
    setCurrentSong, setIsPlaying, setPositionMs, setDurationMs, setAudioRms,
  } = playback;

  // Queue state
  const { queue, queueRebuilding, whyReason, handlePromoteSong, handleRemoveFromQueue, setQueue, setQueueRebuilding, setWhyReason } = useQueueState(currentSongRef);

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

  // UI state (must be before refreshStatus to avoid TDZ)
  const ui = useUIState(fetchStatus);
  const {
    showSettings, setShowSettings,
    showLibrary, setShowLibrary,
    currentMode: uiMode, setCurrentMode: setUiMode,
    talkSegmentActive, setTalkSegmentActive,
    talkGenerating, setTalkGenerating,
    ttsReady, setTtsReady,
    sleepTimer, setSleepTimer,
    handleModeToggle, handleSkipFeedback, handleSkipFeedbackWithDuration, handleSleepTimer,
    handleCancelSleepTimer, handleCommandSent, toggleTheme,
    handleOnboardingComplete, handleOnboardingSkip, handleReseedTaste,
    showQuickSearch, setShowQuickSearch,
  } = ui;

  const refreshStatus = useCallback(async () => {
    const data = await fetchStatus();
    if (data) {
      applyPlaybackStatus(data);
      applyStatus(data);
      // Sync talk overlay: clear when backend says talk is no longer playing
      if (!data.talk_playing && talkSegmentActive) {
        setTalkSegmentActive(null);
      }
    }
    return data;
  }, [fetchStatus, applyPlaybackStatus, applyStatus, talkSegmentActive, setTalkSegmentActive]);

  const { wsConnected, connectWebSocket } = useWebSocket(refreshStatus);

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

  // Play mode state (ai/shuffle/sequential)
  const [playMode, setPlayMode] = useState("ai");
  useEffect(() => {
    api.fetchPlayMode().then((d) => { if (d?.mode) setPlayMode(d.mode); }).catch(() => {});
  }, []);

  const handlePlayModeChange = useCallback(async (mode) => {
    try {
      await api.setPlayMode(mode);
      setPlayMode(mode);
      addToast?.("Play mode: " + mode, "success");
    } catch (e) {
      addToast?.("Failed to change play mode", "error");
    }
  }, [addToast]);

  // Handle adding song to collection (from queue context menu)
  const handleAddToCollection = useCallback(async (songId, collectionId) => {
    try {
      await api.addToCollection(collectionId, songId);
      addToast?.("Added to collection", "success");
    } catch (e) {
      addToast?.("Failed to add to collection", "error");
    }
  }, [addToast]);

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
    playMode,
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
    showQuickSearch, setShowQuickSearch,
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable) return;
      const st = sRef.current;
      if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); st.setShowQuickSearch?.(v => !v); return; }
      if ((e.ctrlKey || e.metaKey) && e.key === "z") {
        if (!st.showCommandPalette) { e.preventDefault(); st.handleUndoNlCommand?.(); return; }
      }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "K") { e.preventDefault(); st.setShowCommandPalette?.(v => !v); return; }
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

  // SMTC / Media Session API — exposes playback to OS media controls
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    // Register handlers once; they read current state from sRef
    navigator.mediaSession.setActionHandler("play", () => sRef.current.wrappedHandlePlay?.());
    navigator.mediaSession.setActionHandler("pause", () => sRef.current.handlePause?.());
    navigator.mediaSession.setActionHandler("nexttrack", () => sRef.current.handleNext?.());
    navigator.mediaSession.setActionHandler("previoustrack", () => {
      const st = sRef.current;
      if (st.positionMs > 5000) st.handleSeek?.(0);
      else st.handleReplay?.();
    });
    navigator.mediaSession.setActionHandler("stop", () => sRef.current.handlePause?.());
  }, []);

  // Update media session metadata when song changes
  useEffect(() => {
    if (!("mediaSession" in navigator) || !currentSong?.id) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: currentSong.title || "Unknown",
      artist: currentSong.artist || "Unknown",
      album: currentSong.album || "",
      artwork: [
        { src: `${window.location.origin}/api/songs/${currentSong.id}/art.jpg`, sizes: "512x512", type: "image/jpeg" },
      ],
    });
  }, [currentSong?.id, currentSong?.title, currentSong?.artist]);

  // Sync media session playback state
  useEffect(() => {
    if ("mediaSession" in navigator) {
      navigator.mediaSession.playbackState = isPlaying ? "playing" : "paused";
    }
  }, [isPlaying]);

  // Electron mini mode
  const [miniMode, setMiniMode] = useState(false);
  const [miniPlayerEnabled, setMiniPlayerEnabled] = useState(true);
  const [showLyrics, setShowLyrics] = useState(true);
  const [isElectron, setIsElectron] = useState(false);
  const [constellationsEnabled, setConstellationsEnabled] = useState(true);
  const [iconStyle, setIconStyle] = useState(DEFAULT_ICON_STYLE);
  useEffect(() => {
    checkOnboarding();
    let cancelled = false;
    const doFetch = () => {
      api.fetchSettings().then((d) => {
        if (cancelled) return;
        if (d.right_rail_width) {
          const w = parseInt(d.right_rail_width) || 255;
          setRightRailWidth(w);
          document.documentElement.style.setProperty("--right-rail-width", w + "px");
        }
        setMiniPlayerEnabled(d.mini_player_enabled !== "false");
        if (d.show_lyrics != null) setShowLyrics(d.show_lyrics !== "false");
        if (d.constellations_enabled != null) setConstellationsEnabled(d.constellations_enabled !== "false");
        if (d.app_icon_style) setIconStyle(d.app_icon_style);
      }).catch(() => {
        if (!cancelled) setTimeout(doFetch, 500);
      });
    };
    doFetch();
    // Listen for icon-style changes from settings panel
    const onStyleChange = (e) => {
      const s = e.detail;
      if (s) setIconStyle(s);
    };
    window.addEventListener("app-icon-style-changed", onStyleChange);
    return () => { cancelled = true; window.removeEventListener("app-icon-style-changed", onStyleChange); };
  }, []);

  useEffect(() => {
    if (window.electron?.getMiniMode) {
      window.electron.getMiniMode().then(setMiniMode).catch(() => {});
      setIsElectron(true);
    }
    const cleanup = window.electron?.onMiniModeChange?.((isMini) => setMiniMode(isMini));
    return () => cleanup?.();
  }, []);

  // Sync layout CSS variables to :root (same pattern as accent_color)
  useLayoutEffect(() => {
    const gap = sectionSpacing === "6" ? "0.5rem" : sectionSpacing === "8" ? "1rem" : sectionSpacing === "12" ? "1.5rem" : "1rem";
    document.documentElement.style.setProperty("--section-gap", gap);
    document.documentElement.style.setProperty("--right-rail-width", rightRailWidth + "px");
  }, [sectionSpacing, rightRailWidth]);

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

  // Recommendation filters
  const [activeFilters, setActiveFilters] = useState({ folders: [], mtime_min_days: null, mtime_max_days: null, artist_mode: null, artists: [] });
  const hasActiveFilters = activeFilters.folders?.length > 0 || activeFilters.mtime_min_days != null || activeFilters.mtime_max_days != null || activeFilters.artist_mode;
  useEffect(() => {
    api.fetchRecommendationFilters().then((data) => {
      setActiveFilters(data);
    }).catch(() => {
      api.fetchFolderFilter().then((data) => {
        setActiveFilters(prev => ({ ...prev, folders: data.folders || [] }));
      }).catch(() => {});
    });
  }, []);
  // Also listen for WS broadcast
  useEffect(() => {
    if (!window._ws) return;
    const handler = (data) => setActiveFilters(data);
    window._ws._recFilterHandler = handler;
    return () => { if (window._ws) window._ws._recFilterHandler = null; };
  }, [wsConnected]);

  // Library tab
  const [libraryTab, setLibraryTab] = useState("library");
  const [selectedCollection, setSelectedCollection] = useState(null);
  const libraryTrans = useTransition(showLibrary, 250);
  const [commandExpanded, setCommandExpanded] = useState(false);
  const [commandResponseTimeout, setCommandResponseTimeout] = useState(10);
  const [commandInactiveTimeout, setCommandInactiveTimeout] = useState(6);
  const inactiveTimerRef = useRef(null);
  const lastNlActionRef = useRef(null);
  const [showCommandPalette, setShowCommandPalette] = useState(false);

  // Lifted response state — persists across compact/expanded CommandBar transitions
  const [nlResponse, setNlResponse] = useState(null);
  const [nlResponseVisible, setNlResponseVisible] = useState(false);
  const nlResponseTimerRef = useRef(null);
  const nlPausedRef = useRef(false);

  const clearNlResponseTimer = useCallback(() => {
    if (nlResponseTimerRef.current) {
      clearTimeout(nlResponseTimerRef.current);
      nlResponseTimerRef.current = null;
    }
  }, []);

  const startNlResponseTimer = useCallback(() => {
    clearNlResponseTimer();
    if (commandResponseTimeout <= 0) return;
    nlResponseTimerRef.current = setTimeout(() => {
      if (!nlPausedRef.current) {
        setNlResponseVisible(false);
        setTimeout(() => setNlResponse(null), 300);
      }
    }, commandResponseTimeout * 1000);
  }, [commandResponseTimeout, clearNlResponseTimer]);

  // Clean up response timer on unmount
  useEffect(() => {
    return () => {
      if (nlResponseTimerRef.current) clearTimeout(nlResponseTimerRef.current);
    };
  }, []);

  const clearInactiveTimer = useCallback(() => {
    if (inactiveTimerRef.current) {
      clearTimeout(inactiveTimerRef.current);
      inactiveTimerRef.current = null;
    }
  }, []);

  const startInactiveTimer = useCallback(() => {
    clearInactiveTimer();
    if (commandInactiveTimeout <= 0) return;
    inactiveTimerRef.current = setTimeout(() => {
      setCommandExpanded(false);
    }, commandInactiveTimeout * 1000);
  }, [commandInactiveTimeout, clearInactiveTimer]);

  const handleUserActivity = useCallback(() => {
    if (commandExpanded) {
      clearInactiveTimer();
      startInactiveTimer();
    }
  }, [commandExpanded, clearInactiveTimer, startInactiveTimer]);

  // Fetch command timeout settings
  useEffect(() => {
    api.fetchSettings().then((d) => {
      if (d.command_response_timeout) setCommandResponseTimeout(parseInt(d.command_response_timeout) || 10);
      if (d.command_inactive_timeout) setCommandInactiveTimeout(parseInt(d.command_inactive_timeout) || 6);
    }).catch(() => {});
  }, []);

  // Start/stop inactivity timer when commandExpanded changes
  useEffect(() => {
    if (commandExpanded) {
      startInactiveTimer();
    } else {
      clearInactiveTimer();
    }
  }, [commandExpanded, startInactiveTimer, clearInactiveTimer]);

  // Wrap handleCommandSent to track last NL action for undo
  const wrappedHandleCommandSent = useCallback((data) => {
    if (data?.action) {
      const snapshot = {};
      if (data.action === "mood_shift") {
        snapshot.previousMood = sRef.current.mood;
      } else if (data.action === "volume_up" || data.action === "volume_down" || data.action === "set_volume") {
        snapshot.previousVolume = sRef.current.volume;
      } else if (data.action === "mode_switch") {
        snapshot.previousMode = sRef.current.uiMode;
      }
      lastNlActionRef.current = { ...data, ...snapshot };
    }
    handleCommandSent(data);
  }, [handleCommandSent]);

  // Undo last NL command
  const handleUndoNlCommand = useCallback(() => {
    const last = lastNlActionRef.current;
    if (!last) {
      sRef.current.addToast?.("Nothing to undo", "info");
      return;
    }
    lastNlActionRef.current = null;
    const st = sRef.current;
    switch (last.action) {
      case "genre_filter":
      case "artist_filter":
        api.sendCommand("clear filters").then((r) => {
          handleCommandSent(r);
          st.addToast?.("Filter cleared", "success");
        }).catch(() => st.addToast?.("Undo failed", "error"));
        break;
      case "clear_filters":
        st.addToast?.("Nothing to undo", "info");
        break;
      case "mood_shift":
        if (last.previousMood) {
          st.handleMoodSet(last.previousMood);
          st.addToast?.(`Mood reverted to ${last.previousMood}`, "success");
        }
        break;
      case "volume_up":
      case "volume_down":
      case "set_volume":
        if (last.previousVolume != null) {
          st.handleVolumeChange(last.previousVolume);
          st.addToast?.(`Volume reverted to ${last.previousVolume}`, "success");
        }
        break;
      case "mode_switch":
        if (last.previousMode) {
          api.toggleMode(last.previousMode).then(() => {
            handleCommandSent({ action: "mode_switch", mode: last.previousMode });
            st.addToast?.(`Switched back to ${last.previousMode} mode`, "success");
          }).catch(() => st.addToast?.("Undo failed", "error"));
        }
        break;
      case "pause":
        st.wrappedHandlePlay();
        st.addToast?.("Resumed", "success");
        break;
      case "resume":
        st.handlePause();
        st.addToast?.("Paused", "success");
        break;
      default:
        st.addToast?.("Can't undo that action", "info");
    }
  }, [handleCommandSent]);

  // Sync late-declared values into sRef for keyboard handler access
  sRef.current.showCommandPalette = showCommandPalette;
  sRef.current.setShowCommandPalette = setShowCommandPalette;
  sRef.current.handleUndoNlCommand = handleUndoNlCommand;
  sRef.current.commandResponseTimeout = commandResponseTimeout;
  sRef.current.commandInactiveTimeout = commandInactiveTimeout;

  // Constellation state
  const [showConstellations, setShowConstellations] = useState(false);
  const [constellationData, setConstellationData] = useState(null);
  useEffect(() => {
    if (!constellationsEnabled) {
      setConstellationData(null);
      setShowConstellations(false);
      return;
    }
    if (uiMode !== "radio") return;
    const fetch = async () => {
      try {
        const data = await api.fetchConstellations();
        setConstellationData(data);
      } catch (_) {}
    };
    fetch();
    const id = setInterval(fetch, 30000);
    return () => clearInterval(id);
  }, [uiMode, constellationsEnabled]);
  useEffect(() => {
    if (!showConstellations) return;
    const id = setInterval(async () => {
      try {
        const data = await api.fetchConstellations();
        setConstellationData(data);
      } catch (_) {}
    }, 15000);
    return () => clearInterval(id);
  }, [showConstellations]);

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
    window._ws._songStartedHandler = (data) => {
      setTalkSegmentActive(null);
      // Reset position to 0 immediately when a new song starts,
      // so lyrics don't lag behind during the first position_request cycle.
      // The song_started broadcast includes elapsed_ms: 0 for fresh starts.
      setPositionMs(0);
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
    window._ws._playModeHandler = (mode) => {
      setPlayMode(mode);
    };
    window._ws._positionHandler = (data) => {
      if (data.elapsed_ms != null) setPositionMs(data.elapsed_ms);
      if (data.duration_ms != null) setDurationMs(data.duration_ms);
    };
  }, [wsConnected, setQueue, setQueueRebuilding, setWhyReason, setMood, setVibeLock, setTalkSegmentActive, setSleepTimer, setPositionMs, setDurationMs, setPlayMode]);

  // Undo state auto-dismiss
  useEffect(() => {
    if (undoState) {
      const id = setTimeout(() => setUndoState(null), undoState.expires_in_ms || 10000);
      return () => clearTimeout(id);
    }
  }, [undoState, setUndoState]);

  // Position update polling — 300ms for smoother lyrics sync
  useEffect(() => {
    const id = setInterval(() => {
      if (window._ws?.readyState === window.WebSocket.OPEN) {
        window._ws.send("position_request");
      }
    }, 300);
    return () => clearInterval(id);
  }, []);

  if (miniMode && miniPlayerEnabled) {
    return <MiniPlayer />;
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200 flex flex-col" data-theme={theme}>
      <AmbientGlow song={currentSong} mood={moodState} />
      <header className="py-3 sticky top-0 z-10" style={{ borderBottom: "1px solid var(--border-subtle)", background: "color-mix(in srgb, var(--bg-page) 85%, transparent)", backdropFilter: "blur(12px)" }}>
        <div className="px-3 sm:px-5 flex items-center gap-3">
          {/* LEFT: Brand */}
          <div className="flex items-center gap-2 flex-shrink-0">
            <BrandIcon style={iconStyle} size={26} />
            <span className="text-[11px] font-semibold px-3 py-1 rounded-full"
              style={{ background: "rgba(72,187,120,0.12)", color: "#4ade80", border: "1px solid rgba(72,187,120,0.2)" }}>
              {uiMode === "radio" ? "Radio" : "Normal"}
            </span>
            {uiMode === "normal" && (
              <div className="flex items-center gap-0.5 rounded-full py-0.5 px-0.5" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)" }}>
                {["ai", "shuffle", "sequential"].map((m) => (
                  <button key={m} onClick={() => handlePlayModeChange(m)}
                    className="text-[10px] font-semibold px-2 py-1 rounded-full transition-all"
                    style={{
                      background: playMode === m ? "var(--accent-muted)" : "transparent",
                      color: playMode === m ? "var(--accent)" : "var(--text-muted)",
                      border: "none", cursor: "pointer", textTransform: "uppercase", letterSpacing: "0.05em",
                    }}>
                    {m === "ai" ? "AI" : m === "shuffle" ? "Shuffle" : "Seq"}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* MIDDLE: Status pills (radio mode only) */}
          {uiMode === "radio" && (
            <>
              <div style={{ width: "0.5px", height: 22, background: "rgba(255,255,255,0.12)", flexShrink: 0 }} />
              <div className="flex items-center gap-[6px] flex-wrap overflow-hidden">
                <ArchetypeBadge archetype={archetype} />
                <VibeLockBadge vibeLock={vibeLock} currentSong={currentSong} />
                <SkipStreakIndicator skipStreak={skipStreak} />

                <HeaderStatusPill dotColor={
                  talkSegmentActive ? "#60a5fa" : ttsReady ? "#4ade80" : "#5f6b7a"
                }>
                  DJ
                </HeaderStatusPill>

                {talkGenerating && (
                  <HeaderStatusPill dotColor="#c084fc">
                    <Loader2 size={11} className="animate-spin" />
                    Talk
                  </HeaderStatusPill>
                )}
                {talkSegmentActive && (
                  <HeaderStatusPill dotColor="#60a5fa">
                    <Mic size={11} />
                    {talkSegmentActive.segment_type === "caller" ? "Caller"
                      : talkSegmentActive.segment_type === "dj_monologue" ? "Monologue"
                      : "Guest"}
                  </HeaderStatusPill>
                )}
              </div>
            </>
          )}

          {/* Spacer */}
          <div className="flex-1" />
          <PlaybackOutputSelector playback={outputPlayback} />

          {/* RIGHT: Action buttons + session controls */}
          <div className="flex items-center gap-[2px] flex-shrink-0">
            <HeaderButton onClick={() => toggleTheme(theme, setTheme)}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </HeaderButton>
            <HeaderButton onClick={() => setShowLibrary(true)} title="Library">
              <Library size={18} />
            </HeaderButton>
            {uiMode === "radio" && constellationsEnabled && (
              <HeaderButton onClick={() => setShowConstellations(true)} title="Taste Constellations">
                <Sparkles size={18} />
                {constellationData?.active && (
                  <span style={{
                    position: "absolute", top: 5, right: 5,
                    width: 6, height: 6, borderRadius: "50%",
                    background: constellationData?.influencing ? "#4ade80" : "#4a5a78",
                    border: "1.5px solid var(--bg-page)",
                  }} />
                )}
              </HeaderButton>
            )}
            <HeaderButton onClick={() => setShowShortcuts(true)} title="Keyboard shortcuts">
              <HelpCircle size={18} />
            </HeaderButton>
            <HeaderButton onClick={() => { setShowLibrary(true); setLibraryTab("settings"); }} title="Settings">
              <Settings size={18} />
            </HeaderButton>

            <div style={{ width: "0.5px", height: 22, background: "rgba(255,255,255,0.12)", flexShrink: 0, margin: "0 4px" }} />

            <SleepTimer timer={sleepTimer} onCancel={handleCancelSleepTimer} />
            {window.electron && miniPlayerEnabled && (
              <HeaderButton onClick={() => window.electron.toggleMiniPlayer()} title="Mini Player">
                &#9632;
              </HeaderButton>
            )}
          </div>
        </div>
      </header>

      <main className={`${isElectron ? '' : 'max-w-6xl mx-auto px-3 sm:px-6 py-8'} flex flex-col lg:flex-row gap-0 ${isElectron ? 'electron-layout' : 'website-layout'}`}>
        {/* Left column: player + controls */}
        <div className={`flex-1 min-w-0 flex flex-col ${isElectron ? 'left-col' : 'website-left-col'}`} style={{ gap: "var(--section-gap)" }}>
          <div className={isElectron ? 'npc-section' : 'website-npc-section'} style={!isElectron ? { display: "flex", flexDirection: "column", gap: "var(--section-gap)" } : {}}>
            <section><NowPlaying compact song={currentSong} isPlaying={isPlaying} whyReason={whyReason} queue={queue} audioRms={audioRms} talkSegmentActive={talkSegmentActive} positionMs={positionMs} durationMs={durationMs} /></section>
            <section><PlayerControls compact isPlaying={isPlaying} onPlay={wrappedHandlePlay} onPause={handlePause} onNext={handleNext} onHeart={handleHeart} onThumbsDown={handleThumbsDown} onReplay={handleReplay} onClearQueue={handleClearQueue} positionMs={positionMs} durationMs={durationMs} onSeek={handleSeek} vibeLock={vibeLock} onVibeLockToggle={handleVibeLockToggle} audioRms={audioRms} isHearted={heartedThisPlay} isThumbedDown={thumbedDownThisPlay} /></section>
            {currentSong && showLyrics && (
              <section><LyricsPanel song={currentSong} positionMs={positionMs} durationMs={durationMs} isPlaying={isPlaying} /></section>
            )}
          </div>
          {uiMode === "radio" && (
            <div className={isElectron ? 'rcb-section' : 'website-rcb-section'} style={!isElectron ? { display: "flex", flexDirection: "column", gap: "var(--section-gap)", background: "var(--bg-sunken)" } : undefined}>
              {!commandExpanded && <section><CommandBar onCommandSent={wrappedHandleCommandSent} compact onFocus={() => setCommandExpanded(true)} responseTimeout={commandResponseTimeout} inactiveTimeout={commandInactiveTimeout} onUserActivity={handleUserActivity} lastCommandRef={lastNlActionRef} onUndo={handleUndoNlCommand} nlResponse={nlResponse} nlResponseVisible={nlResponseVisible} nlResponseTimerRef={nlResponseTimerRef} nlPausedRef={nlPausedRef} clearNlResponseTimer={clearNlResponseTimer} startNlResponseTimer={startNlResponseTimer} setNlResponse={setNlResponse} setNlResponseVisible={setNlResponseVisible} /></section>}
              {commandExpanded && (
                <section><CommandBar onCommandSent={wrappedHandleCommandSent} onClose={() => setCommandExpanded(false)} responseTimeout={commandResponseTimeout} inactiveTimeout={commandInactiveTimeout} onUserActivity={handleUserActivity} lastCommandRef={lastNlActionRef} onUndo={handleUndoNlCommand} nlResponse={nlResponse} nlResponseVisible={nlResponseVisible} nlResponseTimerRef={nlResponseTimerRef} nlPausedRef={nlPausedRef} clearNlResponseTimer={clearNlResponseTimer} startNlResponseTimer={startNlResponseTimer} setNlResponse={setNlResponse} setNlResponseVisible={setNlResponseVisible} /></section>
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
            <div className={`text-center py-8 text-gray-500 text-sm ${isElectron ? 'rcb-section' : 'website-rcb-section'}`}>
              <p>Normal mode — no AI features.</p>
              <p className="mt-1">Play songs manually or switch to Radio mode for AI recommendations.</p>
            </div>
          )}
        </div>

        {/* Right column: queue + prior songs + session context (sticky on desktop) */}
        {uiMode === "radio" && (
          <div className={`${isElectron ? '' : 'w-full lg:w-80 xl:w-96'} flex-shrink-0 ${isElectron ? 'right-rail' : 'website-right-rail'}`} style={isElectron ? { width: rightRailWidth } : undefined}>
            <div className={isElectron ? '' : 'website-right-rail-inner'} style={{ display: "flex", flexDirection: "column", flex: 1, overflow: "hidden", minHeight: 0, height: "100%" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", flex: 1, overflow: "hidden" }}>
                <section className={isElectron ? 'queue-section' : 'website-queue-section'}><QueuePreview queue={queue} isRebuilding={queueRebuilding} onPromoteSong={handlePromoteSong} onRemoveSong={handleRemoveFromQueue} onRebuild={handleRebuild} compact queueLoading={queueLoading} onAddToCollection={handleAddToCollection} /></section>
                <section className={isElectron ? 'prior-section' : 'website-prior-section'}><PriorSongs currentSongId={currentSong?.id} compact /></section>
              </div>
              <section className={isElectron ? 'context-section' : 'website-context-section'} style={{ background: "var(--bg-sunken)", borderTop: "1px solid var(--border)", padding: "8px 10px", borderRadius: 0 }}>
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

      {showConstellations && constellationData && (
        <div className="fixed inset-0 z-50 flex flex-col fade-in" style={{ background: "var(--bg-overlay)" }}>
          <ConstellationMap
            constellations={constellationData}
            onPlaySong={(id) => api.playSong(id)}
            onAddToQueue={(id) => api.addToQueue(id)}
            onClose={() => setShowConstellations(false)}
          />
        </div>
      )}

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

            {libraryTab === "settings" && <SettingsContent addToast={addToast} sectionSpacing={sectionSpacing} onSectionSpacingChange={setSectionSpacing} rightRailWidth={rightRailWidth} setRightRailWidth={setRightRailWidth} explorationLevel={explorationLevel} onExplorationChange={handleExplorationChange} />}
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

      <SkipFeedback visible={showSkipFeedback} onReason={(reason, duration) => {
        if (duration) {
          handleSkipFeedbackWithDuration(reason, duration, () => setShowSkipFeedback(false));
        } else {
          handleSkipFeedback(reason, () => setShowSkipFeedback(false));
        }
      }} onDismiss={() => setShowSkipFeedback(false)} />
      <QuickSearch visible={showQuickSearch} onClose={() => setShowQuickSearch(false)} addToast={addToast} />
      <CommandPalette visible={showCommandPalette} onClose={() => setShowCommandPalette(false)} addToast={addToast} onCommandSent={wrappedHandleCommandSent} />
      <ToastContainer toasts={toasts} onRemove={removeToast} />
      <ShortcutsModal isOpen={showShortcuts} onClose={() => setShowShortcuts(false)} />
    </div>
  );
}

export default function App() {
  const outputPlayback = usePlaybackSession({ apiBase: api.API_BASE_URL,
    onController: api.setPlaybackController,
    onUnauthorized: () => window.dispatchEvent(new CustomEvent('radio-auth-required')) });
  useEffect(() => {
    const headers = api.getPlaybackController()?.requestHeaders();
    const sessionId = headers?.['X-Radio-Playback-Session'];
    if (sessionId) window.electron?.setPlaybackSession?.({ session_id: sessionId,
      owner_key: headers['X-Radio-Playback-Owner'] }).catch(console.error);
    return () => { window.electron?.setPlaybackSession?.(null).catch(console.error); };
  }, [outputPlayback.session?.id]);
  if (!outputPlayback.session) {
    return <div className="min-h-screen bg-gray-950 text-gray-200 flex flex-col gap-4 items-center justify-center" role="status">
      <p>{outputPlayback.error || 'Connecting listening session…'}</p>
      {outputPlayback.error && <button className="rounded border px-4 py-2" onClick={() => outputPlayback.retry().catch(() => {})}>
        {outputPlayback.retryNeeded ? 'Start a new listening session' : 'Retry connection'}
      </button>}
    </div>;
  }
  return (
    <RadioProvider>
      <AppInner outputPlayback={outputPlayback} />
    </RadioProvider>
  );
}
