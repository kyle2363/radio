import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from "react";
import * as api from "../api";
import usePoll from "./usePoll";
import { createPlaybackFacade } from "../api/sessionBridge.js";

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
  const fetchStatusRef = useRef(fetchStatusCb);
  const facadeRef = useRef(null);
  const mountedRef = useRef(false);
  fetchStatusRef.current = fetchStatusCb;

  const connectWebSocket = useCallback(() => {
    if (facadeRef.current?.readyState === 1) return facadeRef.current;
    const facade = createPlaybackFacade({
      fetchStatus: () => fetchStatusRef.current?.(),
      onConnected: connected => { if (mountedRef.current) setWsConnected(connected); },
      electron: window.electron,
    });
    facadeRef.current = facade;
    window._ws = facade;
    return facade;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const facade = connectWebSocket();
    return () => {
      mountedRef.current = false;
      facade.close();
      if (facadeRef.current === facade) facadeRef.current = null;
      if (window._ws === facade) delete window._ws;
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
