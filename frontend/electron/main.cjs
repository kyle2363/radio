const { app, BrowserWindow, ipcMain, Tray, Menu, globalShortcut, nativeImage, nativeTheme, Notification, powerMonitor, powerSaveBlocker, session, safeStorage, dialog } = require("electron");
const path = require("path");
const { spawn, execSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const { shutdownOwnedBackend } = require("./backend-shutdown.cjs");
const { BACKEND_URL, backendFetch, backendCookieHeader, waitForBackendReady, createPlaybackBinding } = require("./backend-session.cjs");
const { loadDevelopmentWindow, setThumbnailButtons } = require('./window-startup.cjs');
const { createMobilePairingControl, registerMobilePairingIPC } = require('./mobile-pairing.cjs');
const { createRemoteHostingControl, createWindowsTunnelProcess, probeTunnelEndpoint, registerRemoteHostingIPC } = require('./remote-hosting.cjs');
const { createDiscoveryPublisher, registerDiscoveryIPC } = require('./discovery-publisher.cjs');
const { resolveBackendLaunch, reportBackendSpawnError } = require('./backend-launch.cjs');

let mainWindow;
let uvicornProcess;
let tray;
let isMiniMode = false;
let remoteHosting;
let discoveryPublisher;
let remoteHostingPreferences = { binaryPath: '', restoreOnLaunch: false };
let resumeRemoteAfterWake = false;
const mobilePairing = createMobilePairingControl({
  enabled: !app.isPackaged,
  isBackendAlive: () => Boolean(uvicornProcess && uvicornProcess.exitCode == null && uvicornProcess.signalCode == null),
  request: (apiPath, options) => backendFetch(session.defaultSession, apiPath, options),
});
const playbackBinding = createPlaybackBinding();
function remoteHostingPreferencesPath() { return path.join(app.getPath('userData'), 'remote-hosting.json'); }
function loadRemoteHostingPreferences() {
  try {
    const value = JSON.parse(fs.readFileSync(remoteHostingPreferencesPath(), 'utf8'));
    remoteHostingPreferences = { binaryPath: typeof value.binaryPath === 'string' ? value.binaryPath : '', restoreOnLaunch: value.restoreOnLaunch === true, firebaseProjectId: typeof value.firebaseProjectId === 'string' ? value.firebaseProjectId : '', firebaseApiKey: typeof value.firebaseApiKey === 'string' ? value.firebaseApiKey : '', firebaseCredential: typeof value.firebaseCredential === 'string' ? value.firebaseCredential : '' };
  } catch { remoteHostingPreferences = { binaryPath: '', restoreOnLaunch: false, firebaseProjectId: '', firebaseApiKey: '', firebaseCredential: '' }; }
}
function saveRemoteHostingPreferences() {
  const target = remoteHostingPreferencesPath();
  const temporary = `${target}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, JSON.stringify(remoteHostingPreferences), { mode: 0o600, flag: 'w' });
    fs.renameSync(temporary, target);
  } catch (error) {
    try { fs.unlinkSync(temporary); } catch (_) {}
    throw Error('Remote hosting preferences could not be saved securely.');
  }
}
function initRemoteHosting() {
  loadRemoteHostingPreferences();
  discoveryPublisher = createDiscoveryPublisher({
    storage: {
      load: () => { if (!safeStorage.isEncryptionAvailable() || !remoteHostingPreferences.firebaseCredential) return null; try { return safeStorage.decryptString(Buffer.from(remoteHostingPreferences.firebaseCredential, 'base64')); } catch { return null; } },
      save: value => {
        if (!safeStorage.isEncryptionAvailable()) throw Error('Protected credential storage is unavailable.');
        const prior = remoteHostingPreferences.firebaseCredential;
        remoteHostingPreferences.firebaseCredential = safeStorage.encryptString(value).toString('base64');
        try { saveRemoteHostingPreferences(); } catch (error) { remoteHostingPreferences.firebaseCredential = prior; throw error; }
      },
      clear: () => {
        const prior = remoteHostingPreferences.firebaseCredential;
        remoteHostingPreferences.firebaseCredential = '';
        try { saveRemoteHostingPreferences(); } catch (error) { remoteHostingPreferences.firebaseCredential = prior; throw error; }
      },
    },
    freshDescriptor: () => remoteHosting.freshDescriptor(),
  });
  if (remoteHostingPreferences.firebaseProjectId && remoteHostingPreferences.firebaseApiKey) void discoveryPublisher.configure({ projectId: remoteHostingPreferences.firebaseProjectId, apiKey: remoteHostingPreferences.firebaseApiKey }).then(() => discoveryPublisher.restore()).catch(() => {});
  const backendRoot = path.join(__dirname, '..', '..', 'backend');
  const pythonPath = path.join(backendRoot, 'venv', 'Scripts', 'python.exe');
  const helperPath = path.join(app.isPackaged ? process.resourcesPath : __dirname, 'mobile-tunnel-job.py');
  const processFactory = process.platform === 'win32'
    ? createWindowsTunnelProcess({ pythonPath: fs.existsSync(pythonPath) ? pythonPath : 'python', helperPath })
    : async executable => {
      const child = spawn(executable, ['tunnel', '--url', 'http://127.0.0.1:18766', '--no-autoupdate'], { shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      let pending = '';
      child.onLine = callback => { child._lineHandler = callback; };
      const parse = bytes => { pending += bytes.toString('utf8'); const lines = pending.split(/\r?\n/); pending = lines.pop() || ''; for (const line of lines) child._lineHandler?.(line); };
      child.stdout.on('data', parse); child.stderr.on('data', parse); child.onExit = callback => child.once('exit', callback); child.ready = Promise.resolve();
      return child;
    };
  remoteHosting = createRemoteHostingControl({
    request: (suffix, options) => mobilePairing.remoteControl(suffix, options.method, options.method === 'GET' ? null : JSON.parse(options.body || '{}')),
    startProcess: processFactory,
    probe: probeTunnelEndpoint,
    stopProcess: child => new Promise(resolve => {
      if (child.exitCode !== null || child.signalCode !== null) return resolve();
      const timeout = setTimeout(() => { try { child.kill(); } catch (_) {} resolve(); }, 4000);
      child.once('exit', () => { clearTimeout(timeout); resolve(); });
      if (process.platform === 'win32') { try { require('child_process').execFileSync('taskkill.exe', ['/F', '/T', '/PID', String(child.pid)], { windowsHide: true, timeout: 2500, stdio: 'ignore' }); } catch (_) { try { child.kill(); } catch (_) {} } }
      else { try { child.kill('SIGTERM'); } catch (_) {} }
    }),
    emit: status => { void discoveryPublisher.hostingChanged(status).catch(() => {}); if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('remote-hosting-changed', status); },
    initialState: { binaryPath: remoteHostingPreferences.binaryPath, restoreOnLaunch: remoteHostingPreferences.restoreOnLaunch },
  });
  if (remoteHostingPreferences.binaryPath) { try { remoteHosting.setBinaryPath(remoteHostingPreferences.binaryPath); } catch { remoteHostingPreferences.binaryPath = ''; saveRemoteHostingPreferences(); } }
  remoteHosting.setRestoreOnLaunch(remoteHostingPreferences.restoreOnLaunch);
}
const backendRequest = (apiPath, options = {}) => {
  const headers = playbackBinding.headers();
  if (/^\/api\/(play|queue|mood|sleep|filter)(\/|$)/.test(apiPath) && !headers['X-Radio-Playback-Session']) {
    return Promise.reject(Error('Listening session is not ready'));
  }
  return backendFetch(session.defaultSession, apiPath, { ...options, headers: { ...options.headers, ...headers } });
};

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
let appIconStyle = "drift";

const ICONS_DIR = path.join(__dirname, "icons");
function currentOsMode() {
  try { return nativeTheme.shouldUseDarkColors ? "dark" : "light"; } catch (_) { return "dark"; }
}
function iconPathFor(style, variant = "tray", mode) {
  const safe = ["drift", "orbit", "pulse", "spectrum"].includes(style) ? style : "drift";
  const m = mode || currentOsMode();
  const file = variant === "tray" ? `${safe}-${m}-tray.png` : `${safe}-${m}.png`;
  return path.join(ICONS_DIR, file);
}

async function loadAppIconStyle() {
  try {
    const resp = await backendRequest(`/api/settings/app_icon_style`);
    if (resp.ok) {
      const data = await resp.json();
      appIconStyle = ["drift", "orbit", "pulse", "spectrum"].includes(data.value) ? data.value : "drift";
    }
  } catch (_) {}
}

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
    title: "Radio",
    show: false,
    backgroundColor: "#070a10",
    frame: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    autoHideMenuBar: true,
  });

  mainWindow.once("ready-to-show", () => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.show();
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
    const window = mainWindow;
    // The page immediately checks authentication. Wait for the backend as
    // well as Vite so those first requests cannot race server startup.
    waitForBackendReady(session.defaultSession, {
      isCancelled: () => window.isDestroyed() || app.isQuitting,
    }).then((ready) => {
      if (ready) return loadDevelopmentWindow(window, session.defaultSession, {
        isCancelled: () => app.isQuitting,
      });
    }).catch(() => console.error('Unable to load the development window'));
  } else {
    const window = mainWindow;
    // Load through HTTP so authenticated requests have a trusted origin. The
    // public status check succeeds before sign-in and while password is unset.
    waitForBackendReady(session.defaultSession, {
      isCancelled: () => window.isDestroyed() || app.isQuitting,
    }).then((ready) => {
      if (ready) return window.loadURL(`${BACKEND_URL}/`);
    }).catch(() => console.error("Unable to load the station window"));
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
  const iconPath = iconPathFor(appIconStyle, "tray");
  let icon;
  if (fs.existsSync(iconPath)) {
    icon = nativeImage.createFromPath(iconPath);
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

function updateTrayIcon(style) {
  if (!["drift", "orbit", "pulse", "spectrum"].includes(style)) return;
  appIconStyle = style;
  refreshTrayIcon();
  refreshWindowIcon();
}

function refreshTrayIcon() {
  if (!tray) return;
  const iconPath = iconPathFor(appIconStyle, "tray");
  if (!fs.existsSync(iconPath)) return;
  tray.setImage(nativeImage.createFromPath(iconPath));
}

function refreshWindowIcon() {
  if (!mainWindow) return;
  const iconPath = iconPathFor(appIconStyle, "full");
  if (fs.existsSync(iconPath)) {
    mainWindow.setIcon(nativeImage.createFromPath(iconPath));
  }
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
      const statusResp = await backendRequest(`/api/play/status`);
      const status = await statusResp.json();
      if (status.is_playing) {
        await backendRequest(`/api/play/pause`, { method: "POST" });
        isPlaying = false;
      } else {
        await backendRequest(`/api/play/resume`, { method: "POST" });
        isPlaying = true;
      }
    } else if (cmd === "next") {
      await backendRequest(`/api/queue/skip`, { method: "POST" });
    } else if (cmd === "stop") {
      await backendRequest(`/api/play/stop`, { method: "POST" });
    } else if (cmd === "heart") {
      await backendRequest(`/api/play/heart`, { method: "POST" });
    } else if (cmd === "thumbs_down") {
      await backendRequest(`/api/play/thumbs-down`, { method: "POST" });
    } else if (cmd === "replay") {
      await backendRequest(`/api/queue/replay`, { method: "POST" });
    }
    rebuildTrayMenu();
  } catch (e) {
    console.error("Command failed:", e);
  }
}

async function setMood(mood) {
  try {
    await backendRequest(`/api/mood/set`, {
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
      await backendRequest(`/api/mood/vibe-lock/deactivate`, { method: "POST" });
      vibeLockActive = false;
    } else {
      // Get current song to activate vibe lock
      const statusResp = await backendRequest(`/api/play/status`);
      const status = await statusResp.json();
      const songId = status.current_song?.id;
      if (!songId) return;
      await backendRequest(`/api/mood/vibe-lock/activate`, {
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
    await backendRequest(`/api/sleep/timer`, {
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
    await backendRequest(`/api/sleep/timer`, { method: "DELETE" });
  } catch (e) {
    console.error("Cancel sleep timer failed:", e);
  }
}

async function setVolume(delta) {
  try {
    const statusResp = await backendRequest(`/api/play/status`);
    const status = await statusResp.json();
    const currentVol = status.volume ?? 50;
    const newVol = Math.max(0, Math.min(100, currentVol + delta));
    await backendRequest(`/api/play/volume`, {
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
function setupNativeThemeWatcher() {
  // Swap tray + window icon when Windows theme changes
  try {
    nativeTheme.on("updated", () => {
      refreshTrayIcon();
      refreshWindowIcon();
    });
  } catch (_) {}
}

function setupPowerMonitor() {
  // Pause on screen lock
  powerMonitor.on("lock-screen", async () => {
    if (!autoPauseOnLock) return;
    console.log("Screen locked - pausing playback");
    try {
      const statusResp = await backendRequest(`/api/play/status`);
      const status = await statusResp.json();
      wasPlayingBeforeLock = status.is_playing;
      if (status.is_playing) {
        await backendRequest(`/api/play/pause`, { method: "POST" });
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
        await backendRequest(`/api/play/resume`, { method: "POST" });
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
      const statusResp = await backendRequest(`/api/play/status`);
      const status = await statusResp.json();
      wasPlayingBeforeLock = status.is_playing;
      if (status.is_playing) {
        await backendRequest(`/api/play/pause`, { method: "POST" });
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
        await backendRequest(`/api/play/resume`, { method: "POST" });
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
    icon: fs.existsSync(iconPathFor(appIconStyle, "full"))
      ? iconPathFor(appIconStyle, "full")
      : path.join(__dirname, "tray-icon.png"),
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

function updateWindowTitle(artist, title) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.setTitle(
    title ? `${artist || "Unknown"} — ${title} • Radio` : "Radio"
  );
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

  setThumbnailButtons(mainWindow, [
    { tooltip: "Previous", icon: makeIcon(drawTriangle), click: () => sendCommand("replay") },
    { tooltip: "Play / Pause", icon: makeIcon(drawPauseBars), click: () => sendCommand("play_pause") },
    { tooltip: "Next", icon: makeIcon(drawSkip), click: () => sendCommand("next") },
    { tooltip: "Heart", icon: makeIcon(drawHeart), click: () => sendCommand("heart") },
  ], process.platform, error => console.warn('Thumbnail toolbar unavailable:', error.message));
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
  registerMobilePairingIPC(ipcMain, mobilePairing, () => mainWindow);
  registerRemoteHostingIPC(ipcMain, {
    status: () => remoteHosting.status(),
    start: () => remoteHosting.start(), stop: () => remoteHosting.stop(), retry: () => remoteHosting.retry(),
    freshDescriptor: () => remoteHosting.freshDescriptor(),
    setBinaryPath(value) { const result = remoteHosting.setBinaryPath(value); remoteHostingPreferences.binaryPath = result; saveRemoteHostingPreferences(); return result; },
    setRestoreOnLaunch(value) { const result = remoteHosting.setRestoreOnLaunch(value); remoteHostingPreferences.restoreOnLaunch = result; saveRemoteHostingPreferences(); return result; },
  }, () => mainWindow);
  registerDiscoveryIPC(ipcMain, {
    status: () => discoveryPublisher.status(),
    configure: async value => { await discoveryPublisher.configure(value); remoteHostingPreferences.firebaseProjectId = value.projectId; remoteHostingPreferences.firebaseApiKey = value.apiKey; saveRemoteHostingPreferences(); return discoveryPublisher.status(); },
    signIn: (email, password) => discoveryPublisher.signIn(email, password), signOut: () => discoveryPublisher.signOut(),
  }, () => mainWindow);
  ipcMain.handle('widget-playback-status', async (event) => {
    if (!widgetWindow || event.sender !== widgetWindow.webContents || event.senderFrame !== widgetWindow.webContents.mainFrame) {
      throw Error('Untrusted widget');
    }
    const response = await backendRequest('/api/play/status', { signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw Error('Station unavailable');
    const state = await response.json();
    return { current_song: state.current_song && { title: state.current_song.title, artist: state.current_song.artist },
      position_ms: state.position_ms, duration_ms: state.duration_ms, is_playing: state.is_playing };
  });
  ipcMain.handle('set-playback-session', (event, binding) => {
    if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) {
      throw Error('Untrusted playback controller');
    }
    const origin = new URL(event.senderFrame.url).origin;
    if (!['http://localhost:3000', BACKEND_URL].includes(origin)) throw Error('Untrusted playback controller');
    playbackBinding.set(binding);
  });
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
    updateWindowTitle(artist, title);
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

  // App icon style (drift/orbit/pulse/spectrum) — live update
  ipcMain.handle("set-app-icon-style", (_event, style) => {
    updateTrayIcon(style);
  });
}

// ── Backend Process ───────────────────────────────────────────
function startBackend() {
  const launch = resolveBackendLaunch({ packaged: app.isPackaged, resourcesPath: process.resourcesPath,
    dirname: __dirname, userDataDir: app.getPath('userData'), dataDir: process.env.RADIO_DATA_DIR });

  const mobileLaunch = mobilePairing.launchOptions();
  uvicornProcess = spawn(launch.python, [launch.serverScript], {
    cwd: launch.cwd,
    stdio: mobileLaunch.stdio,
    env: { ...process.env, PYTHONIOENCODING: "utf-8", ...launch.env, ...mobileLaunch.env },
  });
  uvicornProcess.on("error", error => reportBackendSpawnError(error, {
    showErrorBox: (title, message) => dialog.showErrorBox(title, message),
    stopWaiting: () => { app.isQuitting = true; },
    quit: () => app.quit(),
  }));
  mobilePairing.attach(uvicornProcess);

  uvicornProcess.stdout.on("data", (data) => console.log(`[Backend] ${data}`));
  uvicornProcess.stderr.on("data", (data) => console.error(`[Backend] ${data}`));
  uvicornProcess.on("close", (code) => console.log(`[Backend] Process exited with code ${code}`));
  uvicornProcess.once('exit', () => { if (remoteHosting) void remoteHosting.backendLost(); });
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

let backendShutdownPromise;
function shutdownBackend() {
  if (backendShutdownPromise) return backendShutdownPromise;
  const ownedProcess = uvicornProcess;
  backendShutdownPromise = (async () => {
    // Close mobile admission before the normal owned-backend shutdown. Even if
    // local authorization was lost, terminating our child closes its gateway.
    try { await mobilePairing.shutdown(); } catch (_) {}
    let cookieHeader = '';
    if (ownedProcess) {
      try { cookieHeader = await backendCookieHeader(session.defaultSession); } catch (_) {}
    }
    return shutdownOwnedBackend(ownedProcess, {
      cookieHeader,
      request: http.request,
      forceKill(child) {
        if (process.platform === "win32") {
          try {
            execSync(`taskkill /F /T /PID ${child.pid}`, { timeout: 5000, stdio: "ignore" });
            return;
          } catch (_) { /* Fall back to terminating only our child. */ }
        }
        child.kill();
      },
    });
  })().then((result) => {
    if (result === "timeout" || result === "error") console.warn(`Backend shutdown: ${result}`);
  }).finally(() => {
    if (uvicornProcess === ownedProcess) uvicornProcess = null;
  });
  return backendShutdownPromise;
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

  initRemoteHosting();
  startBackend();
  if (remoteHostingPreferences.restoreOnLaunch) {
    void waitForBackendReady(session.defaultSession, { isCancelled: () => app.isQuitting }).then(ready => {
      if (ready && !app.isQuitting && remoteHostingPreferences.restoreOnLaunch) return remoteHosting.start();
    }).catch(() => {});
  }
  // Load persisted app icon style after backend is up so the API call succeeds
  setTimeout(() => { loadAppIconStyle().then(() => { if (mainWindow) updateTrayIcon(appIconStyle); }); }, 1500);
  createWindow();
  createTray();
  registerAllHotkeys();
  setupIPC();
  setupPowerMonitor();
  powerMonitor.on('suspend', () => { resumeRemoteAfterWake = ['available', 'starting_gateway', 'starting_tunnel', 'verifying_endpoint', 'reconnecting'].includes(remoteHosting?.status().state); if (resumeRemoteAfterWake) void remoteHosting.stop(); });
  powerMonitor.on('resume', () => { if (resumeRemoteAfterWake) { resumeRemoteAfterWake = false; void remoteHosting.start(); } });
  setupNativeThemeWatcher();
  setupJumpList();
  setupThumbnailToolbar();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopPowerSave();
  if (process.platform !== "darwin") app.quit();
});

let shutdownComplete = false;
let quitPending = false;
app.on("before-quit", (event) => {
  if (shutdownComplete) return;
  app.isQuitting = true;
  event.preventDefault();
  if (quitPending) return;
  quitPending = true;
  stopPowerSave();
  globalShortcut.unregisterAll();
  Promise.resolve(remoteHosting?.stop()).catch(() => {}).then(() => shutdownBackend()).catch((error) => {
    console.error("Backend shutdown failed:", error.message);
  }).finally(() => {
    shutdownComplete = true;
    app.quit();
  });
});
