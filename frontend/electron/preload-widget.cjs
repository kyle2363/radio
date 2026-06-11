const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronWidget", {
  onWidgetUpdate: (callback) => {
    const handler = (_event, data) => callback(data);
    ipcRenderer.on("widget-update", handler);
    return () => ipcRenderer.removeListener("widget-update", handler);
  },
});
