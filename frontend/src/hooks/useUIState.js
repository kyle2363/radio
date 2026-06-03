import { useState, useCallback, useEffect } from "react";
import * as api from "../api";
import { useSharedRadio, useFetchStatus } from "./useRadioState";

export default function useUIState(fetchStatusCb) {
  const [showSettings, setShowSettings] = useState(false);
  const [showLibrary, setShowLibrary] = useState(false);
  const [currentMode, setCurrentMode] = useState("radio");
  const [talkSegmentActive, setTalkSegmentActive] = useState(null);
  const [talkGenerating, setTalkGenerating] = useState(false);
  const [ttsReady, setTtsReady] = useState(false);
  const [sleepTimer, setSleepTimer] = useState(null);

  const { addToast, removeToast } = useSharedRadio();

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

  const handleSkipFeedback = useCallback(async (reason, onDismiss) => {
    try {
      await api.submitSkipFeedback(reason);
      addToast("Thanks for the feedback!", "success");
    } catch (e) {
      addToast("Failed to submit feedback", "error");
    } finally {
      onDismiss?.();
    }
  }, [addToast]);

  const handleSleepTimer = useCallback(async (minutes, fadeSeconds = 30, switchToChill = true) => {
    try {
      await api.setSleepTimer(minutes, fadeSeconds, switchToChill);
      addToast(`Sleep timer: ${minutes} min`, "success");
    } catch (e) {
      addToast("Failed to set sleep timer", "error");
    }
  }, [addToast]);

  const handleCancelSleepTimer = useCallback(async () => {
    try {
      await api.cancelSleepTimer();
      setSleepTimer(null);
      addToast("Sleep timer cancelled", "success");
    } catch (e) {
      addToast("Failed to cancel sleep timer", "error");
    }
  }, [addToast]);

  const handleCommandSent = useCallback((response) => {
    if (response.action === "mood_shift") {
      window._moodShift = response.target_mood;
    } else if (response.action === "pause") {
      window._isPlaying = false;
    } else if (response.action === "resume") {
      window._isPlaying = true;
    } else if (response.action === "volume_up" || response.action === "volume_down" || response.action === "set_volume") {
      if (response.volume != null) window._volume = response.volume;
    } else if (response.action === "mode_switch") {
      setCurrentMode(response.mode);
    } else if (response.action === "sleep_timer") {
      setSleepTimer({ minutes: response.minutes });
    } else if (response.action === "cancel_sleep") {
      setSleepTimer(null);
    } else if (response.action === "rebuild") {
      setTalkGenerating(true);
    }
  }, []);

  const toggleTheme = useCallback(async (currentTheme, setTheme) => {
    const newTheme = currentTheme === "dark" ? "light" : "dark";
    setTheme(newTheme);
    try { await api.updateSetting("theme", newTheme); } catch (e) { console.error("Theme update failed:", e); }
  }, []);

  const handleOnboardingComplete = useCallback(() => {
    fetchStatusCb();
  }, [fetchStatusCb]);

  const handleOnboardingSkip = useCallback(() => {
    fetchStatusCb();
  }, [fetchStatusCb]);

  const handleReseedTaste = useCallback(async () => {
    try {
      await api.reseedTaste();
      addToast("Taste reseeded — pick 5 songs to re-calibrate", "success");
    } catch (e) {
      addToast("Failed to reseed taste", "error");
    }
  }, [addToast]);

  return {
    // State
    showSettings, setShowSettings,
    showLibrary, setShowLibrary,
    currentMode, setCurrentMode,
    talkSegmentActive, setTalkSegmentActive,
    talkGenerating, setTalkGenerating,
    ttsReady, setTtsReady,
    sleepTimer, setSleepTimer,
    // Actions
    handleModeToggle, handleSkipFeedback, handleSleepTimer,
    handleCancelSleepTimer, handleCommandSent, toggleTheme,
    handleOnboardingComplete, handleOnboardingSkip, handleReseedTaste,
  };
}
