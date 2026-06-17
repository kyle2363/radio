import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from "react";
import * as api from "../api";
import usePoll from "./usePoll";

const ARCHETYPE_DESC = {
  explorer: "Loves discovering new music",
  comfort_listener: "Prefers familiar favorites",
  deep_listener: "Listens to songs all the way through",
  mood_listener: "Chooses music based on mood",
};

export const RadioContext = createContext(null);

export function RadioProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const toastIdRef = useRef(0);
  const pollController = usePoll();

  const addToast = useCallback((message, type = "error", actionLabel = null, onAction = null) => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, message, type, actionLabel, onAction }]);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const value = { toasts, addToast, removeToast, pollController };

  return (
    <RadioContext.Provider value={value}>
      {children}
    </RadioContext.Provider>
  );
}

export function useSharedRadio() {
  const ctx = useContext(RadioContext);
  if (!ctx) throw new Error("useSharedRadio must be used within RadioProvider");
  return ctx;
}

export function useFetchStatus() {
  const { addToast } = useSharedRadio();
  return useCallback(async () => {
    try {
      const data = await api.fetchPlayStatus();
      return data;
    } catch (e) {
      console.error("Failed to fetch status:", e);
      return null;
    }
  }, [addToast]);
}

export function useWebSocket(fetchStatusCb) {
  const [wsConnected, setWsConnected] = useState(false);
  const wsRetryRef = useRef(0);
  const wsReconnectTimerRef = useRef(null);
  const wsMountedRef = useRef(true);
  const fetchStatusRef = useRef(fetchStatusCb);
  const wsLastPongRef = useRef(Date.now());
  const { addToast } = useSharedRadio();

  useEffect(() => {
    fetchStatusRef.current = fetchStatusCb;
  }, [fetchStatusCb]);

  const connectWebSocket = useCallback(() => {
    wsMountedRef.current = true;
    if (wsReconnectTimerRef.current) {
      clearTimeout(wsReconnectTimerRef.current);
      wsReconnectTimerRef.current = null;
    }

    if (
      window._ws?.readyState === window.WebSocket.OPEN ||
      window._ws?.readyState === window.WebSocket.CONNECTING
    ) {
      return window._ws;
    }

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
      wsLastPongRef.current = Date.now();
      setWsConnected(true);
      fetchStatusRef.current?.();
      ws._pingInterval = setInterval(() => {
        if (ws.readyState === window.WebSocket.OPEN) {
          setWsConnected(true);
          ws.send("ping");
        }
      }, 30000);
      // Pong watchdog: verify we get a response within 35s
      ws._pongWatchdog = setInterval(() => {
        if (ws.readyState !== window.WebSocket.OPEN) return;
        if (Date.now() - wsLastPongRef.current > 35000) {
          console.warn("WebSocket pong timeout, reconnecting");
          ws.close();
        }
      }, 5000);
    };

    ws.onmessage = (event) => {
      if (event.data === "pong") {
        wsLastPongRef.current = Date.now();
        return;
      }
      try {
        const data = JSON.parse(event.data);
        if (data.event === "song_started") {
          // Fire notification directly from status response
          fetchStatusRef.current?.().then((status) => {
            if (status?.current_song && window.electron) {
              window.electron.updateNotification(
                status.current_song.artist || "",
                status.current_song.title || ""
              );
            }
          });
          ws._songStartedHandler?.(data);
        } else if (data.event === "song_ended") {
          fetchStatusRef.current?.();
        } else if (data.event === "song_skipped") {
          fetchStatusRef.current?.();
          if (window.electron && data.song_id != null) {
            const s = data.skip_streak;
            if (s != null) {
              window.electron.updateTraySkipStreak(s);
            }
          }
        } else if (data.event === "position_update") {
          // Update Electron taskbar progress bar
          if (window.electron && data.position_ms != null) {
            window.electron.updateTaskbarProgress(data.position_ms, data.duration_ms || 0);
          }
          ws._positionHandler?.(data);
        } else if (data.event === "queue_updated") {
          ws._queueHandler?.(data);
        } else if (data.event === "queue_rebuilding") {
          ws._rebuildHandler?.(data);
        } else if (data.event === "mood_changed") {
          ws._moodHandler?.(data.state || data.mood);
        } else if (data.event === "vibe_lock_activated") {
          ws._vibeLockHandler?.({ active: true, song_id: data.song_id });
        } else if (data.event === "vibe_lock_deactivated") {
          ws._vibeLockHandler?.(null);
        } else if (data.event === "song_hearted" || data.event === "song_replayed") {
          fetchStatusRef.current?.();
        } else if (data.event === "talk_segment_started") {
          ws._talkHandler?.({
            segment_type: data.segment_type,
            reason: data.reason,
            persona_id: data.persona_id,
            duration_secs: data.duration_secs,
            next_song_id: data.next_song_id,
            next_song_artist: data.next_song_artist,
            next_song_title: data.next_song_title,
          });
        } else if (data.event === "taste_drift") {
          // handled by toast consumer
        } else if (data.event === "song_undo") {
          fetchStatusRef.current?.();
        } else if (data.event === "sleep_timer_expired") {
          ws._sleepTimerHandler?.(null);
        } else if (data.event === "play_mode_changed") {
          ws._playModeHandler?.(data.mode);
        } else if (data.event === "recommendation_filters_changed") {
          ws._recFilterHandler?.(data);
        } else if (data.event === "folder_filter_changed") {
          ws._recFilterHandler?.(data);
        } else if (data.event === "error") {
          if (data.message) addToast(data.message, "error");
        }
      } catch (e) {
        console.error("WebSocket message parse failed:", e);
      }
    };

    ws.onclose = () => {
      clearTimeout(wsConnectTimeout);
      if (ws._pingInterval) clearInterval(ws._pingInterval);
      if (ws._pongWatchdog) clearInterval(ws._pongWatchdog);
      setWsConnected(false);
      if (!wsMountedRef.current || window._ws !== ws) return;
      const delay = Math.min(30000, 1000 * Math.pow(2, wsRetryRef.current));
      wsRetryRef.current += 1;
      wsReconnectTimerRef.current = setTimeout(() => {
        if (wsMountedRef.current) {
          connectWebSocket();
        }
      }, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    return ws;
  }, []);

  useEffect(() => {
    connectWebSocket();
    return () => {
      wsMountedRef.current = false;
      if (wsReconnectTimerRef.current) {
        clearTimeout(wsReconnectTimerRef.current);
        wsReconnectTimerRef.current = null;
      }
      if (window._ws) {
        window._ws.onmessage = null;
        window._ws.onopen = null;
        window._ws.onclose = null;
        window._ws.onerror = null;
        window._ws.close();
      }
    };
  }, [connectWebSocket]);

  return { wsConnected, connectWebSocket };
}

export function useOnboarding() {
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [theme, setTheme] = useState("dark");
  const [sectionSpacing, setSectionSpacing] = useState("10");
  const [rightRailWidth, setRightRailWidth] = useState(255);

  const checkOnboarding = useCallback(async () => {
    try {
      const data = await api.fetchSettings();
      if (data.onboarding_complete !== "true") setShowOnboarding(true);
      if (data.theme) setTheme(data.theme);
      if (data.section_spacing) setSectionSpacing(data.section_spacing);
      if (data.right_rail_width) {
        const w = parseInt(data.right_rail_width) || 255;
        setRightRailWidth(w);
        document.documentElement.style.setProperty("--right-rail-width", w + "px");
      }
      if (data.start_minimized === "true" && window.electron?.minimizeToTray) {
        window.electron.minimizeToTray();
      }
      if (data.mini_player_enabled != null && window.electron?.setSetting) {
        window.electron.setSetting("mini_player_enabled", data.mini_player_enabled);
      }
      if (data.widget_enabled != null && window.electron?.setSetting) {
        window.electron.setSetting("widget_enabled", data.widget_enabled);
      }
      if (data.accent_color) {
        const root = document.documentElement;
        root.style.setProperty("--user-accent", data.accent_color);
        root.style.setProperty("--user-accent-hover", data.accent_color + "cc");
        root.style.setProperty("--user-accent-muted", data.accent_color + "21");
      }
    } catch (e) {
      console.error("Failed to check onboarding:", e);
    }
  }, []);

  return {
    showOnboarding, setShowOnboarding,
    theme, setTheme,
    sectionSpacing, setSectionSpacing,
    rightRailWidth, setRightRailWidth,
    checkOnboarding,
  };
}
