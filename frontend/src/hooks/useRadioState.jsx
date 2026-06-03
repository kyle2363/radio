import { useState, useEffect, useCallback, useRef } from "react";
import * as api from "../api";

const ARCHETYPE_DESC = {
  explorer: "Loves discovering new music",
  comfort_listener: "Prefers familiar favorites",
  deep_listener: "Listens to songs all the way through",
  mood_listener: "Chooses music based on mood",
};

export default function useRadioState() {
  const [currentSong, setCurrentSong] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [queue, setQueue] = useState([]);
  const [mood, setMood] = useState(null);
  const [vibeLock, setVibeLock] = useState(null);
  const [skipStreak, setSkipStreak] = useState(0);
  const [archetype, setArchetype] = useState(null);
  const [archetypeDesc, setArchetypeDesc] = useState("");
  const [loading, setLoading] = useState(true);
  const [positionMs, setPositionMs] = useState(0);
  const [durationMs, setDurationMs] = useState(0);
  const [whyReason, setWhyReason] = useState("");
  const [explorationLevel, setExplorationLevel] = useState(0.3);
  const [volume, setVolume] = useState(70);
  const [showSettings, setShowSettings] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [currentMode, setCurrentMode] = useState("radio");
  const [showLibrary, setShowLibrary] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [queueRebuilding, setQueueRebuilding] = useState(false);
  const [audioRms, setAudioRms] = useState(0.5);
  const [theme, setTheme] = useState("dark");
  const [sectionSpacing, setSectionSpacing] = useState("10");
  const [ttsReady, setTtsReady] = useState(false);
  const [talkSegmentActive, setTalkSegmentActive] = useState(null);
  const [talkGenerating, setTalkGenerating] = useState(false);

  const currentSongRef = useRef(currentSong);
  currentSongRef.current = currentSong;

  const addToast = useCallback((message, type = "error") => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message, type }]);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await api.fetchPlayStatus();
      setCurrentSong(data.current_song);
      setIsPlaying(data.is_playing);
      setMood(data.mood_state);
      setVibeLock(data.vibe_lock);
      // NOTE: queue is NOT set here to avoid overwriting full queue 
      // with the 4-item preview from play/status. Queue is updated 
      // by WebSocket events and the dedicated queue poll.
      setPositionMs(data.position_ms || 0);
      setDurationMs(data.duration_ms || 0);
      setSkipStreak(data.skip_streak || 0);
      if (data.current_mode) setCurrentMode(data.current_mode);
      if (data.audio_rms != null) setAudioRms(data.audio_rms);
      if (data.archetype) {
        setArchetype(data.archetype);
        setArchetypeDesc(ARCHETYPE_DESC[data.archetype] || "Music enthusiast");
      }
      if (data.talk_segment_active) {
        setTalkSegmentActive(data.talk_segment_active);
      } else if (data.current_song) {
        // Only clear talk segment if we actually have a song playing
        setTalkSegmentActive(null);
      }
    } catch (e) {
      console.error("Failed to fetch status:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  const checkOnboarding = useCallback(async () => {
    try {
      const data = await api.fetchSettings();
      if (data.onboarding_complete !== "true") setShowOnboarding(true);
      if (data.theme) setTheme(data.theme);
      if (data.section_spacing) setSectionSpacing(data.section_spacing);
    } catch (e) {
      console.error("Failed to check onboarding:", e);
    }
  }, []);

  const wsRetryRef = useRef(0);

  const connectWebSocket = useCallback(() => {
    const ws = new window.WebSocket(api.WS_URL);
    window._ws = ws;
    let wsConnectTimeout = setTimeout(() => {
      if (ws.readyState !== window.WebSocket.OPEN) {
        ws.close();
      }
    }, 10000);

    ws.onopen = () => {
      clearTimeout(wsConnectTimeout);
      wsRetryRef.current = 0;
      ws._pingInterval = setInterval(() => {
        if (ws.readyState === window.WebSocket.OPEN) ws.send("ping");
      }, 30000);
    };

    ws.onmessage = (event) => {
      if (event.data === "pong") return;
      try {
        const data = JSON.parse(event.data);
        if (data.event === "song_ended") {
          fetchStatus();
          setTalkSegmentActive(null);
        } else if (data.event === "queue_updated") {
          setQueue(data.queue || []);
          setQueueRebuilding(false);
          if (data.queue && currentSongRef.current) {
            const item = data.queue.find((q) => q.song_id === currentSongRef.current.id);
            if (item?.reason) setWhyReason(item.reason);
          }
        } else if (data.event === "queue_rebuilding") {
          setQueueRebuilding(data.rebuilding);
          if (!data.rebuilding) setTalkGenerating(false);
        } else if (data.event === "mood_changed") {
          setMood(data.state);
        } else if (data.event === "vibe_lock_activated") {
          setVibeLock({ active: true, song_id: data.song_id });
        } else if (data.event === "vibe_lock_deactivated") {
          setVibeLock(null);
        } else if (data.event === "song_hearted" || data.event === "song_replayed") {
          fetchStatus();
        } else if (data.event === "talk_segment_started") {
          setTalkSegmentActive({
            segment_type: data.segment_type,
            reason: data.reason,
            persona_id: data.persona_id,
            duration_secs: data.duration_secs,
          });
        } else if (data.event === "taste_drift") {
          addToast(`Your taste is shifting (drift: ${Math.round(data.drift * 100)}%)`, "info");
        }
      } catch (e) {
        console.error("WebSocket message parse failed:", e);
      }
    };

    ws.onclose = () => {
      if (ws._pingInterval) clearInterval(ws._pingInterval);
      const delay = Math.min(30000, 1000 * Math.pow(2, wsRetryRef.current));
      wsRetryRef.current += 1;
      setTimeout(connectWebSocket, delay);
    };
  }, [fetchStatus]);

  // Init: fetch status, check onboarding, connect WebSocket, start polls
  useEffect(() => {
    fetchStatus();
    checkOnboarding();
    connectWebSocket();

    const poll = setInterval(async () => {
      try {
        const data = await api.fetchQueueStatus();
        setQueue(data.queue || []);
        setQueueRebuilding(data.rebuilding || false);
      } catch (e) { /* polling — expected on startup */ }
    }, 10000);

    const statusPoll = setInterval(async () => {
      try {
        const data = await api.fetchPlayStatus();
        setPositionMs(data.position_ms || 0);
        setDurationMs(data.duration_ms || 0);
        setCurrentSong((prev) => {
          const next = data.current_song;
          if (prev?.id !== next?.id) return next;
          return prev;
        });
        setIsPlaying(data.is_playing);
        if (data.audio_rms != null) setAudioRms(data.audio_rms);
        setSkipStreak(data.skip_streak || 0);
      } catch (e) { /* polling — expected when backend frozen */ }
    }, 3000);

    const ttsPoll = setInterval(async () => {
      try {
        const data = await api.fetchTtsStatus();
        setTtsReady(data.next_song_ready);
        setTalkGenerating(queueRebuilding && data.talk_segments_enabled);
      } catch (e) { /* polling — expected on startup */ }
    }, 5000);

    return () => {
      if (window._ws) window._ws.close();
      clearInterval(poll);
      clearInterval(statusPoll);
      clearInterval(ttsPoll);
    };
  }, []);

  // Theme
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // Handlers
  const handleVolumeChange = useCallback(async (vol) => {
    setVolume(vol);
    try { await api.setVolume(vol); } catch (e) { console.error("Volume update failed:", e); }
  }, []);

  const handleExplorationChange = useCallback(async (level) => {
    setExplorationLevel(level);
    try { await api.updateSetting("exploration_level", String(level)); } catch (e) { console.error("Exploration update failed:", e); }
  }, []);

  const handleSeek = useCallback(async (seekMs) => {
    try {
      await api.seekTo(seekMs);
      setPositionMs(seekMs);
    } catch (e) {
      addToast("Failed to seek", "error");
    }
  }, [addToast]);

  const handlePlay = useCallback(async () => {
    try {
      const res = await api.resumeSong();
      if (res.status === "playing") {
        fetchStatus();
        addToast("Now playing", "success");
      } else if (res.status === "stopped") {
        addToast("No songs available", "error");
      }
    } catch (e) {
      addToast("Failed to play", "error");
    }
  }, [fetchStatus, addToast]);

  const handlePause = useCallback(async () => {
    try {
      await api.pauseSong();
      fetchStatus();
      addToast("Paused", "success");
    } catch (e) {
      addToast("Failed to pause", "error");
    }
  }, [fetchStatus, addToast]);

  const handleNext = useCallback(async () => {
    try {
      const res = await api.skipSong();
      fetchStatus();
      addToast(res.mood_shift ? "Mood shifted — current song will finish" : "Skipped", "success");
    } catch (e) {
      addToast("Failed to skip", "error");
    }
  }, [fetchStatus, addToast]);

  const handleHeart = useCallback(async () => {
    if (!currentSongRef.current) {
      addToast("No song playing", "error");
      return;
    }
    try {
      await api.heartSong();
      fetchStatus();
      addToast("Hearted", "success");
    } catch (e) {
      addToast("Failed to heart", "error");
    }
  }, [fetchStatus, addToast]);

  const handleThumbsDown = useCallback(async () => {
    if (!currentSongRef.current) {
      addToast("No song playing", "error");
      return;
    }
    try {
      await api.thumbsDown();
      fetchStatus();
      addToast("Thumbs down", "success");
    } catch (e) {
      addToast("Failed to thumbs down", "error");
    }
  }, [fetchStatus, addToast]);

  const handleReplay = useCallback(async () => {
    if (!currentSongRef.current) {
      addToast("No song playing", "error");
      return;
    }
    try {
      await api.replaySong();
      fetchStatus();
      addToast("Replaying", "success");
    } catch (e) {
      addToast("Failed to replay", "error");
    }
  }, [fetchStatus, addToast]);

  const handleClearQueue = useCallback(async () => {
    try {
      await api.clearQueue();
      setQueue([]);
      addToast("Queue cleared", "success");
    } catch (e) {
      addToast("Failed to clear queue", "error");
    }
  }, [addToast]);

  const handleMoodSet = useCallback(async (moodState) => {
    try {
      await api.setMood(moodState);
      setMood(moodState);
      addToast(`Mood: ${moodState}`, "success");
    } catch (e) {
      addToast("Failed to set mood", "error");
    }
  }, [addToast]);

  const handleVibeLockToggle = useCallback(async () => {
    try {
      if (vibeLock?.active) {
        await api.deactivateVibeLock();
        setVibeLock(null);
        addToast("Vibe lock deactivated", "success");
      } else if (currentSongRef.current) {
        await api.activateVibeLock(currentSongRef.current.id);
        setVibeLock({ active: true, song_id: currentSongRef.current.id });
        addToast(`Vibe locked: ${currentSongRef.current.title}`, "success");
      }
    } catch (e) {
      addToast("Failed to toggle vibe lock", "error");
    }
  }, [vibeLock, addToast]);

  const handleModeToggle = useCallback(async () => {
    try {
      const newMode = currentMode === "radio" ? "normal" : "radio";
      await api.toggleMode(newMode);
      setCurrentMode(newMode);
      addToast(`Switched to ${newMode} mode`, "success");
    } catch (e) {
      addToast("Failed to toggle mode", "error");
    }
  }, [currentMode, addToast]);

  const handleCommandSent = useCallback((response) => {
    if (response.action === "mood_shift") setMood(response.target_mood);
  }, []);

  const toggleTheme = useCallback(async () => {
    const newTheme = theme === "dark" ? "light" : "dark";
    setTheme(newTheme);
    try { await api.updateSetting("theme", newTheme); } catch (e) { console.error("Theme update failed:", e); }
  }, [theme]);

  const handleOnboardingComplete = useCallback(() => {
    setShowOnboarding(false);
    fetchStatus();
  }, [fetchStatus]);

  const handleOnboardingSkip = useCallback(() => {
    setShowOnboarding(false);
    fetchStatus();
  }, [fetchStatus]);

  return {
    // State
    currentSong, isPlaying, queue, mood, vibeLock, skipStreak,
    archetype, archetypeDesc, loading, positionMs, durationMs,
    whyReason, explorationLevel, volume, showSettings, showOnboarding,
    currentMode, showLibrary, toasts, queueRebuilding, theme, sectionSpacing,
    ttsReady, audioRms, talkSegmentActive, talkGenerating,
    // Actions
    addToast, removeToast, handleVolumeChange, handleExplorationChange,
    handleSeek, handlePlay, handlePause, handleNext, handleHeart,
    handleThumbsDown, handleReplay, handleClearQueue, handleMoodSet,
    handleVibeLockToggle, handleModeToggle, handleCommandSent,
    toggleTheme, handleOnboardingComplete, handleOnboardingSkip,
    // Setters for modals
    setShowSettings, setShowOnboarding, setShowLibrary, setSectionSpacing,
  };
}
