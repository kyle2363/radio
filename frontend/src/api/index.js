import { resolveApiUrls } from "./urls.js";

const urls = resolveApiUrls(window.location, {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL,
  wsUrl: import.meta.env.VITE_WS_URL,
});
const BASE = urls.apiBase;
export const WS_URL = urls.wsUrl;

async function request(method, path, body, timeout = 5000, externalSignal) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
    signal: externalSignal || controller.signal,
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

const get = (path, signal) => request("GET", path, undefined, undefined, signal);
export const post = (path, body, timeout, signal) => request("POST", path, body, timeout, signal);
const put = (path, body, signal) => request("PUT", path, body, undefined, signal);
const del = (path, signal) => request("DELETE", path, undefined, undefined, signal);

// ── Playback ──────────────────────────────────────────────
export const fetchPlayStatus = () => get("/api/play/status");
export const playSong = (id) => post(`/api/play/${id}`);
export const pauseSong = () => post("/api/play/pause");
export const resumeSong = () => post("/api/play/resume");
export const heartSong = () => post("/api/play/heart");
export const heartSongById = (id) => post(`/api/songs/${id}/heart`);
export const thumbsDown = () => post("/api/play/thumbs-down");
export const seekTo = (ms) => post("/api/play/seek", { ms: Math.round(ms) });
export const setVolume = (volume) => post("/api/play/volume", { volume });
export const setEqPreset = (preset) => post("/api/play/eq", { preset });
export const fetchEqPreset = () => get("/api/play/eq");
// ── Queue ─────────────────────────────────────────────────
export const fetchQueue = () => get("/api/queue");
export const fetchQueueStatus = () => get("/api/queue/status");

export async function waitForQueue(timeoutMs = 60000, pollMs = 1000, currentIds = null) {
  const start = Date.now();
  const currentSet = currentIds ? new Set(currentIds) : null;
  while (Date.now() - start < timeoutMs) {
    try {
      const status = await fetchQueueStatus();
      if (status?.queue_size > 0) {
        if (!currentSet) return status.queue;
        const newIds = (status.queue || []).map(s => s.song_id);
        if (newIds.length === 0) continue;
        const hasDifferent = newIds.some(id => !currentSet.has(id));
        if (hasDifferent) return status.queue;
      }
    } catch {
      // Server may not be ready, keep polling
    }
    await new Promise(r => setTimeout(r, pollMs));
  }
  return false;
}

export const skipSong = () => post("/api/queue/skip");
export const clearQueue = () => post("/api/queue/clear");
export const replaySong = () => post("/api/queue/replay");
export const addToQueue = (id) => post(`/api/queue/add/${id}`);
export const removeFromQueue = (id) => del(`/api/queue/remove/${id}`);
export const rebuildQueue = () => post("/api/queue/rebuild");
export const reorderQueue = (order) => put("/api/queue/reorder", { order });
export const fetchQueueHistory = (limit = 30) => get(`/api/queue/history?limit=${limit}`);

// ── Recommendations ───────────────────────────────────────
export const fetchRecommendation = () => get("/api/recommend");

// ── Play Mode ─────────────────────────────────────────────
export const fetchPlayMode = () => get("/api/play/mode");
export const setPlayMode = (mode) => post("/api/play/mode", { mode });

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
export const wipeDatabase = () => post("/api/settings/wipe-db");
export const exportSettings = () => get("/api/settings/export");
export const importSettings = (data) => post("/api/settings/import", { data });

// ── Songs ─────────────────────────────────────────────────
export const fetchSongs = (params) => {
  const qs = new URLSearchParams(params).toString();
  return get(`/api/songs?${qs}`);
};
export const fetchSong = (id) => get(`/api/songs/${id}`);
export const fetchSongsByIds = (ids) => post("/api/songs/by-ids", { song_ids: ids });
export const fetchRecentlyPlayed = (limit = 30) => get(`/api/songs/recently-played?limit=${limit}`);
export const fetchPriorSongs = () => get("/api/songs/prior");
export const fetchSimilarSongs = (id, limit = 5) => get(`/api/songs/${id}/similar?limit=${limit}`);
export const blacklistSong = (id, level = "never_play", extra = {}) => post(`/api/songs/${id}/blacklist`, { level, ...extra });
export const unblacklistSong = (id) => del(`/api/songs/${id}/blacklist`);
export const fetchBlacklist = () => get("/api/blacklist");
export const updateSongTags = (id, user_tags, user_notes) => put(`/api/songs/${id}/tags`, { user_tags, user_notes });
export const fetchSongMetadataSource = (id) => get(`/api/songs/${id}/metadata-source`);
export const updateSongMetadata = (id, data) => put(`/api/songs/${id}/metadata`, data);
export const reprocessSong = (id) => post(`/api/ingest/reprocess-single/${id}`);
export const fetchSongArt = (id) => `${BASE}/api/songs/${id}/art.jpg`;
export const fetchLyricsTiming = (id) => get(`/api/songs/${id}/lyrics-timing`);
export const fetchLyricsTimingStatus = () => get("/api/lyrics-timing/status");

// ── Library Verify ────────────────────────────────────────
export const verifyLibrary = () => get("/api/library/verify");
export const autoMatchFiles = () => post("/api/library/auto-match");
export const confirmMatch = (orphanId, filepath) => post("/api/library/confirm-match", { orphan_id: orphanId, filepath });
export const deleteSong = (id) => del(`/api/songs/${id}`);
export const addUnregisteredSong = (filepath) => post("/api/library/add-unregistered", { filepath });
export const deleteAllOrphaned = () => post("/api/library/delete-all-orphaned");

// ── Ingest ────────────────────────────────────────────────
export const fetchIngestStatus = () => get("/api/ingest/status");
export const fetchIngestProgress = () => get("/api/ingest/progress");
export const scanLibrary = () => post("/api/ingest/scan");
export const runIngest = () => post("/api/ingest/run");
export const fetchIngestFailures = () => get("/api/ingest/failures");
export const retryIngestFailures = () => post("/api/ingest/retry-failures");
export const runIngestPhase = (phase) => post("/api/ingest/run-phase/" + phase);
export const fetchIngestPhaseStatus = () => get("/api/ingest/phase-status");
export const reembedAll = () => post("/api/ingest/reembed-all");
export const reprocessEnergy = (reembed, recluster) => post("/api/admin/reprocess-energy", { reembed, recluster });
export const fetchReprocessStatus = () => get("/api/admin/reprocess-status");

// ── Onboarding ────────────────────────────────────────────
export const fetchOnboardingSongs = () => get("/api/onboarding/songs");
export const completeOnboarding = (song_ids) => post("/api/onboarding/complete", { song_ids });
export const skipOnboarding = () => post("/api/onboarding/skip");
export const refreshOnboardingSongs = (exclude_ids) => post("/api/onboarding/refresh", { exclude_ids });

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

// ── Skip Feedback ─────────────────────────────────────────
export const submitSkipFeedback = (reason, duration = null) => post("/api/queue/skip-feedback", { reason, duration });

// ── Undo ───────────────────────────────────────────────────
export const undoSkip = () => post("/api/queue/undo-skip");
export const undoThumbsDown = () => post("/api/queue/undo-thumbs-down");
export const fetchUndoState = () => get("/api/queue/undo-state");

// ── Sleep Timer ────────────────────────────────────────────
export const setSleepTimer = (minutes, fadeSeconds = 30, switchToChill = true) =>
  post("/api/sleep/timer", { minutes, fade_seconds: fadeSeconds, switch_to_chill: switchToChill });
export const cancelSleepTimer = () => del("/api/sleep/timer");
export const fetchSleepTimer = () => get("/api/sleep/timer");

// ── Collections ────────────────────────────────────────────
export const fetchCollections = () => get("/api/collections");
export const createCollection = (name, description) => post("/api/collections", { name, description });
export const updateCollection = (id, name, description) => put(`/api/collections/${id}`, { name, description });
export const deleteCollection = (id) => del(`/api/collections/${id}`);
export const fetchCollectionSongs = (id) => get(`/api/collections/${id}/songs`);
export const addToCollection = (id, song_id, position) => post(`/api/collections/${id}/songs`, { song_id, position });
export const removeFromCollection = (id, song_id) => del(`/api/collections/${id}/songs/${song_id}`);
export const reorderCollection = (id, song_ids) => put(`/api/collections/${id}/reorder`, { song_ids });

// ── Taste Reseed ─────────────────────────────────────────
export const reseedTaste = () => post("/api/taste/reseed");
export const fetchConstellations = () => get("/api/taste/constellations");

// ── Clusters & Tags ───────────────────────────────────────
export const fetchClusters = () => get("/api/clusters");
export const fetchTags = () => get("/api/tags");

// ── Folder Ticks ──────────────────────────────────────────
export const fetchFolderTicks = () => get("/api/songs/folder-ticks");

// ── Folder Filter ─────────────────────────────────────────
export const fetchSubfolders = () => get("/api/songs/subfolders");
export const fetchFolderFilter = () => get("/api/filter/folder");
export const setFolderFilter = (folders) => post("/api/filter/folder", { folders });
export const clearFolderFilter = () => post("/api/filter/folder/clear");

// ── Recommendation Filters (folder + recency + artist) ────
export const fetchRecommendationFilters = () => get("/api/filter/recommendation");
export const setRecommendationFilters = (filters) => post("/api/filter/recommendation", filters);
export const clearRecommendationFilters = () => post("/api/filter/recommendation/clear");

// ── Commands ──────────────────────────────────────────────
export const sendCommand = (text) => post("/api/command", { text }, 30000);

// ── DJ Effects ────────────────────────────────────────────
export const updateDjEffectEligible = (id, eligible) => put(`/api/songs/${id}/dj-effect-eligible`, { eligible });
export const fetchDjEffectPresets = () => get("/api/dj-effects/presets");
export const fetchDjEffectCacheInfo = () => get("/api/dj-effects/cache-info");
export const generateDjEffect = (id, preset) => post(`/api/dj-effects/generate/${id}`, { preset });

// ── Calibration (PR 4) ─────────────────────────────────────
export const fetchCalibrationSample = (n = 20) => get(`/api/calibration/sample?n=${n}`);
export const fetchCalibrationAnchors = () => get("/api/calibration/anchors");
export const setUserEnergy = (songId, value) => post(`/api/songs/${songId}/user-energy`, { value });
export const clearUserEnergy = (songId) => del(`/api/songs/${songId}/user-energy`);
