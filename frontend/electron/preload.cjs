const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electron", {
  platform: process.platform,
  versions: process.versions,

  // Window controls
  toggleMiniPlayer: () => ipcRenderer.invoke("toggle-mini-player"),
  minimizeToTray: () => ipcRenderer.invoke("minimize-to-tray"),
  getMiniMode: () => ipcRenderer.invoke("get-mini-mode"),
  onMiniModeChange: (callback) => {
    const handler = (_event, isMini) => callback(isMini);
    ipcRenderer.on("mini-mode-changed", handler);
    return () => ipcRenderer.removeListener("mini-mode-changed", handler);
  },
  windowMinimize: () => ipcRenderer.invoke("window-minimize"),
  windowMaximize: () => ipcRenderer.invoke("window-maximize"),
  windowClose: () => ipcRenderer.invoke("window-close"),

  // Tray info updates (called from renderer on song/mood/state changes)
  updateTraySong: (artist, title) => ipcRenderer.invoke("update-tray-song", artist, title),
  updateTrayMood: (mood) => ipcRenderer.invoke("update-tray-mood", mood),
  updateTrayVibeLock: (active) => ipcRenderer.invoke("update-tray-vibe-lock", active),
  updateTraySkipStreak: (streak) => ipcRenderer.invoke("update-tray-skip-streak", streak),
  updateTrayPlaying: (playing) => ipcRenderer.invoke("update-tray-playing", playing),

  // Taskbar progress bar
  updateTaskbarProgress: (positionMs, durationMs) => ipcRenderer.invoke("update-taskbar-progress", positionMs, durationMs),

  // System notifications
  updateNotification: (artist, title) => ipcRenderer.invoke("update-notification", artist, title),

  // Start on login (auto-launch with Windows)
  setStartOnLogin: (enabled) => ipcRenderer.invoke("set-start-on-login", enabled),
  getStartOnLogin: () => ipcRenderer.invoke("get-start-on-login"),

  // Hotkey bindings — sync from settings
  reloadHotkeys: (bindings) => ipcRenderer.invoke("reload-hotkeys", bindings),

  // Now Playing widget (always-on-top mini overlay)
  showWidget: () => ipcRenderer.invoke("show-widget"),
  hideWidget: () => ipcRenderer.invoke("hide-widget"),
  updateWidget: (artist, title) => ipcRenderer.invoke("update-widget", artist, title),

  // Settings sync to main process (power monitor, notifications, etc.)
  setSetting: (key, value) => ipcRenderer.invoke("set-setting", key, value),
});
