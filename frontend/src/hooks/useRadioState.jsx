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
      setWsConnected(true);
      ws._pingInterval = setInterval(() => {
        if (ws.readyState === window.WebSocket.OPEN) {
          setWsConnected(true);
          ws.send("ping");
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      if (event.data === "pong") return;
      try {
        const data = JSON.parse(event.data);
        if (data.event === "song_started") {
          fetchStatusRef.current?.();
          ws._songStartedHandler?.();
        } else if (data.event === "song_ended") {
          fetchStatusRef.current?.();
        } else if (data.event === "queue_updated") {
          ws._queueHandler?.(data);
        } else if (data.event === "queue_rebuilding") {
          ws._rebuildHandler?.(data);
        } else if (data.event === "mood_changed") {
          ws._moodHandler?.(data.state || data.mood);
        } else if (data.event === "position_update") {
          ws._positionHandler?.(data);
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
        }
      } catch (e) {
        console.error("WebSocket message parse failed:", e);
      }
    };

    ws.onclose = () => {
      clearTimeout(wsConnectTimeout);
      if (ws._pingInterval) clearInterval(ws._pingInterval);
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
      if (data.right_rail_width) setRightRailWidth(parseInt(data.right_rail_width) || 255);
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
