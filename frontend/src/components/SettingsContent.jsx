import React, { useState, useEffect, useRef, useCallback } from "react";
import { Volume2, Brain, Cpu, Folder, ChevronDown, RefreshCw, Trash2, Moon, Radio, Download, Upload, Palette, Monitor } from "lucide-react";
import * as api from "../api";
import TalkSettings from "./TalkSettings";
import IngestPanel from "./IngestPanel";
import LibraryVerify from "./LibraryVerify";

function SettingsContent({ addToast, sectionSpacing, onSectionSpacingChange, rightRailWidth, setRightRailWidth, explorationLevel, onExplorationChange }) {
  const [settings, setSettings] = useState({});
  const [sections, setSections] = useState({ playback: true, appearance: false, system: false, ai: false, talk: false, llm: false, library: false });
  const [announcementInterval, setAnnouncementInterval] = useState(30);
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [musicDir, setMusicDir] = useState("");
  const [localSpacing, setLocalSpacing] = useState(sectionSpacing || "10");
  const [localRailWidth, setLocalRailWidth] = useState(rightRailWidth || 255);
  const [llmStatus, setLlmStatus] = useState(null);
  const [sleepMinutes, setSleepMinutes] = useState(30);
  const [sleepFade, setSleepFade] = useState(30);
  const [sleepChill, setSleepChill] = useState(true);
  const [sleepTimerStatus, setSleepTimerStatus] = useState(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showWipeConfirm, setShowWipeConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [wiping, setWiping] = useState(false);
  const [talkSegmentsEnabled, setTalkSegmentsEnabled] = useState(false);
  const [waveformMode, setWaveformMode] = useState("orbit");
  const [jitterOverride, setJitterOverride] = useState("");
  const [recencyExcludeHours, setRecencyExcludeHours] = useState("1");
  const [vibeLockDuration, setVibeLockDuration] = useState(30);
  const [vibeLockMaxSongs, setVibeLockMaxSongs] = useState(10);
  const [dislikeDecayRate, setDislikeDecayRate] = useState(0.029);
  const [notifyOnSongChange, setNotifyOnSongChange] = useState(true);
  const [autoPauseOnLock, setAutoPauseOnLock] = useState(true);
  const [autoResumeOnUnlock, setAutoResumeOnUnlock] = useState(true);
  const [altHotkeysEnabled, setAltHotkeysEnabled] = useState(true);
  const [volNormEnabled, setVolNormEnabled] = useState(true);
  const [startMinimized, setStartMinimized] = useState(false);
  const [startOnLogin, setStartOnLogin] = useState(false);
  const [miniPlayerEnabled, setMiniPlayerEnabled] = useState(true);
  const [widgetEnabled, setWidgetEnabled] = useState(false);
  const [showLyrics, setShowLyrics] = useState(true);
  const [hotkeyBindings, setHotkeyBindings] = useState({});
  const [capturingKey, setCapturingKey] = useState(null);
  const [accentColor, setAccentColor] = useState("#4eccc4");
  const [eqPreset, setEqPreset] = useState("off");
  const [commandResponseTimeout, setCommandResponseTimeout] = useState(4);
  const [commandInactiveTimeout, setCommandInactiveTimeout] = useState(6);
  const debounceRef = useRef(null);
  const importFileRef = useRef(null);

  const save = useCallback(async (key, value, showOk = false) => {
    try {
      await api.updateSetting(key, value);
      if (showOk) addToast?.("Saved", "success");
    } catch (e) {
      addToast?.(`Failed to save ${key}`, "error");
    }
  }, [addToast]);

  const debouncedSave = useCallback((key, value) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => save(key, value), 350);
  }, [save]);

  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  useEffect(() => { setLocalSpacing(sectionSpacing || "10"); }, [sectionSpacing]);
  useEffect(() => { setLocalRailWidth(rightRailWidth || 255); }, [rightRailWidth]);

  useEffect(() => {
    fetchSettings();
    fetchLlmStatus();
    const sleepPoll = setInterval(async () => {
      try {
        const data = await api.fetchSleepTimer();
        setSleepTimerStatus(data.active ? data : null);
      } catch (e) {}
    }, 5000);
    return () => { clearInterval(sleepPoll); };
  }, [fetchSubfolders]);

  const fetchSettings = async () => {
    let data;
    try {
      data = await api.fetchSettings();
    } catch (err) {
      addToast?.("Failed to load settings from server", "error");
      return;
    }
    setSettings(data);

    // Layout-critical settings first — before any Electron IPC that might throw
    if (data.right_rail_width) {
      const w = parseInt(data.right_rail_width) || 255;
      setLocalRailWidth(w);
      setRightRailWidth?.(w);
      document.documentElement.style.setProperty("--right-rail-width", w + "px");
    }
    if (data.section_spacing) {
      setLocalSpacing(data.section_spacing);
      onSectionSpacingChange?.(data.section_spacing);
      document.documentElement.style.setProperty("--section-gap", data.section_spacing === "6" ? "0.5rem" : data.section_spacing === "8" ? "1rem" : data.section_spacing === "12" ? "1.5rem" : "1rem");
    }

    // Remaining settings (each Electron IPC call wrapped individually)
    setAnnouncementInterval(data.announcement_interval ?? 30);
    setTtsEnabled(data.tts_enabled !== "false");
    setMusicDir(data.music_dir || "");
    setTalkSegmentsEnabled(data.talk_segments_enabled === "true");
    setWaveformMode(data.waveform_mode || "orbit");
    setJitterOverride(data.jitter_override || "");
    setRecencyExcludeHours(data.recency_exclude_hours || "1");
    setVibeLockDuration(parseInt(data.vibe_lock_duration_minutes) || 30);
    setVibeLockMaxSongs(parseInt(data.vibe_lock_max_songs) || 10);
    setDislikeDecayRate(parseFloat(data.dislike_decay_rate) || 0.029);
    setAltHotkeysEnabled(data.alt_hotkeys_enabled !== "false");
    setVolNormEnabled(data.volume_normalization !== "false");
    setStartMinimized(data.start_minimized === "true");
    setShowLyrics(data.show_lyrics !== "false");
    const miniPlayerVal = data.mini_player_enabled !== "false";
    setMiniPlayerEnabled(miniPlayerVal);
    try { if (window.electron?.setSetting) window.electron.setSetting("mini_player_enabled", String(miniPlayerVal)); } catch (_) {}

    const widgetVal = data.widget_enabled === "true";
    setWidgetEnabled(widgetVal);
    try { if (window.electron?.setSetting) window.electron.setSetting("widget_enabled", String(widgetVal)); } catch (_) {}

    const notifyValue = data.notify_on_song_change !== "false";
    setNotifyOnSongChange(notifyValue);
    try { if (window.electron?.setSetting) window.electron.setSetting("notify_on_song_change", String(notifyValue)); } catch (_) {}

    const pauseValue = data.auto_pause_on_lock !== "false";
    setAutoPauseOnLock(pauseValue);
    try { if (window.electron?.setSetting) window.electron.setSetting("auto_pause_on_lock", String(pauseValue)); } catch (_) {}

    const resumeValue = data.auto_resume_on_unlock !== "false";
    setAutoResumeOnUnlock(resumeValue);
    try { if (window.electron?.setSetting) window.electron.setSetting("auto_resume_on_unlock", String(resumeValue)); } catch (_) {}

    setAccentColor(data.accent_color || "#4eccc4");
    const loadedEq = data.eq_preset || "off";
    setEqPreset(loadedEq);
    if (loadedEq !== "off") {
      try { api.setEqPreset(loadedEq); } catch (_) {}
    }
    setCommandResponseTimeout(parseInt(data.command_response_timeout) || 10);
    setCommandInactiveTimeout(parseInt(data.command_inactive_timeout) || 6);
    let parsedBindings = {};
    try { parsedBindings = JSON.parse(data.hotkey_bindings || "{}"); } catch (_) {}
    setHotkeyBindings(parsedBindings);
    try { if (window.electron?.reloadHotkeys) window.electron.reloadHotkeys(parsedBindings); } catch (_) {}

    const loginValue = data.start_on_login === "true";
    setStartOnLogin(loginValue);
    try { if (window.electron?.setStartOnLogin) window.electron.setStartOnLogin(loginValue); } catch (_) {}
  };

  const fetchLlmStatus = async () => {
    try {
      const data = await api.fetchLlmStatus();
      setLlmStatus(data);
    } catch (err) {
      setLlmStatus({ chat: { openai: { healthy: false }, ollama: { healthy: false } }, embed: { openai: { healthy: false }, ollama: { healthy: false } } });
    }
  };

  const toggleSection = (key) => setSections((s) => ({ ...s, [key]: !s[key] }));

  const llmAnyOffline = llmStatus && (
    (!llmStatus.chat?.openai?.healthy && !llmStatus.chat?.ollama?.healthy) ||
    (!llmStatus.embed?.openai?.healthy && !llmStatus.embed?.ollama?.healthy)
  );

  return (
    <div>
      {/* Playback & audio */}
      <div className="set-sec">
        <div className="set-hdr" onClick={() => toggleSection("playback")}>
          <div className="icon-chip" style={{ background: "#0d3028" }}>
            <Volume2 size={13} style={{ color: "var(--accent)" }} />
          </div>
          <span style={{ color: "var(--text-primary)", fontSize: "13px", fontWeight: 500 }}>Playback & audio</span>
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: sections.playback ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {sections.playback && (
          <div className="set-body">
            <div className="set-row">
              <div>
                <div className="set-label">TTS announcements</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>DJ voice between songs</div>
              </div>
              <div className={`tog ${ttsEnabled ? "" : "off"}`} onClick={() => { const next = !ttsEnabled; setTtsEnabled(next); save("tts_enabled", String(next), true); }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Lyrics</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Karaoke-style lyrics panel</div>
              </div>
              <div className={`tog ${showLyrics ? "" : "off"}`} onClick={() => { const next = !showLyrics; setShowLyrics(next); save("show_lyrics", String(next), true); }}>
                <div className="tog-dot" />
              </div>
            </div>
           <div className="set-row">
               <div className="set-label">Announcement interval</div>
               <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                 <input type="range" min="0" max="300" value={announcementInterval} step="15"
                   onChange={(e) => { const v = parseInt(e.target.value) || 0; setAnnouncementInterval(v); debouncedSave("announcement_interval", String(v)); }}
                   style={{ width: "90px", accentColor: "var(--accent)" }} />
                 <span className="set-val">{announcementInterval}s</span>
               </div>
             </div>
              <div className="set-row">
                <div>
                  <div className="set-label">Volume normalization</div>
                  <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>ReplayGain-style per-song loudness offset</div>
                </div>
                <div className={`tog ${volNormEnabled ? "" : "off"}`} onClick={async () => {
                  try {
                    const data = await api.post(`/api/play/volume-normalization?enabled=${!volNormEnabled}`);
                    setVolNormEnabled(!volNormEnabled);
                  } catch (e) {
                    console.error("Failed to toggle volume normalization", e);
                  }
                }}>
                  <div className="tog-dot" />
                </div>
              </div>

                <div className="set-row">
                  <div>
                    <div className="set-label">Equalizer</div>
                    <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Audio EQ preset</div>
                  </div>
                  <div className="seg">
                    {[
                      { value: "off", label: "Off" },
                      { value: "bass_boost", label: "Bass" },
                      { value: "vocal_boost", label: "Vocal" },
                      { value: "night_mode", label: "Night" },
                      { value: "flat", label: "Flat" },
                      { value: "loudness", label: "Loud" },
                      { value: "clarity", label: "Clarity" },
                    ].map((opt) => (
                      <div key={opt.value} className={`seg-opt ${eqPreset === opt.value ? "on" : ""}`}
                        onClick={async () => {
                          setEqPreset(opt.value);
                          save("eq_preset", opt.value);
                          try { await api.setEqPreset(opt.value); } catch (_) {}
                        }}
                        style={{ fontSize: "10px", padding: "3px 7px" }}>
                        {opt.label}
                      </div>
                    ))}
                  </div>
                </div>

              <div className="set-row" style={{ borderBottom: "none", flexDirection: "column", alignItems: "stretch" }}>
              <div className="set-label" style={{ marginBottom: "8px" }}>
                <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <Moon size={12} />
                  Sleep Timer
                </span>
              </div>
              <div style={{ display: "flex", gap: "4px", marginBottom: "8px", flexWrap: "wrap" }}>
                {[15, 30, 45, 60, 90].map((m) => (
                  <div key={m} className={`seg-opt ${sleepMinutes === m ? "on" : ""}`}
                    onClick={() => setSleepMinutes(m)} style={{ fontSize: "10px", padding: "3px 10px" }}>
                    {m}m
                  </div>
                ))}
                <div className={`seg-opt ${sleepMinutes === 0 ? "on" : ""}`}
                  onClick={() => setSleepMinutes(0)} style={{ fontSize: "10px", padding: "3px 10px" }}>
                  Song end
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Fade last</span>
                <select value={sleepFade} onChange={(e) => setSleepFade(parseInt(e.target.value))}
                  style={{ fontSize: "10px", padding: "3px 8px", borderRadius: "4px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                  <option value={0}>No fade</option>
                  <option value={15}>15s</option>
                  <option value={30}>30s</option>
                  <option value={60}>60s</option>
                </select>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Switch to chill before sleep</span>
                <div className={`tog ${sleepChill ? "" : "off"}`} onClick={() => setSleepChill(!sleepChill)}>
                  <div className="tog-dot" />
                </div>
              </div>
              <button onClick={async () => {
                try {
                  await api.setSleepTimer(sleepMinutes, sleepFade, sleepChill);
                  addToast?.("Sleep timer started", "success");
                } catch (e) { addToast?.("Failed to start timer", "error"); }
              }}
                style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: "pointer", width: "100%" }}>
                Start sleep timer
              </button>
              {sleepTimerStatus?.active && (
                <div style={{ marginTop: "8px", padding: "6px 10px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: "11px", color: "var(--accent)" }}>
                    {Math.floor(sleepTimerStatus.remaining_seconds / 60)}m {sleepTimerStatus.remaining_seconds % 60}s remaining
                    {sleepTimerStatus.fading && " (fading)"}
                  </span>
                  <button onClick={async () => {
                    try {
                      await api.cancelSleepTimer();
                      setSleepTimerStatus(null);
                      addToast?.("Timer cancelled", "success");
                    } catch (e) { addToast?.("Failed to cancel", "error"); }
                  }}
                    style={{ fontSize: "10px", padding: "3px 8px", borderRadius: "4px", background: "#1e0808", border: "1px solid #3a1010", color: "var(--danger)", cursor: "pointer" }}>
                    Cancel
                  </button>
                </div>
              )}
            </div>

          </div>
        )}
      </div>

      {/* Appearance */}
      <div className="set-sec">
        <div className="set-hdr" onClick={() => toggleSection("appearance")}>
          <div className="icon-chip" style={{ background: "#1a2030" }}>
            <Palette size={13} style={{ color: "#a870f0" }} />
          </div>
          <span style={{ color: "var(--text-primary)", fontSize: "13px", fontWeight: 500 }}>Appearance</span>
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: sections.appearance ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {sections.appearance && (
          <div className="set-body">
            <div className="set-row">
              <div className="set-label">Section spacing</div>
              <div className="seg">
                {[
                  { value: "6", label: "Compact" },
                  { value: "8", label: "Normal" },
                  { value: "10", label: "Relaxed" },
                  { value: "12", label: "Spacious" },
                ].map((opt) => (
                  <div key={opt.value} className={`seg-opt ${localSpacing === opt.value ? "on" : ""}`}
                    onClick={() => { setLocalSpacing(opt.value); onSectionSpacingChange?.(opt.value); save("section_spacing", opt.value, true); }}>
                    {opt.label}
                  </div>
                ))}
              </div>
            </div>
            <div className="set-row">
              <div className="set-label">Waveform style</div>
              <div className="seg">
                {["orbit", "pulse", "float"].map((opt) => (
                  <div key={opt} className={`seg-opt ${waveformMode === opt ? "on" : ""}`}
                    onClick={() => { setWaveformMode(opt); save("waveform_mode", opt, true); }}>
                    {opt.charAt(0).toUpperCase() + opt.slice(1)}
                  </div>
                ))}
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Accent color</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Theme accent color</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                {["#4eccc4", "#a870f0", "#f0b44c", "#4ade80", "#f47b8a", "#5eadf5"].map((c) => (
                  <div key={c} onClick={() => {
                    setAccentColor(c);
                    const root = document.documentElement;
                    root.style.setProperty("--user-accent", c);
                    root.style.setProperty("--user-accent-hover", c + "cc");
                    root.style.setProperty("--user-accent-muted", c + "21");
                    save("accent_color", c, true);
                  }} style={{
                    width: "20px", height: "20px", borderRadius: "50%", background: c, cursor: "pointer",
                    border: accentColor === c ? "2px solid white" : "2px solid transparent",
                    outline: accentColor === c ? "2px solid " + c : "none",
                    transition: "all 0.15s",
                  }} />
                ))}
              </div>
            </div>
            <div className="set-row" style={{ borderBottom: "none" }}>
              <div className="set-label">Right panel width</div>
              <div className="seg">
                {[
                  { value: "200", label: "Narrow" },
                  { value: "280", label: "Medium" },
                  { value: "360", label: "Wide" },
                  { value: "440", label: "Extra wide" },
                ].map((opt) => (
                  <div key={opt.value} className={`seg-opt ${localRailWidth === parseInt(opt.value) ? "on" : ""}`}
                    onClick={() => { setLocalRailWidth(parseInt(opt.value)); setRightRailWidth?.(parseInt(opt.value)); save("right_rail_width", opt.value, true); }}>
                    {opt.label}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* System */}
      <div className="set-sec">
        <div className="set-hdr" onClick={() => toggleSection("system")}>
          <div className="icon-chip" style={{ background: "#1e1e1e" }}>
            <Monitor size={13} style={{ color: "#8a9bb5" }} />
          </div>
          <span style={{ color: "var(--text-primary)", fontSize: "13px", fontWeight: 500 }}>System</span>
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: sections.system ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {sections.system && (
          <div className="set-body">
            <div className="set-row">
              <div>
                <div className="set-label">Start minimized</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Launch in system tray (no window)</div>
              </div>
              <div className={`tog ${startMinimized ? "" : "off"}`} onClick={() => { const next = !startMinimized; setStartMinimized(next); save("start_minimized", String(next), true); }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Mini player</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Compact small-window mode</div>
              </div>
              <div className={`tog ${miniPlayerEnabled ? "" : "off"}`} onClick={() => { const next = !miniPlayerEnabled; setMiniPlayerEnabled(next); save("mini_player_enabled", String(next), true); try { if (window.electron?.setSetting) window.electron.setSetting("mini_player_enabled", String(next)); } catch (_) {} }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Now Playing widget</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Floating overlay when main window is hidden</div>
              </div>
              <div className={`tog ${widgetEnabled ? "" : "off"}`} onClick={() => { const next = !widgetEnabled; setWidgetEnabled(next); save("widget_enabled", String(next), true); try { if (window.electron?.setSetting) window.electron.setSetting("widget_enabled", String(next)); } catch (_) {} }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Start on login</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Auto-launch Radio when you log in to Windows</div>
              </div>
              <div className={`tog ${startOnLogin ? "" : "off"}`} onClick={async () => {
                const next = !startOnLogin;
                setStartOnLogin(next);
                if (window.electron?.setStartOnLogin) {
                  window.electron.setStartOnLogin(next);
                }
                save("start_on_login", String(next));
              }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Song change notifications</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Show notification when song changes</div>
              </div>
              <div className={`tog ${notifyOnSongChange ? "" : "off"}`} onClick={() => { const next = !notifyOnSongChange; setNotifyOnSongChange(next); save("notify_on_song_change", String(next), true); window.electron?.setSetting("notify_on_song_change", String(next)); }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Pause on screen lock</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Auto-pause when screen locks or system sleeps</div>
              </div>
              <div className={`tog ${autoPauseOnLock ? "" : "off"}`} onClick={() => { const next = !autoPauseOnLock; setAutoPauseOnLock(next); save("auto_pause_on_lock", String(next), true); window.electron?.setSetting("auto_pause_on_lock", String(next)); }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Resume on unlock</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Resume playback when screen unlocks</div>
              </div>
              <div className={`tog ${autoResumeOnUnlock ? "" : "off"}`} onClick={() => { const next = !autoResumeOnUnlock; setAutoResumeOnUnlock(next); save("auto_resume_on_unlock", String(next), true); window.electron?.setSetting("auto_resume_on_unlock", String(next)); }}>
                <div className="tog-dot" />
              </div>
            </div>
            <div className="set-row" style={{ borderBottom: "none" }}>
              <div>
                <div className="set-label">Keyboard shortcuts</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Alt+H heart, Alt+D dislike, Alt+V vibe lock, Alt+1-5 mood</div>
              </div>
              <div className={`tog ${altHotkeysEnabled ? "" : "off"}`} onClick={() => { const next = !altHotkeysEnabled; setAltHotkeysEnabled(next); save("alt_hotkeys_enabled", String(next), true); }}>
                <div className="tog-dot" />
              </div>
            </div>
            {/* Keyboard Shortcuts */}
            <div className="set-row" style={{ flexDirection: "column", alignItems: "stretch", borderBottom: "none" }}>
              <div className="set-label" style={{ marginBottom: "6px" }}>
                <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <span size={12} style={{ fontSize: "12px" }}>⌨</span>
                  Custom bindings
                </span>
              </div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginBottom: "6px" }}>Click a shortcut to change it, then press the new key</div>
              {Object.entries({
                heart: "Heart song",
                thumbs_down: "Thumbs down",
                vibe_lock: "Toggle vibe lock",
                mood_chill: "Mood: Chill",
                mood_energetic: "Mood: Energetic",
                mood_focus: "Mood: Focus",
                mood_melancholy: "Mood: Melancholy",
                mood_discovery: "Mood: Discovery",
                volume_up: "Volume up",
                volume_down: "Volume down",
              }).map(([action, label]) => (
                <div key={action} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                  <span style={{ fontSize: "11px", color: "var(--text-secondary)" }}>{label}</span>
                  <div
                    onClick={() => setCapturingKey(capturingKey === action ? null : action)}
                    onKeyDown={(e) => {
                      if (capturingKey !== action) return;
                      e.preventDefault();
                      e.stopPropagation();
                      const parts = [];
                      if (e.ctrlKey) parts.push("Ctrl");
                      if (e.altKey) parts.push("Alt");
                      if (e.shiftKey) parts.push("Shift");
                      const key = e.key === " " ? "Space" : e.key.length === 1 ? e.key.toUpperCase() : e.key;
                      if (!["Control", "Alt", "Shift", "Meta"].includes(key)) {
                        parts.push(key);
                        const combo = parts.join("+");
                        const newBindings = { ...hotkeyBindings, [action]: combo };
                        setHotkeyBindings(newBindings);
                        save("hotkey_bindings", JSON.stringify(newBindings));
                        if (window.electron?.reloadHotkeys) {
                          window.electron.reloadHotkeys(newBindings);
                        }
                        setCapturingKey(null);
                      }
                    }}
                    tabIndex={capturingKey === action ? 0 : undefined}
                    style={{
                      padding: "3px 10px", borderRadius: "4px", fontSize: "11px", fontFamily: "monospace", cursor: "pointer", minWidth: "80px", textAlign: "center",
                      background: capturingKey === action ? "rgba(78,204,196,0.15)" : "var(--bg-elevated)",
                      border: `1px solid ${capturingKey === action ? "var(--accent)" : "var(--border)"}`,
                      color: capturingKey === action ? "var(--accent)" : "var(--text-secondary)",
                      outline: "none",
                    }}
                    autoFocus={capturingKey === action}
                  >
                    {capturingKey === action ? "Press a key..." : (hotkeyBindings[action] || "—")}
                  </div>
                </div>
              ))}
              <div style={{ display: "flex", gap: "4px", marginTop: "4px" }}>
                <button onClick={() => {
                  const defaults = {"heart":"Alt+H","thumbs_down":"Alt+D","vibe_lock":"Alt+V","mood_chill":"Alt+1","mood_energetic":"Alt+2","mood_focus":"Alt+3","mood_melancholy":"Alt+4","mood_discovery":"Alt+5","volume_up":"Alt+Up","volume_down":"Alt+Down"};
                  setHotkeyBindings(defaults);
                  save("hotkey_bindings", JSON.stringify(defaults));
                  if (window.electron?.reloadHotkeys) window.electron.reloadHotkeys(defaults);
                  addToast?.("Hotkeys reset to defaults", "success");
                }}
                  style={{ fontSize: "10px", padding: "3px 8px", borderRadius: "4px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer" }}>
                  Reset defaults
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* AI & discovery */}
      <div className="set-sec">
        <div className="set-hdr" onClick={() => toggleSection("ai")}>
          <div className="icon-chip" style={{ background: "#0d1e40" }}>
            <Brain size={13} style={{ color: "#5eadf5" }} />
          </div>
          <span style={{ color: "var(--text-primary)", fontSize: "13px", fontWeight: 500 }}>AI & discovery</span>
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: sections.ai ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {sections.ai && (
          <div className="set-body">
            <div className="set-row">
              <div>
                <div className="set-label">Diversity</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Controls how adventurous and varied your queue is</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Stable</span>
                <input type="range" min="0" max="100" value={Math.round(explorationLevel * 100)} step="5"
                  onChange={(e) => { const v = parseInt(e.target.value) / 100; onExplorationChange?.(v); debouncedSave("exploration_level", String(v)); }}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{Math.round(explorationLevel * 100)}%</span>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Wild</span>
              </div>
            </div>
            <div className="set-row" style={{ opacity: 0.6, fontSize: "11px", color: "var(--text-muted)", paddingLeft: "12px" }}>
              At {Math.round(explorationLevel * 100)}%: {explorationLevel < 0.4 ? "Familiar songs, consistent picks" : explorationLevel < 0.7 ? "Balanced mix of familiar and fresh" : "Adventurous picks, more variety"}
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Advanced diversity</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Manually override per-cycle variety</div>
              </div>
              <div className={`tog ${jitterOverride ? "" : "off"}`}
                onClick={() => {
                  if (jitterOverride) {
                    setJitterOverride("");
                    save("jitter_override", "", true);
                  } else {
                    const derived = 0.03 + explorationLevel * 0.13;
                    setJitterOverride(String(derived));
                    save("jitter_override", String(derived), true);
                  }
                }}>
                <div className="tog-dot" />
              </div>
            </div>
            {jitterOverride && (
              <div className="set-row">
                <div>
                  <div className="set-label">Per-cycle variety</div>
                  <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>How much candidates vary each rebuild</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Stable</span>
                  <input type="range" min="0" max="100" value={Math.round(((parseFloat(jitterOverride) - 0.03) / 0.13) * 100)} step="5"
                    onChange={(e) => { const pct = parseInt(e.target.value); const v = 0.03 + (pct / 100) * 0.13; setJitterOverride(String(v)); debouncedSave("jitter_override", String(v)); }}
                    style={{ width: "80px", accentColor: "var(--accent)" }} />
                  <span className="set-val">{Math.round(((parseFloat(jitterOverride) - 0.03) / 0.13) * 100)}%</span>
                  <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Mix</span>
                </div>
              </div>
            )}
            <div className="set-row">
              <div>
                <div className="set-label">Recently played</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>How long before a song can reappear</div>
              </div>
              <div className="seg">
                {[
                  { value: "0.5", label: "30m" },
                  { value: "1", label: "1h" },
                  { value: "3", label: "3h" },
                  { value: "6", label: "6h" },
                  { value: "12", label: "12h" },
                ].map((opt) => (
                  <div key={opt.value} className={`seg-opt ${recencyExcludeHours === opt.value ? "on" : ""}`}
                    onClick={() => { setRecencyExcludeHours(opt.value); save("recency_exclude_hours", opt.value, true); }}
                    style={{ fontSize: "10px", padding: "3px 8px" }}>
                    {opt.label}
                  </div>
                ))}
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Command response timeout</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Auto-dismiss command reply (seconds)</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input type="range" min="0" max="30" step="1" value={commandResponseTimeout}
                  onChange={(e) => { const v = parseInt(e.target.value) || 10; setCommandResponseTimeout(v); debouncedSave("command_response_timeout", String(v)); }}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{commandResponseTimeout}s</span>
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Command bar inactivity</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Auto-collapse command bar (seconds, 0=never)</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input type="range" min="0" max="60" step="1" value={commandInactiveTimeout}
                  onChange={(e) => { const v = parseInt(e.target.value) || 6; setCommandInactiveTimeout(v); debouncedSave("command_inactive_timeout", String(v)); }}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{commandInactiveTimeout > 0 ? commandInactiveTimeout + "s" : "∞"}</span>
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Vibe lock length</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>How long a vibe lock lasts</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input type="range" min="10" max="60" step="5" value={vibeLockDuration}
                  onChange={(e) => { const v = parseInt(e.target.value); setVibeLockDuration(v); debouncedSave("vibe_lock_duration_minutes", String(v)); }}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{vibeLockDuration}m</span>
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Max vibe lock songs</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Songs before vibe lock expires</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input type="range" min="5" max="20" step="5" value={vibeLockMaxSongs}
                  onChange={(e) => { const v = parseInt(e.target.value); setVibeLockMaxSongs(v); debouncedSave("vibe_lock_max_songs", String(v)); }}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{vibeLockMaxSongs}</span>
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Dislike memory</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>How long skips stay influential</div>
              </div>
              <div className="seg">
                {[
                  { value: "0.058", label: "12h" },
                  { value: "0.029", label: "1 day" },
                  { value: "0.014", label: "2 days" },
                  { value: "0.007", label: "4 days" },
                ].map((opt) => (
                  <div key={opt.value} className={`seg-opt ${String(dislikeDecayRate) === opt.value ? "on" : ""}`}
                    onClick={() => { setDislikeDecayRate(parseFloat(opt.value)); save("dislike_decay_rate", opt.value, true); }}
                    style={{ fontSize: "10px", padding: "3px 8px" }}>
                    {opt.label}
                  </div>
                ))}
              </div>
            </div>
            <div className="set-row">
              <div className="set-label">Mode</div>
              <div className="seg">
                <div className={`seg-opt ${settings.current_mode === "radio" ? "on" : ""}`}
                  onClick={() => { setSettings((s) => ({ ...s, current_mode: "radio" })); save("current_mode", "radio", true); }}>Radio</div>
                <div className={`seg-opt ${settings.current_mode === "normal" ? "on" : ""}`}
                  onClick={() => { setSettings((s) => ({ ...s, current_mode: "normal" })); save("current_mode", "normal", true); }}>Normal</div>
              </div>
            </div>
            <div className="set-row" style={{ borderBottom: "none" }}>
              <div className="set-label">Detected archetype</div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <div style={{ width: "7px", height: "7px", borderRadius: "50%", background: "#5eadf5" }} />
                <span className="set-val">{settings.archetype ? settings.archetype.replace(/_/g, " ") : "Mood listener"}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Radio Talk Show */}
      <div className="set-sec">
        <div className="set-hdr" onClick={() => toggleSection("talk")}>
          <div className="icon-chip" style={{ background: "#1e0d2e" }}>
            <Radio size={13} style={{ color: "#c084fc" }} />
          </div>
          <span style={{ color: "var(--text-primary)", fontSize: "13px", fontWeight: 500 }}>Radio Talk Show</span>
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: sections.talk ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {sections.talk && (
          <div className="set-body">
            <div className="set-row">
              <div>
                <div className="set-label">Enable talk show</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Caller segments, DJ monologues, guest takeovers</div>
              </div>
              <div className={`tog ${talkSegmentsEnabled ? "" : "off"}`}
                onClick={() => { const next = !talkSegmentsEnabled; setTalkSegmentsEnabled(next); save("talk_segments_enabled", String(next), true); }}>
                <div className="tog-dot" />
              </div>
            </div>
            <TalkSettings addToast={addToast} masterEnabled={talkSegmentsEnabled} />
          </div>
        )}
      </div>

      {/* LLM providers */}
      <div className="set-sec">
        <div className="set-hdr" onClick={() => toggleSection("llm")}>
          <div className="icon-chip" style={{ background: "#1e0d0d" }}>
            <Cpu size={13} style={{ color: "var(--danger)" }} />
          </div>
          <span style={{ color: "var(--text-primary)", fontSize: "13px", fontWeight: 500 }}>LLM providers</span>
          {llmAnyOffline && <div style={{ width: "7px", height: "7px", borderRadius: "50%", background: "var(--danger)", marginLeft: "8px" }} />}
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: sections.llm ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {sections.llm && (
          <div className="set-body">
            {!llmStatus ? (
              <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>Loading...</div>
            ) : (
              <>
                {/* Chat providers */}
                {["openai", "ollama"].map((p) => {
                  const st = llmStatus.chat?.[p];
                  if (!st) return null;
                  const isActive = llmStatus.active_chat === p;
                  const label = p === "openai" ? "OpenAI-compatible" : "Ollama";
                  return (
                    <div key={`chat-${p}`} className="llm-row" style={p === "ollama" ? { paddingTop: "10px", borderTop: "1px solid var(--border-subtle)" } : undefined}>
                      <div className={`dot ${st.healthy ? "dot-ok" : "dot-err"}`} />
                      <div style={{ flex: 1 }}>
                        <div style={{ color: "var(--text-primary)", fontSize: "12px" }}>
                          Chat: {label} {isActive && <span style={{ color: "var(--accent)", fontWeight: 500 }}>(active)</span>}
                        </div>
                        <div style={{ fontSize: "10px", color: "var(--text-muted)" }}>{st.model || ""}</div>
                        {!st.healthy && (
                          <div style={{ fontSize: "10px", color: "var(--danger)", marginTop: "1px" }}>{st.error}</div>
                        )}
                      </div>
                    </div>
                  );
                })}
                {/* Embed providers */}
                {["openai", "ollama"].map((p) => {
                  const st = llmStatus.embed?.[p];
                  if (!st) return null;
                  const isActive = llmStatus.active_embed === p;
                  const label = p === "openai" ? "OpenAI-compatible" : "Ollama";
                  return (
                    <div key={`embed-${p}`} className="llm-row" style={{ paddingTop: "10px", borderTop: "1px solid var(--border-subtle)" }}>
                      <div className={`dot ${st.healthy ? "dot-ok" : "dot-err"}`} />
                      <div style={{ flex: 1 }}>
                        <div style={{ color: "var(--text-primary)", fontSize: "12px" }}>
                          Embed: {label} {isActive && <span style={{ color: "var(--accent)", fontWeight: 500 }}>(active)</span>}
                        </div>
                        <div style={{ fontSize: "10px", color: "var(--text-muted)" }}>{st.model || ""}</div>
                        {!st.healthy && (
                          <div style={{ fontSize: "10px", color: "var(--danger)", marginTop: "1px" }}>{st.error}</div>
                        )}
                      </div>
                    </div>
                  );
                })}
                <div style={{ marginTop: "10px", padding: "8px 10px", background: "var(--bg-elevated)", borderRadius: "6px", border: "1px solid var(--border)", fontSize: "11px", color: "var(--text-secondary)" }}>
                  Click Retry to re-check all providers
                </div>
               </>
              )}
            </div>
        )}
      </div>

      {/* Library management */}
      <div className="set-sec" style={{ borderBottom: "none" }}>
        <div className="set-hdr" onClick={() => toggleSection("library")}>
          <div className="icon-chip" style={{ background: "#0f1e10" }}>
            <Folder size={13} style={{ color: "#639922" }} />
          </div>
          <span style={{ color: "var(--text-primary)", fontSize: "13px", fontWeight: 500 }}>Library management</span>
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: sections.library ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {sections.library && (
          <div className="set-body">
            <div className="set-row">
              <div>
                <div className="set-label">Music directory</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Restart required after change</div>
              </div>
              <div style={{ fontSize: "11px", color: "var(--accent)", fontFamily: "monospace" }}>
                {musicDir || "audio_files/"}
              </div>
            </div>
            <IngestPanel addToast={addToast} />

            <LibraryVerify addToast={addToast} />

            <div style={{ paddingTop: "12px" }}>
              <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
                <button onClick={async () => {
                  try {
                    const data = await api.exportSettings();
                    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `radio-settings-${new Date().toISOString().split("T")[0]}.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                    addToast?.("Settings exported", "success");
                  } catch (e) { addToast?.("Export failed", "error"); }
                }}
                  style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <Download size={12} /> Export
                </button>
                <input type="file" accept=".json" hidden ref={importFileRef}
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    try {
                      const text = await file.text();
                      const data = JSON.parse(text);
                      const result = await api.importSettings(data);
                      addToast?.(`Imported: ${result.settings_count} settings, ${result.collections_count} collections, ${result.song_metadata_count} metadata items`, "success");
                    } catch (err) { addToast?.("Import failed: " + (err.message || "Invalid file"), "error"); }
                    e.target.value = "";
                  }} />
                <button onClick={() => importFileRef.current?.click()}
                  style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <Upload size={12} /> Import
                </button>
              </div>
            </div>

            <div style={{ paddingTop: "12px", borderTop: "1px solid var(--border-subtle)" }}>
              <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginBottom: "6px" }}>Danger zone</div>
              {showResetConfirm ? (
                <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                  <button onClick={async () => {
                    setResetting(true);
                    try {
                      await api.resetRecommendations();
                      addToast?.("Recommendations reset. Restart to re-seed.", "success");
                      setShowResetConfirm(false);
                    } catch (e) { addToast?.("Reset failed", "error"); }
                    setResetting(false);
                  }} disabled={resetting}
                    style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "#1e0808", border: "1px solid #3a1010", color: "var(--danger)", cursor: "pointer" }}>
                    {resetting ? "Resetting..." : "Yes, reset everything"}
                  </button>
                  <button onClick={() => setShowResetConfirm(false)}
                    style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer" }}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button onClick={() => setShowResetConfirm(true)}
                  style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "#1e0808", border: "1px solid #3a1010", color: "var(--danger)", cursor: "pointer", display: "flex", alignItems: "center", gap: "6px", width: "100%", textAlign: "left" }}>
                  <Trash2 size={12} /> Reset recommendations & listening history
                </button>
              )}
              <div style={{ height: "12px" }} />
              {showWipeConfirm ? (
                <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                  <button onClick={async () => {
                    setWiping(true);
                    try {
                      await api.wipeDatabase();
                      addToast?.("Database wiped. Reloading...", "success");
                      setTimeout(() => window.location.reload(), 1500);
                    } catch (e) { addToast?.("Wipe failed: " + (e.message || "unknown error"), "error"); }
                    setWiping(false);
                  }} disabled={wiping}
                    style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "#3a0000", border: "1px solid #6a1010", color: "var(--danger)", cursor: "pointer", fontWeight: 600 }}>
                    {wiping ? "Wiping..." : "Yes, wipe everything"}
                  </button>
                  <button onClick={() => setShowWipeConfirm(false)}
                    style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer" }}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button onClick={() => setShowWipeConfirm(true)}
                  style={{ fontSize: "11px", padding: "5px 12px", borderRadius: "5px", background: "#3a0000", border: "1px solid #6a1010", color: "var(--danger)", cursor: "pointer", display: "flex", alignItems: "center", gap: "6px", width: "100%", textAlign: "left", fontWeight: 600 }}>
                  <Trash2 size={12} /> Wipe all songs, embeddings & cache
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", padding: "12px 16px", background: "var(--bg-sunken)", borderTop: "1px solid var(--border)" }}>
        <button onClick={() => {}}
          style={{ fontSize: "12px", padding: "7px 16px", borderRadius: "6px", background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-secondary)", cursor: "pointer" }}>
          Cancel
        </button>
        <button onClick={() => { addToast?.("Settings saved", "success"); }}
          style={{ fontSize: "12px", padding: "7px 16px", borderRadius: "6px", background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: "pointer", fontWeight: 500 }}>
          Save changes
        </button>
      </div>
    </div>
  );
}

export default SettingsContent;