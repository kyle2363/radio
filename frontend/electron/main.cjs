const { app, BrowserWindow, ipcMain, Tray, Menu, globalShortcut, nativeImage } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");

let mainWindow;
let uvicornProcess;
let tray;
let isMiniMode = false;
const API_URL = "http://localhost:8765";

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    autoHideMenuBar: true,
  });

  if (process.env.NODE_ENV === "development" || !app.isPackaged) {
    mainWindow.loadURL("http://localhost:3000");
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  mainWindow.on("close", (e) => {
    // Minimize to tray instead of quitting
    if (tray) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

function createTray() {
  const iconPath = path.join(__dirname, "tray-icon.png");
  let icon;
  if (fs.existsSync(iconPath)) {
    icon = iconPath;
  } else {
    // Generate a 16x16 green circle programmatically
    const size = 16;
    const canvas = Buffer.alloc(size * size * 4);
    const cx = size / 2, cy = size / 2, r = 5;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const idx = (y * size + x) * 4;
        const dist = Math.sqrt((x - cx) ** 2 + (y - cy) ** 2);
        if (dist <= r) {
          canvas[idx] = 74;       // R
          canvas[idx + 1] = 187;  // G
          canvas[idx + 2] = 120;  // B
          canvas[idx + 3] = 255;  // A
        } else {
          canvas[idx + 3] = 0;    // transparent
        }
      }
    }
    icon = nativeImage.createFromBuffer(canvas, { width: size, height: size });
  }

  tray = new Tray(icon);
  tray.setToolTip("Radio");

  const contextMenu = Menu.buildFromTemplate([
    { label: "Play / Pause", click: () => sendCommand("play_pause") },
    { label: "Next", click: () => sendCommand("next") },
    { type: "separator" },
    { label: "Show Radio", click: () => { mainWindow.show(); mainWindow.focus(); } },
    { label: "Mini Player", click: () => toggleMiniPlayer() },
    { type: "separator" },
    { label: "Quit", click: () => { app.isQuitting = true; app.quit(); } },
  ]);

  tray.setContextMenu(contextMenu);
  tray.on("double-click", () => {
    mainWindow.show();
    mainWindow.focus();
  });
}

function toggleMiniPlayer() {
  if (!mainWindow) return;

  if (isMiniMode) {
    // Restore to full size
    mainWindow.setSize(1200, 800);
    mainWindow.center();
    isMiniMode = false;
  } else {
    // Shrink to mini player
    mainWindow.setSize(320, 200);
    mainWindow.setAlwaysOnTop(true, "floating");
    isMiniMode = true;
  }
}

async function sendCommand(cmd) {
  try {
    if (cmd === "play_pause") {
      await fetch(`${API_URL}/api/play/pause`, { method: "POST" });
    } else if (cmd === "next") {
      await fetch(`${API_URL}/api/queue/skip`, { method: "POST" });
    } else if (cmd === "stop") {
      await fetch(`${API_URL}/api/play/next`, { method: "POST" });
    }
  } catch (e) {
    console.error("Command failed:", e);
  }
}

function registerGlobalShortcuts() {
  globalShortcut.register("MediaPlayPause", () => sendCommand("play_pause"));
  globalShortcut.register("MediaNextTrack", () => sendCommand("next"));
  globalShortcut.register("MediaPreviousTrack", () => sendCommand("play_pause"));
  globalShortcut.register("MediaStop", () => sendCommand("stop"));
}

function startBackend() {
  const venvPython = path.join(
    __dirname, "..", "..", "backend", "venv",
    process.platform === "win32" ? "Scripts" : "bin",
    process.platform === "win32" ? "python.exe" : "python"
  );

  const useVenv = fs.existsSync(venvPython);
  const python = useVenv ? venvPython : (process.platform === "win32" ? "python" : "python3");
  const serverScript = path.join(__dirname, "..", "..", "backend", "main.py");

  uvicornProcess = spawn(python, [serverScript], {
    cwd: path.join(__dirname, "..", "..", "backend"),
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });

  uvicornProcess.stdout.on("data", (data) => console.log(`[Backend] ${data}`));
  uvicornProcess.stderr.on("data", (data) => console.error(`[Backend] ${data}`));
  uvicornProcess.on("close", (code) => console.log(`[Backend] Process exited with code ${code}`));
}

app.whenReady().then(() => {
  startBackend();
  createWindow();
  createTray();
  registerGlobalShortcuts();

  // IPC handlers for renderer
  ipcMain.handle("toggle-mini-player", () => toggleMiniPlayer());
  ipcMain.handle("minimize-to-tray", () => mainWindow?.hide());

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (uvicornProcess) uvicornProcess.kill();
  globalShortcut.unregisterAll();
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", () => {
  if (uvicornProcess) uvicornProcess.kill();
  globalShortcut.unregisterAll();
});
