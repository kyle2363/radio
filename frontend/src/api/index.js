const API_HOST = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  ? `${window.location.protocol}//${window.location.host}`
  : "http://localhost:8765";
const BASE = API_HOST;
export const WS_URL = API_HOST.replace(/^http/, "ws") + "/ws/events";

async function request(method, path, body, timeout = 5000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
    signal: controller.signal,
  };
  if (body !== undefined) {
    opts.body = JSON.stringify(body);
  }
  try {
    const res = await fetch(`${BASE}${path}`, opts);
    clearTimeout(timeoutId);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || `${res.status} ${res.statusText}`);
    }
    return res.json();
  } catch (e) {
    clearTimeout(timeoutId);
    if (e.name === "AbortError") {
      throw new Error(`Request to ${path} timed out after ${timeout}ms`);
    }
    throw e;
  }
}

const get = (path) => request("GET", path);
const post = (path, body) => request("POST", path, body);
const put = (path, body) => request("PUT", path, body);

// ── Playback ──────────────────────────────────────────────
export const fetchPlayStatus = () => get("/api/play/status");
export const playSong = (id) => post(`/api/play/${id}`);
export const pauseSong = () => post("/api/play/pause");
export const resumeSong = () => post("/api/play/resume");
export const heartSong = () => post("/api/play/heart");
export const thumbsDown = () => post("/api/play/thumbs-down");
export const seekTo = (ms) => post("/api/play/seek", { ms: Math.round(ms) });
export const setVolume = (volume) => post("/api/play/volume", { volume });

// ── Queue ─────────────────────────────────────────────────
export const fetchQueueStatus = () => get("/api/queue/status");
export const skipSong = () => post("/api/queue/skip");
export const clearQueue = () => post("/api/queue/clear");
export const replaySong = () => post("/api/queue/replay");
export const addToQueue = (id) => post(`/api/queue/add/${id}`);
export const rebuildQueue = () => post("/api/queue/rebuild");
export const reorderQueue = (order) => put("/api/queue/reorder", { order });
export const fetchQueueHistory = (limit = 30) => get(`/api/queue/history?limit=${limit}`);

// ── Recommendations ───────────────────────────────────────
export const fetchRecommendation = () => get("/api/recommend");

// ── Mood ──────────────────────────────────────────────────
export const setMood = (state) => post("/api/mood/set", { state });
export const activateVibeLock = (song_id) => post("/api/mood/vibe-lock/activate", { song_id });
export const deactivateVibeLock = () => post("/api/mood/vibe-lock/deactivate");
export const fetchMoodHistory = () => get("/api/mood/history");

// ── Mode ──────────────────────────────────────────────────
export const toggleMode = (mode) => post("/api/mode/toggle", { mode });

// ── Settings ──────────────────────────────────────────────
export const fetchSettings = () => get("/api/settings");
export const updateSetting = (key, value) => put(`/api/settings/${key}`, { value });
export const resetRecommendations = () => post("/api/settings/reset-recommendations");

// ── Songs ─────────────────────────────────────────────────
export const fetchSongs = (params) => {
  const qs = new URLSearchParams(params).toString();
  return get(`/api/songs?${qs}`);
};
export const fetchSong = (id) => get(`/api/songs/${id}`);
export const fetchRecentlyPlayed = (limit = 30) => get(`/api/songs/recently-played?limit=${limit}`);
export const fetchSimilarSongs = (id, limit = 5) => get(`/api/songs/${id}/similar?limit=${limit}`);
export const blacklistSong = (id, reason = "user") => post(`/api/songs/${id}/blacklist`, { reason });
export const updateSongTags = (id, user_tags, user_notes) => put(`/api/songs/${id}/tags`, { user_tags, user_notes });
export const reprocessSong = (id) => post(`/api/ingest/reprocess-single/${id}`);
export const fetchSongArt = (id) => `${BASE}/api/songs/${id}/art.jpg`;

// ── Ingest ────────────────────────────────────────────────
export const fetchIngestStatus = () => get("/api/ingest/status");
export const fetchIngestProgress = () => get("/api/ingest/progress");
export const scanLibrary = () => post("/api/ingest/scan");
export const runIngest = () => post("/api/ingest/run");
export const fetchIngestFailures = () => get("/api/ingest/failures");
export const retryIngestFailures = () => post("/api/ingest/retry-failures");

// ── Onboarding ────────────────────────────────────────────
export const fetchOnboardingSongs = () => get("/api/onboarding/songs");
export const completeOnboarding = (song_ids) => post("/api/onboarding/complete", { song_ids });
export const skipOnboarding = () => post("/api/onboarding/skip");

// ── Stats ─────────────────────────────────────────────────
export const fetchDailyReport = () => get("/api/stats/daily-report");
export const fetchStatsOverview = () => get("/api/stats/overview");
export const fetchEnergyTrajectory = () => get("/api/energy/trajectory");

// ── Sessions ──────────────────────────────────────────────
export const fetchSessions = (limit = 20) => get(`/api/sessions?limit=${limit}`);
export const fetchSessionDetail = (id) => get(`/api/sessions/${id}`);

// ── LLM ───────────────────────────────────────────────────
export const fetchLlmStatus = () => get("/api/llm/status");

// ── TTS ───────────────────────────────────────────────────
export const fetchTtsStatus = () => get("/api/tts/status");
export const setTtsEngine = (engine, voice) => post("/api/tts/engine", { engine, voice });
export const setTtsVoiceMode = (mode) => post("/api/tts/voice-mode", { mode });
export const setTtsVoice = (voice) => post("/api/tts/voice", { voice });

// ── Talk Show ─────────────────────────────────────────────
export const fetchTalkConfig = () => get("/api/talk/config");
export const updateTalkConfig = (config) => post("/api/talk/config", config);
export const fetchVoiceStatus = () => get("/api/talk/voice-status");
export const downloadVoices = () => post("/api/talk/download-voices");
export const dismissGuest = () => post("/api/talk/guest-dismiss");
export const fetchPersonas = () => get("/api/talk/personas");

// ── Commands ──────────────────────────────────────────────
export const sendCommand = (text) => post("/api/command", { text });
