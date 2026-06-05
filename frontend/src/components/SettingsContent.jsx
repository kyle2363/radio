import React, { useState, useEffect, useRef, useCallback } from "react";
import { Volume2, Brain, Cpu, Folder, ChevronDown, RefreshCw, Trash2, Moon, Filter, X } from "lucide-react";
import * as api from "../api";
import TalkSettings from "./TalkSettings";
import IngestPanel from "./IngestPanel";

function SettingsContent({ addToast, sectionSpacing, onSectionSpacingChange, rightRailWidth, setRightRailWidth }) {
  const [settings, setSettings] = useState({});
  const [sections, setSections] = useState({ playback: true, ai: false, llm: false, library: false });
  const [announcementInterval, setAnnouncementInterval] = useState(30);
  const [explorationLevel, setExplorationLevel] = useState(0.3);
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
  const [jitterStrength, setJitterStrength] = useState(0.12);
  const [recencyExcludeHours, setRecencyExcludeHours] = useState("1");
  const [vibeLockDuration, setVibeLockDuration] = useState(30);
  const [vibeLockMaxSongs, setVibeLockMaxSongs] = useState(10);
  const [dislikeDecayRate, setDislikeDecayRate] = useState(0.029);
  const [subfolders, setSubfolders] = useState([]);
  const [selectedFolders, setSelectedFolders] = useState([]);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const debounceRef = useRef(null);

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

  const fetchSubfolders = useCallback(async () => {
    try {
      const data = await api.fetchSubfolders();
      setSubfolders(data.subfolders || []);
      const filter = await api.fetchFolderFilter();
      setSelectedFolders(filter.folders || []);
    } catch (e) {}
  }, []);

  useEffect(() => {
    fetchSettings();
    fetchLlmStatus();
    fetchSubfolders();
    const sleepPoll = setInterval(async () => {
      try {
        const data = await api.fetchSleepTimer();
        setSleepTimerStatus(data.active ? data : null);
      } catch (e) {}
    }, 5000);
    return () => { clearInterval(sleepPoll); };
  }, [fetchSubfolders]);

  const fetchSettings = async () => {
    try {
      const data = await api.fetchSettings();
      setSettings(data);
      setAnnouncementInterval(data.announcement_interval ?? 30);
      setExplorationLevel(parseFloat(data.exploration_level) ?? 0.3);
      setTtsEnabled(data.tts_enabled !== "false");
      setMusicDir(data.music_dir || "");
      setTalkSegmentsEnabled(data.talk_segments_enabled === "true");
      setWaveformMode(data.waveform_mode || "orbit");
      setJitterStrength(parseFloat(data.jitter_strength) || 0.12);
      setRecencyExcludeHours(data.recency_exclude_hours || "1");
      setVibeLockDuration(parseInt(data.vibe_lock_duration_minutes) || 30);
      setVibeLockMaxSongs(parseInt(data.vibe_lock_max_songs) || 10);
      setDislikeDecayRate(parseFloat(data.dislike_decay_rate) || 0.029);
    } catch (err) {}
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
               <div className="set-label">Announcement interval</div>
               <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                 <input type="range" min="0" max="300" value={announcementInterval} step="15"
                   onChange={(e) => { const v = parseInt(e.target.value) || 0; setAnnouncementInterval(v); debouncedSave("announcement_interval", String(v)); }}
                   style={{ width: "90px", accentColor: "var(--accent)" }} />
                 <span className="set-val">{announcementInterval}s</span>
               </div>
             </div>
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
                <div className="set-label">Exploration level</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>How adventurous your queue is</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Safe</span>
                <input type="range" min="0" max="100" value={Math.round(explorationLevel * 100)} step="5"
                  onChange={(e) => { const v = parseInt(e.target.value) / 100; setExplorationLevel(v); debouncedSave("exploration_level", String(v)); }}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{Math.round(explorationLevel * 100)}%</span>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Adv.</span>
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-label">Variety</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>How much the queue diversifies each cycle</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Stable</span>
                <input type="range" min="0" max="100" value={Math.round(((jitterStrength - 0.03) / 0.13) * 100)} step="5"
                  onChange={(e) => { const pct = parseInt(e.target.value); const v = 0.03 + (pct / 100) * 0.13; setJitterStrength(v); debouncedSave("jitter_strength", String(v)); }}
                  style={{ width: "80px", accentColor: "var(--accent)" }} />
                <span className="set-val">{Math.round(((jitterStrength - 0.03) / 0.13) * 100)}%</span>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Mix</span>
              </div>
            </div>
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
            <div className="set-row">
              <div className="set-label">Detected archetype</div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <div style={{ width: "7px", height: "7px", borderRadius: "50%", background: "#5eadf5" }} />
                <span className="set-val">{settings.archetype ? settings.archetype.replace(/_/g, " ") : "Mood listener"}</span>
              </div>
            </div>
            <div className="set-row" style={{ borderBottom: "none" }}>
              <div>
                <div className="set-label">Radio Talk Show</div>
                <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "1px" }}>Caller segments, DJ monologues</div>
              </div>
              <div className={`tog ${talkSegmentsEnabled ? "" : "off"}`}
                onClick={() => { const next = !talkSegmentsEnabled; setTalkSegmentsEnabled(next); save("talk_segments_enabled", String(next), true); }}>
                <div className="tog-dot" />
              </div>
            </div>
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

            {/* Folder filter */}
            <div className="set-row" style={{ flexDirection: "column", alignItems: "stretch", borderBottom: "1px solid var(--border-subtle)" }}>
              <div className="set-hdr" onClick={() => setShowFolderFilter(!showFolderFilter)} style={{ padding: "8px 0", cursor: "pointer", display: "flex", alignItems: "center", gap: "8px" }}>
                <div className="icon-chip" style={{ background: "#0f1e10" }}>
                  <Filter size={13} style={{ color: "#639922" }} />
                </div>
                <span style={{ color: "var(--text-primary)", fontSize: "12px", fontWeight: 500 }}>Folder filter (session)</span>
                {selectedFolders.length > 0 && (
                  <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "4px", background: "rgba(72,187,120,0.15)", color: "#4ade80", marginLeft: "4px" }}>
                    {selectedFolders.length} selected
                  </span>
                )}
                <div style={{ flex: 1 }} />
                <ChevronDown size={13} style={{ color: "var(--text-muted)", transform: showFolderFilter ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
              </div>
              {showFolderFilter && (
                <div style={{ paddingBottom: "10px" }}>
                  <div style={{ fontSize: "10px", color: "var(--text-muted)", marginBottom: "8px" }}>
                    Restrict recommendations to specific folders. Clears on restart.
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", marginBottom: "8px", maxHeight: "200px", overflowY: "auto" }}>
                    {subfolders.length === 0 && (
                      <div style={{ fontSize: "11px", color: "var(--text-muted)", padding: "4px 0" }}>No subfolders found</div>
                    )}
                    {subfolders.map((sf) => {
                      const isSelected = selectedFolders.includes(sf.name);
                      return (
                        <div key={sf.name}
                          onClick={() => {
                            const next = isSelected
                              ? selectedFolders.filter((f) => f !== sf.name)
                              : [...selectedFolders, sf.name];
                            setSelectedFolders(next);
                            api.setFolderFilter(next).catch(() => {});
                          }}
                          style={{
                            display: "inline-flex", alignItems: "center", gap: "4px",
                            padding: "4px 10px", borderRadius: "5px", fontSize: "11px", cursor: "pointer",
                            background: isSelected ? "rgba(72,187,120,0.12)" : "var(--bg-elevated)",
                            border: `1px solid ${isSelected ? "rgba(72,187,120,0.3)" : "var(--border)"}`,
                            color: isSelected ? "#4ade80" : "var(--text-secondary)",
                            transition: "all 0.15s",
                          }}>
                          {sf.name}
                          <span style={{ fontSize: "9px", opacity: 0.6 }}>({sf.count})</span>
                        </div>
                      );
                    })}
                  </div>
                  {selectedFolders.length > 0 && (
                    <button onClick={async () => {
                      setSelectedFolders([]);
                      try { await api.clearFolderFilter(); } catch (e) {}
                    }}
                      style={{ fontSize: "10px", padding: "4px 10px", borderRadius: "4px", background: "#1e0808", border: "1px solid #3a1010", color: "var(--danger)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                      <X size={11} /> Clear filter
                    </button>
                  )}
                </div>
              )}
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

      {/* Talk Settings (expanded inline when enabled) */}
      {sections.ai && talkSegmentsEnabled && <TalkSettings addToast={addToast} masterEnabled={talkSegmentsEnabled} />}

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