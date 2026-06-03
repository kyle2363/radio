import { useState, useCallback, useRef } from "react";
import * as api from "../api";
import { useSharedRadio, useFetchStatus } from "./useRadioState";

const ARCHETYPE_DESC = {
  explorer: "Loves discovering new music",
  comfort_listener: "Prefers familiar favorites",
  deep_listener: "Listens to songs all the way through",
  mood_listener: "Chooses music based on mood",
};

export default function useMoodState(currentSongRef, fetchStatusCb) {
  const [mood, setMood] = useState(null);
  const [vibeLock, setVibeLock] = useState(null);
  const [skipStreak, setSkipStreak] = useState(0);
  const [archetype, setArchetype] = useState(null);
  const [archetypeDesc, setArchetypeDesc] = useState("");
  const [explorationLevel, setExplorationLevel] = useState(0.3);

  const { addToast } = useSharedRadio();

  // Restore mood state from fetchStatus response
  const applyStatus = useCallback((data) => {
    if (data.mood_state) setMood(data.mood_state);
    if (data.vibe_lock) setVibeLock(data.vibe_lock);
    if (data.skip_streak != null) setSkipStreak(data.skip_streak);
    if (data.archetype) {
      setArchetype(data.archetype);
      setArchetypeDesc(ARCHETYPE_DESC[data.archetype] || "Music enthusiast");
    }
  }, []);

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
  }, [vibeLock, addToast, currentSongRef]);

  const handleExplorationChange = useCallback(async (level) => {
    setExplorationLevel(level);
    try { await api.updateSetting("exploration_level", String(level)); } catch (e) { console.error("Exploration update failed:", e); }
  }, []);

  return {
    // State
    mood, vibeLock, skipStreak, archetype, archetypeDesc, explorationLevel,
    // Actions
    handleMoodSet, handleVibeLockToggle, handleExplorationChange,
    // Handlers for WebSocket
    applyStatus,
    // Setters
    setMood, setVibeLock, setSkipStreak, setArchetype, setArchetypeDesc, setExplorationLevel,
  };
}
