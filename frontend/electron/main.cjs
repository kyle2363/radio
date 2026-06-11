const { app, BrowserWindow, ipcMain, Tray, Menu, globalShortcut, nativeImage, Notification, powerMonitor, powerSaveBlocker } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");

let mainWindow;
let uvicornProcess;
let tray;
let isMiniMode = false;
const API_URL = "http://localhost:8765";

// ── State tracking ────────────────────────────────────────────
let currentSong = { artist: "", title: "" };
let currentMood = "neutral";
let vibeLockActive = false;
let skipStreak = 0;
let isPlaying = false;
let wasPlayingBeforeLock = false;
let lastNotifyTime = 0;
let positionMs = 0;
let durationMs = 0;
let registeredHotkeys = [];
let powerSaveBlockerId = null;
let notifyOnSongChange = false;
let widgetEnabled = false;

// ── Window State Persistence ─────────────────────────────────
function getWindowStatePath() {
  return path.join(app.getPath("userData"), "window-state.json");
}

function saveWindowState() {
  if (!mainWindow) return;
  try {
    const bounds = mainWindow.getBounds();
    const isMaximized = mainWindow.isMaximized();
    fs.writeFileSync(getWindowStatePath(), JSON.stringify({ ...bounds, isMaximized }));
  } catch (e) { /* silent */ }
}

function loadWindowState() {
  try {
    const data = fs.readFileSync(getWindowStatePath(), "utf-8");
    return JSON.parse(data);
  } catch { return null; }
}
let autoPauseOnLock = true;
let autoResumeOnUnlock = true;
let miniPlayerEnabled = true;
let hotkeyConfig = {
  alt_h: "heart",
  alt_d: "thumbs_down",
  alt_v: "vibe_lock",
  alt_1: "mood:chill",
  alt_2: "mood:energetic",
  alt_3: "mood:focus",
  alt_4: "mood:melancholy",
  alt_5: "mood:discovery",
  alt_up: "volume_up",
  alt_down: "volume_down",
};

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

  // Restore saved window bounds
  const savedState = loadWindowState();
  if (savedState && savedState.width && savedState.height) {
    mainWindow.setBounds({
      x: Math.max(0, savedState.x ?? 0),
      y: Math.max(0, savedState.y ?? 0),
      width: Math.max(440, savedState.width),
      height: Math.max(110, savedState.height),
    });
    if (savedState.isMaximized) mainWindow.maximize();
  } else {
    mainWindow.setBounds({ x: 0, y: 0, width: screenWidth, height: screenHeight });
  }

  // Debounced save on resize/move
  let _saveTimer;
  const debouncedSave = () => { clearTimeout(_saveTimer); _saveTimer = setTimeout(saveWindowState, 500); };
  mainWindow.on("resize", debouncedSave);
  mainWindow.on("move", debouncedSave);

  if (process.env.NODE_ENV === "development" || !app.isPackaged) {
    mainWindow.loadURL("http://localhost:3000");
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  mainWindow.on("close", (e) => {
    saveWindowState();
    if (tray && !app.isQuitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  // Auto-hide widget when main window is shown/focused
  mainWindow.on("show", () => destroyWidget());
  mainWindow.on("focus", () => destroyWidget());
  // Show widget when minimized (if still connected and playing check is done in renderer)
  mainWindow.on("minimize", () => {
    // The renderer will call show-widget on next status poll if playing
  });
}

// ── Tray ──────────────────────────────────────────────────────
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

  rebuildTrayMenu();

  tray.on("double-click", () => {
    mainWindow.show();
    mainWindow.focus();
  });
}

function rebuildTrayMenu() {
  if (!tray) return;

  const songLabel = currentSong.title
    ? `${currentSong.artist || "Unknown"} — ${currentSong.title}`
    : "No track playing";

  const moodLabels = { chill: "Chill", neutral: "Neutral", energetic: "Energetic", focus: "Focus", melancholy: "Melancholy", discovery: "Discovery" };

  const moodSubmenu = Object.entries(moodLabels).map(([key, label]) => ({
    label,
    type: "radio",
    checked: currentMood === key,
    click: () => setMood(key),
  }));

  const sleepSubmenu = [
    { label: "15 min", click: () => setSleepTimer(15) },
    { label: "30 min", click: () => setSleepTimer(30) },
    { label: "60 min", click: () => setSleepTimer(60) },
    { label: "90 min", click: () => setSleepTimer(90) },
    { type: "separator" },
    { label: "Cancel timer", click: () => cancelSleepTimer() },
  ];

  const contextMenu = Menu.buildFromTemplate([
    { label: songLabel, enabled: false },
    { type: "separator" },
    { label: isPlaying ? "▶ Playing" : "⏸ Paused", click: () => sendCommand("play_pause") },
    { label: "Next", click: () => sendCommand("next") },
    { type: "separator" },
    { label: "Mood", submenu: moodSubmenu },
    { label: vibeLockActive ? "🔓 Unlock Vibe" : "🔒 Vibe Lock", click: () => toggleVibeLock() },
    { label: "Skip Streak", enabled: false, sublabel: skipStreak >= 2 ? `${skipStreak} skips` : undefined },
    { type: "separator" },
    { label: "Heart", click: () => sendCommand("heart") },
    { label: "Thumbs Down", click: () => sendCommand("thumbs_down") },
    { type: "separator" },
    { label: "Sleep Timer", submenu: sleepSubmenu },
    { type: "separator" },
    { label: "Toggle Mini Player", click: () => toggleMiniPlayer() },
    { label: "Toggle Always On Top", click: () => toggleAlwaysOnTop() },
    { type: "separator" },
    { label: "Show Radio", click: () => { mainWindow.show(); mainWindow.focus(); } },
    { type: "separator" },
    { label: "Quit", click: () => { app.isQuitting = true; app.quit(); } },
  ]);

  tray.setContextMenu(contextMenu);

  // Update tooltip with current song
  const tooltipText = currentSong.title
    ? `Radio — ${currentSong.artist || "Unknown"} — ${currentSong.title}`
    : "Radio";
  tray.setToolTip(tooltipText);
}

// ── API Helpers ───────────────────────────────────────────────
async function sendCommand(cmd) {
  try {
    if (cmd === "play_pause") {
      const statusResp = await fetch(`${API_URL}/api/play/status`);
      const status = await statusResp.json();
      if (status.is_playing) {
        await fetch(`${API_URL}/api/play/pause`, { method: "POST" });
        isPlaying = false;
      } else {
        await fetch(`${API_URL}/api/play/resume`, { method: "POST" });
        isPlaying = true;
      }
    } else if (cmd === "next") {
      await fetch(`${API_URL}/api/queue/skip`, { method: "POST" });
    } else if (cmd === "stop") {
      await fetch(`${API_URL}/api/play/stop`, { method: "POST" });
    } else if (cmd === "heart") {
      await fetch(`${API_URL}/api/play/heart`, { method: "POST" });
    } else if (cmd === "thumbs_down") {
      await fetch(`${API_URL}/api/play/thumbs-down`, { method: "POST" });
    } else if (cmd === "replay") {
      await fetch(`${API_URL}/api/queue/replay`, { method: "POST" });
    }
    rebuildTrayMenu();
  } catch (e) {
    console.error("Command failed:", e);
  }
}

async function setMood(mood) {
  try {
    await fetch(`${API_URL}/api/mood/set`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: mood }),
    });
    currentMood = mood;
    rebuildTrayMenu();
  } catch (e) {
    console.error("Mood set failed:", e);
  }
}

async function toggleVibeLock() {
  try {
    if (vibeLockActive) {
      await fetch(`${API_URL}/api/mood/vibe-lock/deactivate`, { method: "POST" });
      vibeLockActive = false;
    } else {
      // Get current song to activate vibe lock
      const statusResp = await fetch(`${API_URL}/api/play/status`);
      const status = await statusResp.json();
      const songId = status.current_song?.id;
      if (!songId) return;
      await fetch(`${API_URL}/api/mood/vibe-lock/activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ song_id: songId }),
      });
      vibeLockActive = true;
    }
    rebuildTrayMenu();
  } catch (e) {
    console.error("Vibe lock toggle failed:", e);
  }
}

async function setSleepTimer(minutes) {
  try {
    await fetch(`${API_URL}/api/sleep/timer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes, fade_seconds: 30, switch_to_chill: true }),
    });
  } catch (e) {
    console.error("Sleep timer failed:", e);
  }
}

async function cancelSleepTimer() {
  try {
    await fetch(`${API_URL}/api/sleep/timer`, { method: "DELETE" });
  } catch (e) {
    console.error("Cancel sleep timer failed:", e);
  }
}

async function setVolume(delta) {
  try {
    const statusResp = await fetch(`${API_URL}/api/play/status`);
    const status = await statusResp.json();
    const currentVol = status.volume ?? 50;
    const newVol = Math.max(0, Math.min(100, currentVol + delta));
    await fetch(`${API_URL}/api/play/volume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume: newVol }),
    });
  } catch (e) {
    console.error("Volume change failed:", e);
  }
}

// ── Hotkeys ───────────────────────────────────────────────────
// Default bindings (used if no overrides provided)
const DEFAULT_HOTKEYS = {
  heart: "Alt+H",
  thumbs_down: "Alt+D",
  vibe_lock: "Alt+V",
  mood_chill: "Alt+1",
  mood_energetic: "Alt+2",
  mood_focus: "Alt+3",
  mood_melancholy: "Alt+4",
  mood_discovery: "Alt+5",
  volume_up: "Alt+Up",
  volume_down: "Alt+Down",
};

let currentHotkeyBindings = { ...DEFAULT_HOTKEYS };

function registerAllHotkeys(overrides) {
  // Unregister existing
  registeredHotkeys.forEach((key) => {
    try { globalShortcut.unregister(key); } catch (_) {}
  });
  registeredHotkeys = [];

  // Merge overrides with defaults
  const bindingsMap = { ...DEFAULT_HOTKEYS, ...(overrides || {}) };
  currentHotkeyBindings = bindingsMap;

  const actionMap = {
    heart: () => sendCommand("heart"),
    thumbs_down: () => sendCommand("thumbs_down"),
    vibe_lock: () => toggleVibeLock(),
    mood_chill: () => setMood("chill"),
    mood_energetic: () => setMood("energetic"),
    mood_focus: () => setMood("focus"),
    mood_melancholy: () => setMood("melancholy"),
    mood_discovery: () => setMood("discovery"),
    volume_up: () => setVolume(5),
    volume_down: () => setVolume(-5),
  };

  let successCount = 0;
  Object.entries(bindingsMap).forEach(([action, key]) => {
    const fn = actionMap[action];
    if (!fn || !key) return;
    try {
      const success = globalShortcut.register(key, fn);
      if (success) {
        registeredHotkeys.push(key);
        successCount++;
      } else {
        console.warn(`Failed to register hotkey: ${key} for ${action}`);
      }
    } catch (e) {
      console.warn(`Hotkey registration error for ${key} (${action}):`, e.message);
    }
  });

  console.log(`Registered ${successCount}/${Object.keys(bindingsMap).length} hotkeys`);
}

// ── System Events (Power Monitor) ─────────────────────────────
function setupPowerMonitor() {
  // Pause on screen lock
  powerMonitor.on("lock-screen", async () => {
    if (!autoPauseOnLock) return;
    console.log("Screen locked - pausing playback");
    try {
      const statusResp = await fetch(`${API_URL}/api/play/status`);
      const status = await statusResp.json();
      wasPlayingBeforeLock = status.is_playing;
      if (status.is_playing) {
        await fetch(`${API_URL}/api/play/pause`, { method: "POST" });
      }
    } catch (e) {
      console.error("Auto-pause on lock failed:", e);
    }
  });

  // Resume on unlock (only if was playing before lock)
  powerMonitor.on("unlock", async () => {
    if (!autoResumeOnUnlock) return;
    if (wasPlayingBeforeLock) {
      console.log("Screen unlocked - resuming playback");
      try {
        await fetch(`${API_URL}/api/play/resume`, { method: "POST" });
      } catch (e) {
        console.error("Auto-resume on unlock failed:", e);
      }
      wasPlayingBeforeLock = false;
    }
  });

  // Pause on system sleep
  powerMonitor.on("suspend", async () => {
    if (!autoPauseOnLock) return;
    console.log("System sleeping - pausing playback");
    try {
      const statusResp = await fetch(`${API_URL}/api/play/status`);
      const status = await statusResp.json();
      wasPlayingBeforeLock = status.is_playing;
      if (status.is_playing) {
        await fetch(`${API_URL}/api/play/pause`, { method: "POST" });
      }
    } catch (e) {
      console.error("Auto-pause on suspend failed:", e);
    }
  });

  // Resume after wake
  powerMonitor.on("resume", async () => {
    if (!autoResumeOnUnlock) return;
    if (wasPlayingBeforeLock) {
      console.log("System resumed - resuming playback");
      try {
        await fetch(`${API_URL}/api/play/resume`, { method: "POST" });
      } catch (e) {
        console.error("Auto-resume after wake failed:", e);
      }
      wasPlayingBeforeLock = false;
    }
  });
}

// ── System Notification ───────────────────────────────────────
function sendSongNotification(artist, title) {
  if (!notifyOnSongChange) return;
  if (!Notification.isSupported()) return;

  const now = Date.now();
  if (now - lastNotifyTime < 30000) return; // Rate limit: 30s between notifications
  lastNotifyTime = now;

  const notification = new Notification({
    title: "Now Playing",
    body: `${artist || "Unknown"} — ${title || "Unknown"}`,
    silent: true,
    icon: path.join(__dirname, "tray-icon.png"),
  });

  notification.show();
}

// ── Taskbar Progress ──────────────────────────────────────────
function updateTaskbarProgress(position, duration) {
  if (!mainWindow) return;
  if (duration > 0 && position >= 0) {
    const progress = Math.min(1, Math.max(0, position / duration));
    mainWindow.setProgressBar(progress);
  } else {
    mainWindow.setProgressBar(-1); // Remove progress bar
  }
}

// ── Mini Player Toggle ────────────────────────────────────────
function toggleMiniPlayer() {
  if (!mainWindow) return;
  if (!miniPlayerEnabled) return;

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

function toggleAlwaysOnTop() {
  if (!mainWindow) return;
  const newVal = !mainWindow.isAlwaysOnTop();
  mainWindow.setAlwaysOnTop(newVal, "floating");
}

// ── Now Playing Widget (always-on-top mini overlay) ───────────
let widgetWindow = null;

function createWidget() {
  if (widgetWindow) return;
  const { screen } = require("electron");
  const { width: screenWidth, height: screenHeight } = screen.getPrimaryDisplay().workAreaSize;

  widgetWindow = new BrowserWindow({
    width: 320,
    height: 52,
    x: screenWidth - 330,
    y: screenHeight - 110,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload-widget.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  widgetWindow.loadFile(path.join(__dirname, "widget.html"));
  widgetWindow.on("closed", () => { widgetWindow = null; });
  widgetWindow.once("ready-to-show", () => widgetWindow.show());
}

function destroyWidget() {
  if (widgetWindow) {
    widgetWindow.close();
    widgetWindow = null;
  }
}

function updateWidget(artist, title) {
  if (widgetWindow && !widgetWindow.isDestroyed()) {
    try {
      widgetWindow.webContents.send("widget-update", { artist, title });
    } catch (_) {}
  }
}

// ── Thumbnail Toolbar (Windows taskbar hover preview) ────────
function setupThumbnailToolbar() {
  if (!mainWindow || process.platform !== "win32") return;

  function makeIcon(drawFn) {
    const size = 16;
    const buf = Buffer.alloc(size * size * 4);
    drawFn(buf, size);
    return nativeImage.createFromBuffer(buf, { width: size, height: size });
  }

  function drawTriangle(buf, s) {
    const cx = s / 2, cy = s / 2, r = 5;
    for (let y = 0; y < s; y++) {
      for (let x = 0; x < s; x++) {
        const i = (y * s + x) * 4;
        const inTriangle = x >= cx - r && x <= cx + r && y >= cy - r && y <= cy + r
          && (x - (cx - r)) / (r * 2) >= (y - (cy - r)) / (r * 2);
        if (inTriangle) { buf[i] = 78; buf[i + 1] = 204; buf[i + 2] = 196; buf[i + 3] = 255; }
      }
    }
  }

  function drawPauseBars(buf, s) {
    const color = [78, 204, 196];
    for (let y = 0; y < s; y++) {
      for (let x = 0; x < s; x++) {
        const i = (y * s + x) * 4;
        const bar1 = x >= 5 && x <= 7 && y >= 4 && y <= 12;
        const bar2 = x >= 9 && x <= 11 && y >= 4 && y <= 12;
        if (bar1 || bar2) { buf[i] = color[0]; buf[i + 1] = color[1]; buf[i + 2] = color[2]; buf[i + 3] = 255; }
      }
    }
  }

  function drawSkip(buf, s) {
    const color = [200, 200, 210];
    for (let y = 0; y < s; y++) {
      for (let x = 0; x < s; x++) {
        const i = (y * s + x) * 4;
        const bar = x >= 3 && x <= 5 && y >= 4 && y <= 12;
        const tri = y >= 4 && y <= 12 && x >= 7 && x <= 13 && (x - 7) / 6 >= (y - 4) / 8 && (x - 7) / 6 <= 1 - (y - 4) / 8;
        if (bar || tri) { buf[i] = color[0]; buf[i + 1] = color[1]; buf[i + 2] = color[2]; buf[i + 3] = 255; }
      }
    }
  }

  function drawHeart(buf, s) {
    const color = [244, 114, 182];
    for (let y = 0; y < s; y++) {
      for (let x = 0; x < s; x++) {
        const i = (y * s + x) * 4;
        // Simple heart approximation: two circles + triangle
        const nx = (x - s / 2) / (s / 2), ny = (y - s / 2) / (s / 2);
        const d1 = Math.sqrt((nx + 0.3) ** 2 + (ny + 0.15) ** 2);
        const d2 = Math.sqrt((nx - 0.3) ** 2 + (ny + 0.15) ** 2);
        const inHeart = (d1 < 0.35 || d2 < 0.35 || (ny >= 0 && ny <= 0.5 && Math.abs(nx) <= 0.5 * (1 - ny)));
        if (inHeart) { buf[i] = color[0]; buf[i + 1] = color[1]; buf[i + 2] = color[2]; buf[i + 3] = 255; }
      }
    }
  }

  mainWindow.setThumbnailToolbar([
    { tooltip: "Previous", icon: makeIcon(drawTriangle), click: () => sendCommand("replay") },
    { tooltip: "Play / Pause", icon: makeIcon(drawPauseBars), click: () => sendCommand("play_pause") },
    { tooltip: "Next", icon: makeIcon(drawSkip), click: () => sendCommand("next") },
    { tooltip: "Heart", icon: makeIcon(drawHeart), click: () => sendCommand("heart") },
  ]);
}

// ── Jump List ─────────────────────────────────────────────────
function setupJumpList() {
  try {
    app.setJumpList([
      {
        name: "Radio Controls",
        items: [
          { type: "task", title: "Play / Pause", program: process.execPath, args: ["--action=play_pause"] },
          { type: "task", title: "Next Song", program: process.execPath, args: ["--action=next"] },
          { type: "task", title: "Heart Song", program: process.execPath, args: ["--action=heart"] },
        ],
      },
    ]);
  } catch (e) {
    console.warn("Jump list setup failed:", e.message);
  }
}

// ── IPC Handlers ──────────────────────────────────────────────
function setupIPC() {
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

  // Tray info updates from renderer
  ipcMain.handle("update-tray-song", (_event, artist, title) => {
    currentSong = { artist, title };
    rebuildTrayMenu();
  });

  ipcMain.handle("update-tray-mood", (_event, mood) => {
    currentMood = mood;
    rebuildTrayMenu();
  });

  ipcMain.handle("update-tray-vibe-lock", (_event, active) => {
    vibeLockActive = active;
    rebuildTrayMenu();
  });

  ipcMain.handle("update-tray-skip-streak", (_event, streak) => {
    skipStreak = streak;
    rebuildTrayMenu();
  });

  ipcMain.handle("update-tray-playing", (_event, playing) => {
    isPlaying = playing;
    rebuildTrayMenu();
    updatePowerSave(playing);
  });

  ipcMain.handle("update-taskbar-progress", (_event, position, duration) => {
    positionMs = position;
    durationMs = duration;
    updateTaskbarProgress(position, duration);
  });

  ipcMain.handle("update-notification", (_event, artist, title) => {
    sendSongNotification(artist, title);
  });

  // Start-on-login (auto-launch with Windows)
  ipcMain.handle("set-start-on-login", (_event, enabled) => {
    try {
      app.setLoginItemSettings({ openAtLogin: enabled });
      console.log(`Start on login set to ${enabled}`);
    } catch (e) {
      console.warn("Failed to set start-on-login:", e.message);
    }
  });

  ipcMain.handle("get-start-on-login", () => {
    try {
      return app.getLoginItemSettings().openAtLogin;
    } catch {
      return false;
    }
  });

  // Hotkey bindings — reload from renderer when settings change
  ipcMain.handle("reload-hotkeys", (_event, bindings) => {
    registerAllHotkeys(bindings);
  });

  // Now Playing widget controls (only show when main window is hidden and widget enabled)
  ipcMain.handle("show-widget", () => {
    if (mainWindow && !mainWindow.isVisible() && widgetEnabled) {
      createWidget();
    }
  });
  ipcMain.handle("hide-widget", () => destroyWidget());
  ipcMain.handle("update-widget", (_event, artist, title) => {
    updateWidget(artist, title);
  });

  // Settings sync from renderer (used by power monitor, notifications)
  ipcMain.handle("set-setting", (_event, key, value) => {
    if (key === "notify_on_song_change") notifyOnSongChange = value === "true";
    else if (key === "auto_pause_on_lock") autoPauseOnLock = value === "true";
    else if (key === "auto_resume_on_unlock") autoResumeOnUnlock = value === "true";
    else if (key === "mini_player_enabled") miniPlayerEnabled = value === "true";
    else if (key === "widget_enabled") widgetEnabled = value === "true";
  });
}

// ── Backend Process ───────────────────────────────────────────
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

// ── Power Save Blocker ─────────────────────────────────────────
function updatePowerSave(playing) {
  if (playing && powerSaveBlockerId === null) {
    powerSaveBlockerId = powerSaveBlocker.start("prevent-app-suspension");
    console.log("Power save blocker started");
  } else if (!playing && powerSaveBlockerId !== null) {
    powerSaveBlocker.stop(powerSaveBlockerId);
    powerSaveBlockerId = null;
    console.log("Power save blocker stopped");
  }
}

function stopPowerSave() {
  if (powerSaveBlockerId !== null) {
    try { powerSaveBlocker.stop(powerSaveBlockerId); } catch (_) {}
    powerSaveBlockerId = null;
  }
}

// ── App Lifecycle ─────────────────────────────────────────────
app.whenReady().then(() => {
  // Single instance lock
  const gotTheLock = app.requestSingleInstanceLock();
  if (!gotTheLock) {
    console.log("Another instance is running — quitting");
    app.quit();
    return;
  }

  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  startBackend();
  createWindow();
  createTray();
  registerAllHotkeys();
  setupIPC();
  setupPowerMonitor();
  setupJumpList();
  setupThumbnailToolbar();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopPowerSave();
  if (uvicornProcess) uvicornProcess.kill();
  globalShortcut.unregisterAll();
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", () => {
  stopPowerSave();
  if (uvicornProcess) uvicornProcess.kill();
  globalShortcut.unregisterAll();
});
