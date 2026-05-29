import React, { useState, useEffect } from "react";
import * as api from "../api";
import useTransition from "../hooks/useTransition";
import IngestPanel from "./IngestPanel";
import TalkSettings from "./TalkSettings";

function SettingsModal({ isOpen, onClose, addToast, sectionSpacing, onSectionSpacingChange }) {
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [settings, setSettings] = useState({});
  const [announcementInterval, setAnnouncementInterval] = useState(30);
  const [explorationLevel, setExplorationLevel] = useState(0.3);
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [musicDir, setMusicDir] = useState("");
  const [saving, setSaving] = useState(false);
  const trans = useTransition(isOpen, 200);
  const [llmStatus, setLlmStatus] = useState(null);
  const [localSpacing, setLocalSpacing] = useState(sectionSpacing || "10");

  useEffect(() => { setLocalSpacing(sectionSpacing || "10"); }, [sectionSpacing]);

  useEffect(() => {
    if (!isOpen) return;
    fetchSettings();
    fetchLlmStatus();
  }, [isOpen]);

  const fetchSettings = async () => {
    try {
      const data = await api.fetchSettings();
      setSettings(data);
      setAnnouncementInterval(data.announcement_interval ?? 30);
      setExplorationLevel(parseFloat(data.exploration_level) ?? 0.3);
      setTtsEnabled(data.tts_enabled !== "false");
      setMusicDir(data.music_dir || "");
    } catch (err) { console.error("SettingsModal fetchSettings error:", err); }
  };

  const fetchLlmStatus = async () => {
    try {
      const data = await api.fetchLlmStatus();
      setLlmStatus(data);
    } catch (err) {
      setLlmStatus({ chat: { healthy: false }, embed: { healthy: false } });
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateSetting("announcement_interval", String(announcementInterval));
      await api.updateSetting("exploration_level", String(explorationLevel));
      await api.updateSetting("tts_enabled", String(ttsEnabled));
      await api.updateSetting("music_dir", musicDir);
      await api.updateSetting("section_spacing", localSpacing);
      addToast?.("Settings saved", "success");
    } catch (err) {
      console.error("SettingsModal handleSave error:", err);
      addToast?.("Failed to save settings", "error");
    }
    setSaving(false);
    onClose();
  };

  if (!trans.shouldRender) return null;

  const isExiting = trans.animClass === "exit" || trans.animClass === "exit-active";

  return (
    <div className={`fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm backdrop-fade-in ${isExiting ? "scale-out" : ""}`} onClick={onClose}>
      <div className={`bg-gray-900 rounded-xl w-full max-w-lg mx-4 shadow-xl max-h-[90vh] overflow-y-auto scale-in ${isExiting ? "scale-out" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-8 py-4 sticky top-0 bg-gray-900">
          <h2 className="text-lg font-semibold text-white">Settings</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition text-xl leading-none">&times;</button>
        </div>
        <div className="px-8 py-6 space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Music Directory</label>
            <input type="text" value={musicDir} onChange={(e) => setMusicDir(e.target.value)} placeholder="Leave empty for default (audio_files/)" className="w-full px-4 py-2.5 rounded-xl bg-gray-800 text-gray-200 text-sm focus:outline-none" />
            <p className="text-xs text-gray-600 mt-1.5 ml-0.5">Path to your MP3 library. Restart required after change.</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Min. seconds between announcements</label>
            <input type="number" min="0" max="300" value={announcementInterval} onChange={(e) => setAnnouncementInterval(parseInt(e.target.value) || 0)} className="w-full px-4 py-2.5 rounded-xl bg-gray-800 text-gray-200 text-sm focus:outline-none" />
            <p className="text-xs text-gray-600 mt-1.5 ml-0.5">0 = always announce, 30 = every 30s, 120 = every 2 minutes</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Exploration level</label>
            <input type="range" min="0" max="1" step="0.05" value={explorationLevel} onChange={(e) => setExplorationLevel(parseFloat(e.target.value))} className="w-full h-2 bg-gray-800 rounded-full appearance-none cursor-pointer accent-green-500" />
            <div className="flex justify-between text-xs text-gray-600 mt-1.5">
              <span>Safe</span><span>{Math.round(explorationLevel * 100)}%</span><span>Adventure</span>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <div>
              <label className="block text-sm font-medium text-gray-300">TTS announcements</label>
              <p className="text-xs text-gray-600 mt-1.5">DJ voice announcements between songs</p>
            </div>
            <button onClick={() => setTtsEnabled(!ttsEnabled)} className={`relative w-12 h-6 rounded-full transition-colors ${ttsEnabled ? "bg-green-600" : "bg-gray-700"}`}>
              <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${ttsEnabled ? "translate-x-6" : "translate-x-0.5"}`} />
            </button>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Section spacing</label>
            <div className="flex gap-2">
              {[
                { value: "6", label: "Compact" },
                { value: "8", label: "Normal" },
                { value: "10", label: "Relaxed" },
                { value: "12", label: "Spacious" },
              ].map((opt) => (
                <button key={opt.value} onClick={() => { setLocalSpacing(opt.value); onSectionSpacingChange?.(opt.value); }}
                  className="flex-1 px-3.5 py-2.5 rounded-xl text-xs font-medium transition"
                  style={{
                    background: localSpacing === opt.value ? "var(--accent-muted)" : "var(--bg-elevated)",
                    color: localSpacing === opt.value ? "var(--accent)" : "var(--text-muted)",
                  }}>
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Mode</label>
            <div className="px-4 py-2.5 rounded-xl bg-gray-800 text-sm text-gray-400">{settings.current_mode || "radio"}</div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Archetype</label>
            <div className="px-4 py-2.5 rounded-xl bg-gray-800 text-sm text-gray-400">{settings.archetype || "mood_listener"}</div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2.5">LLM Providers</label>
            <div className="space-y-2.5">
              {llmStatus?.chat && (
                <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-gray-800">
                  <span className={`w-2 h-2 rounded-full ${llmStatus.chat.healthy ? "bg-green-400" : "bg-red-400"}`} />
                  <span className="text-xs text-gray-300">Chat: {llmStatus.chat.provider || "?"}</span>
                  <span className="text-xs text-gray-500 ml-auto">{llmStatus.chat.model || "?"}</span>
                </div>
              )}
              {llmStatus?.embed && (
                <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-gray-800">
                  <span className={`w-2 h-2 rounded-full ${llmStatus.embed.healthy ? "bg-green-400" : "bg-red-400"}`} />
                  <span className="text-xs text-gray-300">Embed: {llmStatus.embed.provider || "?"}</span>
                  <span className="text-xs text-gray-500 ml-auto">{llmStatus.embed.model || "?"}</span>
                </div>
              )}
              {!llmStatus && <div className="px-4 py-3 rounded-xl bg-gray-800"><span className="text-xs text-gray-500">Loading...</span></div>}
            </div>
            <button onClick={fetchLlmStatus} className="mt-2.5 text-xs text-gray-500 hover:text-gray-300 transition px-1 py-0.5">Refresh status</button>
          </div>

          {/* Radio Talk Show */}
          <TalkSettings addToast={addToast} />

          {/* Reset Recommendations */}
          <div className="pt-4" style={{ borderTop: "1px solid var(--border)" }}>
            <label className="block text-sm font-medium text-red-400 mb-2">Reset Recommendations</label>
            <p className="text-xs text-gray-600 mb-3">Clears all listening history, transitions, and stats. You'll re-seed your taste on next launch.</p>
            {showResetConfirm ? (
              <div className="flex gap-2">
                <button onClick={async () => {
                  setResetting(true);
                  try {
                    await api.resetRecommendations();
                    addToast?.("Recommendations reset. Restart to re-seed your taste.", "success");
                    setShowResetConfirm(false);
                  } catch (e) {
                    addToast?.("Reset failed: " + (e.message || "unknown error"), "error");
                  }
                  setResetting(false);
                }} disabled={resetting} className="px-4 py-2 rounded-xl text-xs font-medium bg-red-700 text-white hover:bg-red-600 transition disabled:opacity-40">
                  {resetting ? "Resetting..." : "Yes, reset everything"}
                </button>
                <button onClick={() => setShowResetConfirm(false)} className="px-4 py-2 rounded-xl text-xs font-medium" style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)" }}>
                  Cancel
                </button>
              </div>
            ) : (
              <button onClick={() => setShowResetConfirm(true)} className="px-4 py-2 rounded-xl text-xs font-medium transition" style={{ background: "rgba(240,80,80,0.1)", color: "#f05050", border: "1px solid rgba(240,80,80,0.2)" }}>
                Reset recommendations
              </button>
            )}
          </div>

          {/* Library Status */}
          <div className="mt-2 pt-4" style={{ borderTop: "1px solid var(--border)" }}>
            <IngestPanel addToast={addToast} />
          </div>
        </div>
        <div className="flex justify-end gap-3 px-8 py-4 sticky bottom-0" style={{ borderTop: "1px solid var(--border)", background: "var(--bg-surface)" }}>
          <button onClick={onClose} className="px-5 py-2.5 rounded-xl text-sm transition" style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)" }}>Cancel</button>
          <button onClick={handleSave} disabled={saving} className="px-5 py-2.5 rounded-xl text-sm font-medium transition" style={{ background: "var(--accent)", color: "var(--text-inverse)" }}>{saving ? "Saving..." : "Save"}</button>
        </div>
      </div>
    </div>
  );
}

export default SettingsModal;
