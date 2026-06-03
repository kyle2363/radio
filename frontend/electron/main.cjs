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
  const { screen } = require("electron");
  const { width: screenWidth, height: screenHeight } = screen.getPrimaryDisplay().workAreaSize;

  mainWindow = new BrowserWindow({
    width: screenWidth,
    height: screenHeight,
    minWidth: 440,
    minHeight: 110,
    frame: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    autoHideMenuBar: true,
  });

  const contentSize = mainWindow.getContentSize();
  const contentW = contentSize[0];
  const contentH = contentSize[1];
  console.log("CONTENT SIZE:", contentW, "x", contentH);
  console.log("WORKAREA:", screenWidth, "x", screenHeight);

mainWindow.setBounds({ x: 0, y: 0, width: screenWidth, height: screenHeight });

  if (process.env.NODE_ENV === "development" || !app.isPackaged) {
    mainWindow.loadURL("http://localhost:3000");
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  mainWindow.on("close", (e) => {
    if (tray && !app.isQuitting) {
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
    const size = 16;
    const canvas = Buffer.alloc(size * size * 4);
    const cx = size / 2, cy = size / 2, r = 5;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const idx = (y * size + x) * 4;
        const dist = Math.sqrt((x - cx) ** 2 + (y - cy) ** 2);
        if (dist <= r) {
          canvas[idx] = 74;
          canvas[idx + 1] = 187;
          canvas[idx + 2] = 120;
          canvas[idx + 3] = 255;
        } else {
          canvas[idx + 3] = 0;
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
    { label: "Heart", click: () => sendCommand("heart") },
    { label: "Thumbs Down", click: () => sendCommand("thumbs_down") },
    { type: "separator" },
    {
      label: "Toggle Mini Player",
      click: () => toggleMiniPlayer(),
    },
    {
      label: "Toggle Always On Top",
      click: () => {
        if (mainWindow) {
          const newVal = !mainWindow.isAlwaysOnTop();
          mainWindow.setAlwaysOnTop(newVal, "floating");
        }
      },
    },
    { type: "separator" },
    { label: "Show Radio", click: () => { mainWindow.show(); mainWindow.focus(); } },
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
    const prev = mainWindow._prevBounds;
    if (prev) {
      mainWindow.setBounds({ x: prev.x, y: prev.y, width: prev.width, height: prev.height });
    } else {
      mainWindow.setSize(1200, 800);
      mainWindow.center();
    }
    mainWindow.setAlwaysOnTop(false);
    mainWindow.setResizable(true);
    mainWindow.setMaximizable(true);
    mainWindow.setMinimizable(true);
    mainWindow.setMenuBarVisibility(false);
    isMiniMode = false;
  } else {
    const bounds = mainWindow.getBounds();
    mainWindow._prevBounds = { ...bounds };
    mainWindow.setBounds({ x: bounds.x, y: bounds.y, width: 440, height: 110 });
    mainWindow.setAlwaysOnTop(true, "floating");
    mainWindow.setResizable(false);
    mainWindow.setMaximizable(false);
    mainWindow.setMinimizable(true);
    mainWindow.setMenuBarVisibility(false);
    isMiniMode = true;
  }
  try {
    mainWindow.webContents.send("mini-mode-changed", isMiniMode);
  } catch (_) {}
}

async function sendCommand(cmd) {
  try {
    if (cmd === "play_pause") {
      await fetch(`${API_URL}/api/play/pause`, { method: "POST" });
    } else if (cmd === "next") {
      await fetch(`${API_URL}/api/queue/skip`, { method: "POST" });
    } else if (cmd === "stop") {
      await fetch(`${API_URL}/api/play/stop`, { method: "POST" });
    } else if (cmd === "heart") {
      await fetch(`${API_URL}/api/play/heart`, { method: "POST" });
    } else if (cmd === "thumbs_down") {
      await fetch(`${API_URL}/api/play/thumbs-down`, { method: "POST" });
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

  ipcMain.handle("toggle-mini-player", () => toggleMiniPlayer());
  ipcMain.handle("minimize-to-tray", () => mainWindow?.hide());
  ipcMain.handle("get-mini-mode", () => isMiniMode);
  ipcMain.handle("window-minimize", () => mainWindow?.minimize());
  ipcMain.handle("window-maximize", () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });
  ipcMain.handle("window-close", () => mainWindow?.close());

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
