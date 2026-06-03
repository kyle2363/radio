import React, { useState, useEffect } from "react";
import * as api from "../api";

function TalkSettings({ addToast, masterEnabled }) {
  const [enabled, setEnabled] = useState(false);
  const [callerEnabled, setCallerEnabled] = useState(true);
  const [callerMode, setCallerMode] = useState("influences_queue");
  const [monologueEnabled, setMonologueEnabled] = useState(true);
  const [guestTakeoverEnabled, setGuestTakeoverEnabled] = useState(true);
  const [frequency, setFrequency] = useState(4);
  const [guestSongs, setGuestSongs] = useState(4);
  const [segmentCooldown, setSegmentCooldown] = useState(2);
  const [heartbrokenEnabled, setHeartbrokenEnabled] = useState(false);
  const [melancholyEnabled, setMelancholyEnabled] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  // TTS engine state
  const [ttsEngine, setTtsEngineState] = useState("omnivoice");
  const [ttsVoiceMode, setTtsVoiceModeState] = useState("user_select");
  const [ttsVoice, setTtsVoiceState] = useState("");
  const [gpuAvailable, setGpuAvailable] = useState(null);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const config = await api.fetchTalkConfig();
      setEnabled(config.talk_segments_enabled === "true");
      setCallerEnabled(config.talk_caller_enabled !== "false");
      setCallerMode(config.talk_caller_mode || "influences_queue");
      setMonologueEnabled(config.talk_monologue_enabled !== "false");
      setGuestTakeoverEnabled(config.talk_guest_takeover_enabled !== "false");
      setFrequency(parseInt(config.talk_frequency) || 4);
      setGuestSongs(parseInt(config.talk_guest_songs_per_set) || 4);
      setSegmentCooldown(parseInt(config.talk_segment_cooldown) || 2);
      setHeartbrokenEnabled(config.talk_caller_heartbroken_enabled === "true");
      setMelancholyEnabled(config.talk_caller_melancholy_enabled === "true");
    } catch (e) {
      console.error("Failed to load talk config:", e);
    }

    try {
      const tts = await api.fetchTtsStatus();
      setTtsEngineState(tts.voice_engine || "omnivoice");
      setTtsVoiceModeState(tts.voice_mode || "user_select");
      setTtsVoiceState(tts.active_voice || "");
      setGpuAvailable(tts.gpu_available);
    } catch (e) {
      console.error("Failed to load TTS status:", e);
    }
  };

  const loadVoiceStatus = async () => {
    try {
      const status = await api.fetchVoiceStatus();
      setVoiceStatus(status);
    } catch (e) {
      console.error("Failed to load voice status:", e);
    }
  };

  useEffect(() => {
    if (expanded) loadVoiceStatus();
  }, [expanded]);

  const saveSetting = async (key, value) => {
    try {
      await api.updateSetting(key, value);
    } catch (e) {
      console.error(`Failed to save ${key}:`, e);
      addToast?.(`Failed to save ${key}`, "error");
    }
  };

  const handleToggleEnabled = async () => {
    const newVal = !enabled;
    setEnabled(newVal);
    await saveSetting("talk_segments_enabled", String(newVal));
    if (!newVal) {
      await api.updateTalkConfig({ talk_segments_enabled: "false" });
    }
    addToast?.(newVal ? "Talk segments enabled" : "Talk segments disabled", "success");
  };

  const handleFrequencyChange = (e) => {
    const val = parseInt(e.target.value);
    setFrequency(val);
    saveSetting("talk_frequency", String(val));
  };

  const handleGuestSongsChange = (e) => {
    const val = parseInt(e.target.value);
    setGuestSongs(val);
    saveSetting("talk_guest_songs_per_set", String(val));
  };

  const handleCooldownChange = (e) => {
    const val = parseInt(e.target.value);
    setSegmentCooldown(val);
    saveSetting("talk_segment_cooldown", String(val));
  };

  const handleEngineChange = async (engine) => {
    try {
      const result = await api.setTtsEngine(engine);
      if (result.status === "ok") {
        setTtsEngineState(result.active_engine);
        setTtsVoiceState(result.active_voice || "");
        setGpuAvailable(result.gpu_available);
        addToast?.(`Switched to ${engine} engine`, "success");
        loadVoiceStatus();
      } else {
        addToast?.(`Failed: ${result.error}`, "error");
      }
    } catch (e) {
      addToast?.("Failed to switch engine: " + (e.message || "unknown"), "error");
    }
  };

  const handleVoiceModeChange = async (mode) => {
    try {
      const result = await api.setTtsVoiceMode(mode);
      if (result.status === "ok") {
        setTtsVoiceModeState(mode);
        addToast?.(`Voice mode: ${mode}`, "success");
      }
    } catch (e) {
      addToast?.("Failed to set voice mode", "error");
    }
  };

  const handleVoiceChange = async (voice) => {
    try {
      const result = await api.setTtsVoice(voice);
      if (result.status === "ok") {
        setTtsVoiceState(voice);
        addToast?.(`Voice: ${voice}`, "success");
      }
    } catch (e) {
      addToast?.("Failed to set voice", "error");
    }
  };

  const handleDownloadVoices = async () => {
    setDownloading(true);
    try {
      const result = await api.downloadVoices();
      if (result.status === "completed") {
        addToast?.("All Piper voices downloaded", "success");
      } else {
        addToast?.("Some voices failed to download", "error");
      }
      loadVoiceStatus();
    } catch (e) {
      addToast?.("Voice download failed: " + (e.message || "unknown error"), "error");
    }
    setDownloading(false);
  };

  // Separate voices by engine
  const omnivoiceVoices = voiceStatus?.voices
    ? Object.entries(voiceStatus.voices)
        .filter(([k]) => k.startsWith("omnivoice:"))
        .map(([k, v]) => ({ id: k.replace("omnivoice:", ""), ready: v }))
    : [];
  const piperVoices = voiceStatus?.voices
    ? Object.entries(voiceStatus.voices)
        .filter(([k]) => k.startsWith("piper:"))
        .map(([k, v]) => ({ id: k.replace("piper:", ""), ready: v }))
    : [];
  const totalReady = voiceStatus?.voices
    ? Object.values(voiceStatus.voices).filter(Boolean).length
    : 0;
  const totalVoices = voiceStatus?.voices ? Object.keys(voiceStatus.voices).length : 0;

  const configPanel = (
    <div className="space-y-4 mt-3">
      {/* TTS Engine Selector */}
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">TTS Engine</label>
        <div className="flex gap-2">
          {[
            { id: "omnivoice", label: "OmniVoice", desc: "Voice cloning (GPU)" },
            { id: "piper", label: "Piper", desc: "Fast local (CPU)" },
          ].map((eng) => (
            <button
              key={eng.id}
              onClick={() => handleEngineChange(eng.id)}
              className="flex-1 px-3 py-2.5 rounded-xl text-xs font-medium transition text-left"
              style={{
                background: ttsEngine === eng.id ? "var(--accent-muted)" : "var(--bg-elevated)",
                color: ttsEngine === eng.id ? "var(--accent)" : "var(--text-muted)",
                border: ttsEngine === eng.id ? "1px solid var(--accent)" : "1px solid transparent",
              }}
            >
              <div>{eng.label}</div>
              <div className="text-[10px] opacity-60 mt-0.5">{eng.desc}</div>
            </button>
          ))}
        </div>
        {gpuAvailable === false && ttsEngine === "omnivoice" && (
          <p className="text-xs text-yellow-500 mt-1.5">GPU not available — will fall back to Piper</p>
        )}
      </div>

      {/* Voice Selection Mode */}
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">Voice Selection</label>
        <div className="flex gap-2">
          {[
            { id: "user_select", label: "Fixed Voice" },
            { id: "random", label: "Random" },
            { id: "context", label: "By Context" },
          ].map((mode) => (
            <button
              key={mode.id}
              onClick={() => handleVoiceModeChange(mode.id)}
              className="flex-1 px-3 py-2 rounded-lg text-xs font-medium transition"
              style={{
                background: ttsVoiceMode === mode.id ? "var(--accent-muted)" : "var(--bg-elevated)",
                color: ttsVoiceMode === mode.id ? "var(--accent)" : "var(--text-muted)",
              }}
            >
              {mode.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-gray-600 mt-1">
          {ttsVoiceMode === "user_select" && "One voice for all announcements"}
          {ttsVoiceMode === "random" && "Different voice each time"}
          {ttsVoiceMode === "context" && "Voice matches announcement type"}
        </p>
      </div>

      {enabled && (
        <>
          {/* Frequency slider */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Frequency: every {frequency} songs
            </label>
            <input
              type="range" min="2" max="10" step="1"
              value={frequency}
              onChange={handleFrequencyChange}
              className="w-full h-2 bg-gray-800 rounded-full appearance-none cursor-pointer accent-green-500"
            />
            <div className="flex justify-between text-xs text-gray-600 mt-1">
              <span>Frequent</span>
              <span>Sparse</span>
            </div>
          </div>

          {/* Caller segments */}
          <div className="flex items-center justify-between">
            <div>
              <label className="block text-sm font-medium text-gray-300">Caller Segments</label>
              <p className="text-xs text-gray-600 mt-1">Fake phone-in callers reacting to your music</p>
            </div>
            <button
              onClick={async () => {
                const newVal = !callerEnabled;
                setCallerEnabled(newVal);
                await saveSetting("talk_caller_enabled", String(newVal));
              }}
              className={`relative w-12 h-6 rounded-full transition-colors ${callerEnabled ? "bg-green-600" : "bg-gray-700"}`}
            >
              <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${callerEnabled ? "translate-x-6" : "translate-x-0.5"}`} />
            </button>
          </div>

          {callerEnabled && (
            <div className="ml-4">
              <label className="block text-xs font-medium text-gray-400 mb-2">Caller Mode</label>
              <div className="flex gap-2">
                {[
                  { value: "influences_queue", label: "Influences Queue" },
                  { value: "narrative_only", label: "Narrative Only" },
                ].map((opt) => (
                  <button
                    key={opt.value}
                    onClick={async () => {
                      setCallerMode(opt.value);
                      await saveSetting("talk_caller_mode", opt.value);
                    }}
                    className="flex-1 px-3 py-2 rounded-lg text-xs font-medium transition"
                    style={{
                      background: callerMode === opt.value ? "var(--accent-muted)" : "var(--bg-elevated)",
                      color: callerMode === opt.value ? "var(--accent)" : "var(--text-muted)",
                    }}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Emotional Caller Toggles */}
          {callerEnabled && (
            <div className="ml-4 space-y-2">
              <label className="block text-xs font-medium text-gray-400">Emotional Callers</label>
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-xs text-gray-300">Heartbroken</span>
                  <p className="text-[10px] text-gray-600">Breakup stories, every song is a reminder</p>
                </div>
                <button
                  onClick={async () => {
                    const newVal = !heartbrokenEnabled;
                    setHeartbrokenEnabled(newVal);
                    await saveSetting("talk_caller_heartbroken_enabled", String(newVal));
                  }}
                  className={`relative w-10 h-5 rounded-full transition-colors ${heartbrokenEnabled ? "bg-green-600" : "bg-gray-700"}`}
                >
                  <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${heartbrokenEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-xs text-gray-300">Melancholy</span>
                  <p className="text-[10px] text-gray-600">Quiet, poetic, speaks in half-sentences</p>
                </div>
                <button
                  onClick={async () => {
                    const newVal = !melancholyEnabled;
                    setMelancholyEnabled(newVal);
                    await saveSetting("talk_caller_melancholy_enabled", String(newVal));
                  }}
                  className={`relative w-10 h-5 rounded-full transition-colors ${melancholyEnabled ? "bg-green-600" : "bg-gray-700"}`}
                >
                  <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${melancholyEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
            </div>
          )}

          {/* DJ Monologue */}
          <div className="flex items-center justify-between">
            <div>
              <label className="block text-sm font-medium text-gray-300">DJ Monologues</label>
              <p className="text-xs text-gray-600 mt-1">Longer reflective segments triggered by milestones and mood shifts</p>
            </div>
            <button
              onClick={async () => {
                const newVal = !monologueEnabled;
                setMonologueEnabled(newVal);
                await saveSetting("talk_monologue_enabled", String(newVal));
              }}
              className={`relative w-12 h-6 rounded-full transition-colors ${monologueEnabled ? "bg-green-600" : "bg-gray-700"}`}
            >
              <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${monologueEnabled ? "translate-x-6" : "translate-x-0.5"}`} />
            </button>
          </div>

          {/* Guest Takeover */}
          <div className="flex items-center justify-between">
            <div>
              <label className="block text-sm font-medium text-gray-300">Guest Takeovers</label>
              <p className="text-xs text-gray-600 mt-1">Guest DJ personas take over for a set of songs with their own style</p>
            </div>
            <button
              onClick={async () => {
                const newVal = !guestTakeoverEnabled;
                setGuestTakeoverEnabled(newVal);
                await saveSetting("talk_guest_takeover_enabled", String(newVal));
              }}
              className={`relative w-12 h-6 rounded-full transition-colors ${guestTakeoverEnabled ? "bg-green-600" : "bg-gray-700"}`}
            >
              <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${guestTakeoverEnabled ? "translate-x-6" : "translate-x-0.5"}`} />
            </button>
          </div>

          {guestTakeoverEnabled && (
            <div className="ml-4">
              <label className="block text-xs font-medium text-gray-400 mb-2">
                Songs per guest set: {guestSongs}
              </label>
              <input
                type="range" min="3" max="6" step="1"
                value={guestSongs}
                onChange={handleGuestSongsChange}
                className="w-full h-2 bg-gray-800 rounded-full appearance-none cursor-pointer accent-green-500"
              />
              <div className="flex justify-between text-xs text-gray-600 mt-1">
                <span>Short</span>
                <span>Long</span>
              </div>
            </div>
          )}

          {/* Segment Cooldown */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Persona cooldown: {segmentCooldown} segments
            </label>
            <input
              type="range" min="1" max="5" step="1"
              value={segmentCooldown}
              onChange={handleCooldownChange}
              className="w-full h-2 bg-gray-800 rounded-full appearance-none cursor-pointer accent-green-500"
            />
            <div className="flex justify-between text-xs text-gray-600 mt-1">
              <span>More variety</span>
              <span>More repeats</span>
            </div>
            <p className="text-[10px] text-gray-600 mt-1">Min segments before a caller/guest can be reused</p>
          </div>

          {/* Voice Models */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Voice Models
              <span className="text-xs text-gray-500 ml-2">{totalReady}/{totalVoices} ready</span>
            </label>

            {/* Voice selector (for user_select mode) */}
            {ttsVoiceMode === "user_select" && voiceStatus?.voices && (
              <div className="mb-3">
                <label className="block text-xs text-gray-400 mb-1.5">Active Voice</label>
                <select
                  value={`${ttsEngine}:${ttsVoice}`}
                  onChange={(e) => {
                    const [eng, ...rest] = e.target.value.split(":");
                    const voice = rest.join(":");
                    handleVoiceChange(voice);
                    if (eng !== ttsEngine) handleEngineChange(eng);
                  }}
                  className="w-full px-3 py-2 rounded-xl bg-gray-800 text-gray-200 text-xs focus:outline-none"
                >
                  {omnivoiceVoices.length > 0 && (
                    <optgroup label="OmniVoice (cloning)">
                      {omnivoiceVoices.map((v) => (
                        <option key={v.id} value={`omnivoice:${v.id}`}>
                          {v.id} {v.ready ? "(ready)" : ""}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {piperVoices.length > 0 && (
                    <optgroup label="Piper (local)">
                      {piperVoices.map((v) => (
                        <option key={v.id} value={`piper:${v.id}`}>
                          {v.id} {v.ready ? "(ready)" : ""}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
              </div>
            )}

            {/* OmniVoice voices */}
            {omnivoiceVoices.length > 0 && (
              <div className="rounded-xl bg-gray-800 p-3 mb-2">
                <div className="text-xs font-medium text-gray-300 mb-2">OmniVoice (cloning)</div>
                <div className="space-y-1">
                  {omnivoiceVoices.map((v) => (
                    <div key={v.id} className="flex items-center gap-2 px-2 py-1">
                      <span className="w-2 h-2 rounded-full" style={{ background: v.ready ? "#4ade80" : "#6b7280" }} />
                      <span className="text-xs text-gray-400">{v.id}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Piper voices */}
            <div className="rounded-xl bg-gray-800 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-gray-300">Piper (fallback)</span>
                <button
                  onClick={handleDownloadVoices}
                  disabled={downloading}
                  className="px-2 py-1 rounded-lg text-[10px] font-medium transition disabled:opacity-40"
                  style={{ background: "var(--accent-muted)", color: "var(--accent)" }}
                >
                  {downloading ? "Downloading..." : "Download All"}
                </button>
              </div>
              <div className="space-y-1">
                {piperVoices.map((v) => (
                  <div key={v.id} className="flex items-center gap-2 px-2 py-1">
                    <span className="w-2 h-2 rounded-full" style={{ background: v.ready ? "#4ade80" : "#6b7280" }} />
                    <span className="text-xs text-gray-400">{v.id}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Info text */}
          <p className="text-xs text-gray-600 italic">
            Talk segments play between songs when TTS is enabled. Each persona uses a different voice. 
            Guest DJs influence song recommendations based on their personality.
          </p>
        </>
      )}
    </div>
  );

  if (masterEnabled) {
    return (
      <div className="pt-4" style={{ borderTop: "1px solid var(--border)" }}>
        {configPanel}
      </div>
    );
  }

  if (!expanded) {
    return (
      <div className="pt-4" style={{ borderTop: "1px solid var(--border)" }}>
        <button
          onClick={() => setExpanded(true)}
          className="w-full flex items-center justify-between px-1 py-2 text-sm font-medium transition"
          style={{ color: "var(--text-primary)" }}
        >
          <span>Radio Talk Show</span>
          <span style={{ color: "var(--text-muted)" }}>+</span>
        </button>
      </div>
    );
  }

  return (
    <div className="pt-4" style={{ borderTop: "1px solid var(--border)" }}>
      <button
        onClick={() => setExpanded(false)}
        className="w-full flex items-center justify-between px-1 py-2 text-sm font-medium transition"
        style={{ color: "var(--text-primary)" }}
      >
        <span>Radio Talk Show</span>
        <span style={{ color: "var(--text-muted)" }}>-</span>
      </button>

      {configPanel}
    </div>
  );
}

export default TalkSettings;
