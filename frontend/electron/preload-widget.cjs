const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronWidget", {
  getPlaybackStatus: () => ipcRenderer.invoke('widget-playback-status'),
  onWidgetUpdate: (callback) => {
    const handler = (_event, data) => callback(data);
    ipcRenderer.on("widget-update", handler);
    return () => ipcRenderer.removeListener("widget-update", handler);
  },
});
