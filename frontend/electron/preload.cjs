const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electron", {
  platform: process.platform,
  versions: process.versions,
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
});
