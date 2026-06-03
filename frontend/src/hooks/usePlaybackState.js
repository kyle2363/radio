import { useState, useEffect, useCallback, useRef } from "react";
import * as api from "../api";
import { useSharedRadio, useFetchStatus } from "./useRadioState";

export default function usePlaybackState() {
  const [currentSong, setCurrentSong] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [positionMs, setPositionMs] = useState(0);
  const [durationMs, setDurationMs] = useState(0);
  const [volume, setVolume] = useState(70);
  const [audioRms, setAudioRms] = useState(0.5);
  const [undoState, setUndoState] = useState(null);
  const [showSkipFeedback, setShowSkipFeedback] = useState(false);
  const [loading, setLoading] = useState(true);

  const currentSongRef = useRef(currentSong);
  currentSongRef.current = currentSong;
  const undoStateRef = useRef(undoState);
  undoStateRef.current = undoState;

  const { addToast } = useSharedRadio();
  const fetchStatus = useFetchStatus();

  const applyPlaybackStatus = useCallback((data) => {
    if (!data) return;
    setCurrentSong(data.current_song);
    setIsPlaying(data.is_playing);
    setPositionMs(data.position_ms || 0);
    setDurationMs(data.duration_ms || 0);
    if (data.audio_rms != null) setAudioRms(data.audio_rms);
    if (data.skip_streak != null) {
      window._skipStreak = data.skip_streak;
    }
    if (data.show_skip_feedback) {
      setShowSkipFeedback(true);
    }
  }, []);

  const refreshPlaybackStatus = useCallback(async () => {
    const data = await fetchStatus();
    applyPlaybackStatus(data);
    return data;
  }, [fetchStatus, applyPlaybackStatus]);

  useEffect(() => {
    refreshPlaybackStatus().then((data) => {
      setLoading(false);
    }).catch(() => {
      setLoading(false);
    });
  }, [refreshPlaybackStatus]);

  const handleVolumeChange = useCallback(async (vol) => {
    setVolume(vol);
    try { await api.setVolume(vol); } catch (e) { console.error("Volume update failed:", e); }
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
        refreshPlaybackStatus();
        addToast("Now playing", "success");
      } else if (res.status === "stopped") {
        addToast("No songs available", "error");
      }
    } catch (e) {
      addToast("Failed to play", "error");
    }
  }, [refreshPlaybackStatus, addToast]);

  const handlePause = useCallback(async () => {
    try {
      await api.pauseSong();
      refreshPlaybackStatus();
      addToast("Paused", "success");
    } catch (e) {
      addToast("Failed to pause", "error");
    }
  }, [refreshPlaybackStatus, addToast]);

  const doUndo = useCallback(async () => {
    const state = undoStateRef.current;
    if (!state) return;
    try {
      let res;
      if (state.action_type === "skip") {
        res = await api.undoSkip();
      } else if (state.action_type === "thumbs_down") {
        res = await api.undoThumbsDown();
      }
      setUndoState(null);
      if (res?.success) {
        refreshPlaybackStatus();
      } else {
        addToast(res?.error || "Undo failed", "error");
      }
    } catch (e) {
      setUndoState(null);
      addToast("Failed to undo", "error");
    }
  }, [refreshPlaybackStatus, addToast]);

  const handleNext = useCallback(async () => {
    try {
      const res = await api.skipSong();
      refreshPlaybackStatus();
      if (res.undo?.can_undo) {
        setUndoState(res.undo);
        setTimeout(() => setUndoState(null), res.undo.expires_in_ms || 10000);
        addToast("Skipped", "action", "Undo", doUndo);
      } else {
        addToast(res.mood_shift ? "Mood shifted — current song will finish" : "Skipped", "success");
      }
      if (res.show_feedback) {
        window._showSkipFeedback = true;
      }
    } catch (e) {
      addToast("Failed to skip", "error");
    }
  }, [refreshPlaybackStatus, addToast, doUndo]);

  const handleHeart = useCallback(async () => {
    if (!currentSongRef.current) {
      addToast("No song playing", "error");
      return;
    }
    try {
      await api.heartSong();
      refreshPlaybackStatus();
      addToast("Hearted", "success");
    } catch (e) {
      addToast("Failed to heart", "error");
    }
  }, [refreshPlaybackStatus, addToast]);

  const handleThumbsDown = useCallback(async () => {
    if (!currentSongRef.current) {
      addToast("No song playing", "error");
      return;
    }
    try {
      const res = await api.thumbsDown();
      refreshPlaybackStatus();
      if (res.undo?.can_undo) {
        setUndoState(res.undo);
        setTimeout(() => setUndoState(null), res.undo.expires_in_ms || 10000);
        addToast("Thumbs down", "action", "Undo", doUndo);
      } else {
        addToast("Thumbs down", "success");
      }
    } catch (e) {
      addToast("Failed to thumbs down", "error");
    }
  }, [refreshPlaybackStatus, addToast, doUndo]);

  const handleReplay = useCallback(async () => {
    if (!currentSongRef.current) {
      addToast("No song playing", "error");
      return;
    }
    try {
      await api.replaySong();
      refreshPlaybackStatus();
      addToast("Replaying", "success");
    } catch (e) {
      addToast("Failed to replay", "error");
    }
  }, [refreshPlaybackStatus, addToast]);

  const handleClearQueue = useCallback(async () => {
    try {
      await api.clearQueue();
      window._clearQueue = true;
      addToast("Queue cleared", "success");
    } catch (e) {
      addToast("Failed to clear queue", "error");
    }
  }, [addToast]);

  return {
    // State
    currentSong, isPlaying, positionMs, durationMs, volume, audioRms,
    undoState, showSkipFeedback, loading,
    // Refs for inter-hook sync
    currentSongRef,
    // Actions
    handleVolumeChange, handleSeek, handlePlay, handlePause, handleNext,
    handleHeart, handleThumbsDown, handleReplay, handleClearQueue,
    doUndo, applyPlaybackStatus, refreshPlaybackStatus, setUndoState, setShowSkipFeedback,
    // Setters
    setCurrentSong, setIsPlaying, setPositionMs, setDurationMs, setAudioRms,
  };
}
