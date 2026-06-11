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
  const [djSpeechSpeed, setDjSpeechSpeed] = useState(1.0);
  const [djIntroStartHour, setDjIntroStartHour] = useState(6);
  const [djIntroEndHour, setDjIntroEndHour] = useState(23);
  const [talkVolume, setTalkVolume] = useState(100);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [showActiveVoice, setShowActiveVoice] = useState(false);
  const [showVoiceModels, setShowVoiceModels] = useState(false);
  const [downloading, setDownloading] = useState(false);

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
      setDjSpeechSpeed(parseFloat(config.dj_speech_speed) || 1.0);
      setDjIntroStartHour(parseInt(config.dj_intro_start_hour) || 6);
      setDjIntroEndHour(parseInt(config.dj_intro_end_hour) || 23);
      setTalkVolume(parseInt(config.talk_volume) || 100);
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
    loadVoiceStatus();
  }, []);

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

  const handleFrequencyChange = (val) => {
    setFrequency(val);
    saveSetting("talk_frequency", String(val));
  };

  const handleGuestSongsChange = (val) => {
    setGuestSongs(val);
    saveSetting("talk_guest_songs_per_set", String(val));
  };

  const handleCooldownChange = (val) => {
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

  const formatHour = (h) => h === 0 ? "12am" : h < 12 ? `${h}am` : h === 12 ? "12pm" : `${h - 12}pm`;

  return (
    <div>
      {/* TTS Engine */}
      <div className="set-row">
        <div className="set-label">TTS engine</div>
        <div className="seg">
          {[
            { id: "omnivoice", label: "OmniVoice" },
            { id: "piper", label: "Piper" },
          ].map((eng) => (
            <div key={eng.id}
              className={`seg-opt ${ttsEngine === eng.id ? "on" : ""}`}
              onClick={() => handleEngineChange(eng.id)}>
              {eng.label}
            </div>
          ))}
        </div>
      </div>
      {gpuAvailable === false && ttsEngine === "omnivoice" && (
        <div style={{ fontSize: "10px", color: "#eab308", padding: "0 0 4px 0" }}>
          GPU not available — will fall back to Piper
        </div>
      )}

      {/* Voice Selection Mode */}
      <div className="set-row">
        <div className="set-label">Voice selection</div>
        <div className="seg">
          {[
            { id: "user_select", label: "Fixed" },
            { id: "random", label: "Random" },
            { id: "context", label: "By context" },
          ].map((mode) => (
            <div key={mode.id}
              className={`seg-opt ${ttsVoiceMode === mode.id ? "on" : ""}`}
              onClick={() => handleVoiceModeChange(mode.id)}>
              {mode.label}
            </div>
          ))}
        </div>
      </div>
      <div style={{ fontSize: "10px", color: "var(--text-muted)", padding: "0 0 4px 0" }}>
        {ttsVoiceMode === "user_select" && "One voice for all announcements"}
        {ttsVoiceMode === "random" && "Different voice each time"}
        {ttsVoiceMode === "context" && "Voice matches announcement type"}
      </div>

      {/* Voice Models */}
      <div className="set-row" style={{ borderBottom: showVoiceModels ? "none" : undefined, cursor: "pointer" }}
        onClick={() => setShowVoiceModels(!showVoiceModels)}>
        <div className="set-label" style={{ lineHeight: "20px" }}>Voice models</div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span className="set-val" style={{ fontSize: "11px" }}>{totalReady}/{totalVoices} ready</span>
          <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>{showVoiceModels ? "\u2212" : "+"}</span>
        </div>
      </div>

      {showVoiceModels && (
        <>
          {/* Voice selector (for user_select mode) */}
          {ttsVoiceMode === "user_select" && voiceStatus?.voices && (
            <div className="set-row">
              <div className="set-label">Active voice</div>
              <select
                value={`${ttsEngine}:${ttsVoice}`}
                onChange={(e) => {
                  const [eng, ...rest] = e.target.value.split(":");
                  const voice = rest.join(":");
                  handleVoiceChange(voice);
                  if (eng !== ttsEngine) handleEngineChange(eng);
                }}
                style={{ fontSize: "11px", padding: "4px 8px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)", maxWidth: "180px" }}>
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

          {/* OmniVoice voice list */}
          {omnivoiceVoices.length > 0 && (
            <div style={{ padding: "6px 10px", borderRadius: "6px", background: "var(--bg-elevated)", border: "1px solid var(--border)", marginBottom: "8px" }}>
              <div style={{ fontSize: "10px", fontWeight: 500, color: "var(--text-muted)", marginBottom: "4px" }}>OmniVoice (cloning)</div>
              {omnivoiceVoices.map((v) => (
                <div key={v.id} style={{ display: "flex", alignItems: "center", gap: "6px", padding: "2px 0" }}>
                  <div style={{ width: "6px", height: "6px", borderRadius: "50%", background: v.ready ? "var(--accent)" : "var(--text-muted)" }} />
                  <span style={{ fontSize: "11px", color: "var(--text-secondary)" }}>{v.id}</span>
                </div>
              ))}
            </div>
          )}

          {/* Piper voice list */}
          <div style={{ padding: "6px 10px", borderRadius: "6px", background: "var(--bg-elevated)", border: "1px solid var(--border)", marginBottom: "8px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "4px" }}>
              <span style={{ fontSize: "10px", fontWeight: 500, color: "var(--text-muted)" }}>Piper (fallback)</span>
              <button
                onClick={handleDownloadVoices}
                disabled={downloading}
                style={{ fontSize: "10px", padding: "2px 8px", borderRadius: "4px", background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: "pointer", opacity: downloading ? 0.5 : 1 }}>
                {downloading ? "Downloading..." : "Download All"}
              </button>
            </div>
            {piperVoices.map((v) => (
              <div key={v.id} style={{ display: "flex", alignItems: "center", gap: "6px", padding: "2px 0" }}>
                <div style={{ width: "6px", height: "6px", borderRadius: "50%", background: v.ready ? "var(--accent)" : "var(--text-muted)" }} />
                <span style={{ fontSize: "11px", color: "var(--text-secondary)" }}>{v.id}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {masterEnabled && (
        <>
          {/* Frequency */}
          <div className="set-row">
            <div>
              <div className="set-label">Talk frequency</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Every {frequency} songs</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input type="range" min="2" max="10" step="1" value={frequency}
                onChange={(e) => handleFrequencyChange(parseInt(e.target.value))}
                style={{ width: "80px", accentColor: "var(--accent)" }} />
              <span className="set-val">{frequency}</span>
            </div>
          </div>

          {/* Caller Segments */}
          <div className="set-row">
            <div>
              <div className="set-label">Caller segments</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Fake phone-in callers reacting to your music</div>
            </div>
            <div className={`tog ${callerEnabled ? "" : "off"}`}
              onClick={() => { const next = !callerEnabled; setCallerEnabled(next); saveSetting("talk_caller_enabled", String(next)); }}>
              <div className="tog-dot" />
            </div>
          </div>

          {/* Caller Mode */}
          {callerEnabled && (
            <div className="set-row">
              <div className="set-label">Caller mode</div>
              <div className="seg">
                {[
                  { value: "influences_queue", label: "Influences queue" },
                  { value: "narrative_only", label: "Narrative only" },
                ].map((opt) => (
                  <div key={opt.value}
                    className={`seg-opt ${callerMode === opt.value ? "on" : ""}`}
                    onClick={() => { setCallerMode(opt.value); saveSetting("talk_caller_mode", opt.value); }}>
                    {opt.label}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Emotional Callers: Heartbroken */}
          {callerEnabled && (
            <div className="set-row">
              <div>
                <div className="set-label">Heartbroken callers</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Breakup stories, every song is a reminder</div>
              </div>
              <div className={`tog ${heartbrokenEnabled ? "" : "off"}`}
                onClick={() => { const next = !heartbrokenEnabled; setHeartbrokenEnabled(next); saveSetting("talk_caller_heartbroken_enabled", String(next)); }}>
                <div className="tog-dot" />
              </div>
            </div>
          )}

          {/* Emotional Callers: Melancholy */}
          {callerEnabled && (
            <div className="set-row">
              <div>
                <div className="set-label">Melancholy callers</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Quiet, poetic, speaks in half-sentences</div>
              </div>
              <div className={`tog ${melancholyEnabled ? "" : "off"}`}
                onClick={() => { const next = !melancholyEnabled; setMelancholyEnabled(next); saveSetting("talk_caller_melancholy_enabled", String(next)); }}>
                <div className="tog-dot" />
              </div>
            </div>
          )}

          {/* DJ Monologues */}
          <div className="set-row">
            <div>
              <div className="set-label">DJ monologues</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Reflective segments triggered by milestones and mood shifts</div>
            </div>
            <div className={`tog ${monologueEnabled ? "" : "off"}`}
              onClick={() => { const next = !monologueEnabled; setMonologueEnabled(next); saveSetting("talk_monologue_enabled", String(next)); }}>
              <div className="tog-dot" />
            </div>
          </div>

          {/* Guest Takeovers */}
          <div className="set-row">
            <div>
              <div className="set-label">Guest takeovers</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Guest DJ personas take over for a set of songs</div>
            </div>
            <div className={`tog ${guestTakeoverEnabled ? "" : "off"}`}
              onClick={() => { const next = !guestTakeoverEnabled; setGuestTakeoverEnabled(next); saveSetting("talk_guest_takeover_enabled", String(next)); }}>
              <div className="tog-dot" />
            </div>
          </div>

          {/* Songs per guest set */}
          {guestTakeoverEnabled && (
            <div className="set-row">
              <div>
                <div className="set-label">Songs per guest set</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>How long a guest DJ plays</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input type="range" min="3" max="6" step="1" value={guestSongs}
                  onChange={(e) => handleGuestSongsChange(parseInt(e.target.value))}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{guestSongs}</span>
              </div>
            </div>
          )}

          {/* Persona Cooldown */}
          <div className="set-row">
            <div>
              <div className="set-label">Persona cooldown</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>{segmentCooldown} segments before reuse</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input type="range" min="1" max="5" step="1" value={segmentCooldown}
                onChange={(e) => handleCooldownChange(parseInt(e.target.value))}
                style={{ width: "80px", accentColor: "var(--accent)" }} />
              <span className="set-val">{segmentCooldown}</span>
            </div>
          </div>

          {/* DJ Speech Speed */}
          <div className="set-row">
            <div>
              <div className="set-label">DJ speech speed</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>{djSpeechSpeed.toFixed(3)}x</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input type="range" min="0.700" max="1.300" step="0.025" value={djSpeechSpeed}
                onChange={(e) => {
                  const val = parseFloat(e.target.value);
                  setDjSpeechSpeed(val);
                  saveSetting("dj_speech_speed", String(val));
                }}
                style={{ width: "80px", accentColor: "var(--accent)" }} />
              <span className="set-val">{djSpeechSpeed.toFixed(2)}x</span>
            </div>
          </div>

          {/* Talk Segment Volume */}
          <div className="set-row">
            <div>
              <div className="set-label">Talk segment volume</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>{talkVolume}% of main volume</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input type="range" min="50" max="200" step="5" value={talkVolume}
                onChange={(e) => {
                  const val = parseInt(e.target.value);
                  setTalkVolume(val);
                  saveSetting("talk_volume", String(val));
                }}
                style={{ width: "80px", accentColor: "var(--accent)" }} />
              <span className="set-val">{talkVolume}%</span>
            </div>
          </div>

          {/* DJ Intro Hours */}
          <div className="set-row">
            <div>
              <div className="set-label">Song intro hours</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>When DJ intros can play</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <select
                value={djIntroStartHour}
                onChange={(e) => {
                  const val = parseInt(e.target.value);
                  setDjIntroStartHour(val);
                  saveSetting("dj_intro_start_hour", String(val));
                }}
                style={{ fontSize: "11px", padding: "4px 8px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={i}>{formatHour(i)}</option>
                ))}
              </select>
              <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>to</span>
              <select
                value={djIntroEndHour}
                onChange={(e) => {
                  const val = parseInt(e.target.value);
                  setDjIntroEndHour(val);
                  saveSetting("dj_intro_end_hour", String(val));
                }}
                style={{ fontSize: "11px", padding: "4px 8px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={i}>{formatHour(i)}</option>
                ))}
              </select>
            </div>
          </div>
        </>
      )}

      {!masterEnabled && (
        <div style={{ fontSize: "10px", color: "var(--text-muted)", padding: "8px 0 0 0" }}>
          Enable the talk show above to configure segments, callers, and voice settings.
        </div>
      )}
    </div>
  );
}

export default TalkSettings;
