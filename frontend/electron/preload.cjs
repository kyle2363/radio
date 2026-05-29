const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electron", {
  platform: process.platform,
  versions: process.versions,
  toggleMiniPlayer: () => ipcRenderer.invoke("toggle-mini-player"),
  minimizeToTray: () => ipcRenderer.invoke("minimize-to-tray"),
});
