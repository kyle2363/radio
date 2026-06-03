import React, { useEffect, useMemo, useState } from "react";
import {
  Brain,
  ChevronDown,
  Database,
  ListMusic,
  Mic2,
  Network,
  Radio,
  Sparkles,
  Terminal,
  Volume2,
  Waves,
} from "lucide-react";
import * as api from "../api";
import { SLOT_GUIDE, WIKI_SECTIONS } from "../content/wiki";

const ICONS = {
  Brain,
  Database,
  ListMusic,
  Mic2,
  Network,
  Radio,
  Sparkles,
  Terminal,
  Volume2,
  Waves,
};

const PHASES = ["raw", "analyzed", "described", "embedded", "clustered"];
const PHASE_LABELS = {
  raw: "Raw",
  analyzed: "Analyzed",
  described: "Described",
  embedded: "Embedded",
  clustered: "Clustered",
};

function StatusDot({ state = "muted" }) {
  const cls = state === "ok" ? "dot-ok" : state === "warn" ? "dot-warn" : state === "err" ? "dot-err" : "";
  return <span className={`dot ${cls}`} style={!cls ? { background: "var(--text-muted)" } : undefined} />;
}

function LiveCard({ title, value, detail, state = "muted" }) {
  return (
    <div className="stat-card" style={{ minHeight: 78 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 8 }}>
        <StatusDot state={state} />
        <div className="stat-desc" style={{ marginTop: 0 }}>{title}</div>
      </div>
      <div className="stat-num" style={{ fontSize: 18, lineHeight: 1.15 }}>{value}</div>
      {detail && (
        <div style={{ color: "var(--text-muted)", fontSize: 10, marginTop: 4, lineHeight: 1.3 }}>
          {detail}
        </div>
      )}
    </div>
  );
}

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "Unavailable";
  return `${Math.round(Number(value) * 100)}%`;
}

function getPhaseTotal(status) {
  if (!status) return 0;
  return Object.values(status).reduce((sum, value) => sum + (Number(value) || 0), 0);
}

function LiveStatusPanel({ live }) {
  const ingestTotal = getPhaseTotal(live.ingestStatus);
  const clustered = Number(live.ingestStatus?.clustered || 0);
  const ingestRunning = live.ingestProgress?.running;
  const ingestValue = ingestRunning
    ? `${live.ingestProgress?.phase || "Running"}`
    : ingestTotal > 0
      ? `${clustered}/${ingestTotal} clustered`
      : "Unavailable";

  const chatOk = live.llmStatus
    ? (live.llmStatus.chat?.openai?.healthy || live.llmStatus.chat?.ollama?.healthy)
    : false;
  const embedOk = live.llmStatus
    ? (live.llmStatus.embed?.openai?.healthy || live.llmStatus.embed?.ollama?.healthy)
    : false;
  const llmValue = live.llmStatus
    ? `${chatOk ? "Chat ok" : "Chat off"} / ${embedOk ? "Embed ok" : "Embed off"}`
    : "Unavailable";

  const ttsValue = live.ttsStatus
    ? live.ttsStatus.next_song_ready
      ? "Next voice ready"
      : live.ttsStatus.enabled === false
        ? "Disabled"
        : "Waiting"
    : "Unavailable";

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span className="badge b-teal">Live</span>
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
          These cards use existing backend status endpoints.
        </span>
      </div>
      <div className="stat-cards" style={{ padding: 0, gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
        <LiveCard
          title="Library"
          value={live.stats ? `${live.stats.total_songs_in_library || 0} songs` : "Unavailable"}
          detail={live.stats ? `Avg completion ${formatPct(live.stats.avg_completion_rate)}` : "Static wiki still works offline"}
          state={live.stats ? "ok" : "muted"}
        />
        <LiveCard
          title="Ingest"
          value={ingestValue}
          detail={ingestRunning ? `${live.ingestProgress?.processed || 0}/${live.ingestProgress?.total || 0} processed` : "Pipeline phase snapshot"}
          state={ingestRunning ? "warn" : ingestTotal > 0 ? "ok" : "muted"}
        />
        <LiveCard
          title="LLM"
          value={llmValue}
          detail={live.llmStatus?.active_chat ? `Active: ${live.llmStatus.active_chat}` : "Provider health"}
          state={live.llmStatus ? (chatOk && embedOk ? "ok" : "err") : "muted"}
        />
        <LiveCard
          title="TTS"
          value={ttsValue}
          detail={live.ttsStatus?.voice_engine ? `${live.ttsStatus.voice_engine} / ${live.ttsStatus.active_voice || "voice"}` : "Voice readiness"}
          state={live.ttsStatus ? (live.ttsStatus.next_song_ready ? "ok" : "warn") : "muted"}
        />
        <LiveCard
          title="Energy"
          value={live.trajectory?.direction || "Unavailable"}
          detail={live.trajectory?.target_energy != null ? `Target ${formatPct(live.trajectory.target_energy)}` : "Current session trajectory"}
          state={live.trajectory ? "ok" : "muted"}
        />
      </div>
    </div>
  );
}

function IngestMiniStatus({ status, progress }) {
  const total = getPhaseTotal(status);
  if (!status && !progress) {
    return <div className="wiki-note">Ingest status unavailable. The explanation below is still available.</div>;
  }

  return (
    <div className="prog-sec" style={{ padding: "10px 0 4px" }}>
      {progress?.running && (
        <div style={{ marginBottom: 10, padding: "7px 10px", borderRadius: 6, background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", fontSize: 11 }}>
          Running {progress.phase}: {progress.current_song || "working"} ({progress.processed || 0}/{progress.total || 0})
        </div>
      )}
      {PHASES.map((phase) => {
        const count = Number(status?.[phase] || 0);
        const pct = total > 0 ? (count / total) * 100 : 0;
        return (
          <div key={phase} className="prog-item">
            <div className="prog-label">
              <span style={{ fontSize: 10, color: phase === "clustered" && count > 0 ? "var(--accent)" : "var(--text-muted)" }}>
                {PHASE_LABELS[phase]}
              </span>
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{count}</span>
            </div>
            <div className="prog-track">
              <div className="prog-fill-bar" style={{ width: `${Math.min(100, pct)}%`, background: phase === "clustered" ? "var(--accent)" : "#5eadf5" }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SlotGuide() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8, marginTop: 10 }}>
      {SLOT_GUIDE.map((slot) => (
        <div key={slot.id} className={`queue-card ${slot.id}`} style={{ marginBottom: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
            <span className={`slot-badge ${slot.id}`}>{slot.label}</span>
          </div>
          <div style={{ color: "var(--text-secondary)", fontSize: 11, lineHeight: 1.45 }}>
            {slot.description}
          </div>
        </div>
      ))}
    </div>
  );
}

function SectionCard({ section, open, onToggle, live }) {
  const Icon = ICONS[section.icon] || Radio;

  return (
    <section id={`wiki-${section.id}`} className="wiki-section">
      <div className="set-sec" style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", background: "var(--bg-surface)", marginBottom: 10 }}>
        <div className="set-hdr" onClick={onToggle}>
          <div className="icon-chip" style={{ background: "var(--accent-muted)" }}>
            <Icon size={13} style={{ color: "var(--accent)" }} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: "var(--text-primary)", fontSize: 13, fontWeight: 600 }}>{section.title}</div>
            <div style={{ color: "var(--text-muted)", fontSize: 10, marginTop: 2, lineHeight: 1.35 }}>{section.summary}</div>
          </div>
          <div style={{ flex: 1 }} />
          <ChevronDown size={14} style={{ color: "var(--text-muted)", transform: open ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s", flexShrink: 0 }} />
        </div>
        {open && (
          <div className="set-body wiki-body">
            {section.id === "ingest" && (
              <IngestMiniStatus status={live.ingestStatus} progress={live.ingestProgress} />
            )}
            {section.id === "slots" && <SlotGuide />}
            <div className="wiki-bullets">
              {section.bullets.map((item) => (
                <div key={item} className="wiki-bullet">
                  <span className="wiki-bullet-dot" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
            <details className="wiki-details">
              <summary>Technical details</summary>
              <div style={{ marginTop: 8 }}>
                {section.details.map((detail) => (
                  <p key={detail}>{detail}</p>
                ))}
              </div>
            </details>
          </div>
        )}
      </div>
    </section>
  );
}

function WikiPage() {
  const [activeId, setActiveId] = useState(WIKI_SECTIONS[0].id);
  const [openSections, setOpenSections] = useState(() => new Set(WIKI_SECTIONS.map((s) => s.id)));
  const [live, setLive] = useState({
    ingestStatus: null,
    ingestProgress: null,
    llmStatus: null,
    ttsStatus: null,
    stats: null,
    trajectory: null,
  });

  const loadLive = async () => {
    const [ingestStatus, ingestProgress, llmStatus, ttsStatus, stats, trajectory] = await Promise.allSettled([
      api.fetchIngestStatus(),
      api.fetchIngestProgress(),
      api.fetchLlmStatus(),
      api.fetchTtsStatus(),
      api.fetchStatsOverview(),
      api.fetchEnergyTrajectory(),
    ]);

    setLive({
      ingestStatus: ingestStatus.status === "fulfilled" ? ingestStatus.value : null,
      ingestProgress: ingestProgress.status === "fulfilled" ? ingestProgress.value : null,
      llmStatus: llmStatus.status === "fulfilled" ? llmStatus.value : null,
      ttsStatus: ttsStatus.status === "fulfilled" ? ttsStatus.value : null,
      stats: stats.status === "fulfilled" ? stats.value : null,
      trajectory: trajectory.status === "fulfilled" ? trajectory.value : null,
    });
  };

  useEffect(() => {
    loadLive();
    const id = setInterval(loadLive, 15000);
    return () => clearInterval(id);
  }, []);

  const openCount = useMemo(() => openSections.size, [openSections]);

  const toggleSection = (id) => {
    setOpenSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const jumpTo = (id) => {
    setActiveId(id);
    setOpenSections((prev) => new Set(prev).add(id));
    const el = document.getElementById(`wiki-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="wiki-page">
      <div className="wiki-hero">
        <div>
          <div className="stat-desc" style={{ marginBottom: 5 }}>How Radio Works</div>
          <h3>Feature wiki</h3>
          <p>
            A compact map of the ingest pipeline, recommendation system, queue logic, mood engine, TTS, talk show, and data model.
          </p>
        </div>
        <button
          onClick={() => setOpenSections((prev) => prev.size === WIKI_SECTIONS.length ? new Set(["overview"]) : new Set(WIKI_SECTIONS.map((s) => s.id)))}
          className="btn-muted"
          style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "7px 12px", fontSize: 11, alignSelf: "flex-start" }}
        >
          {openCount === WIKI_SECTIONS.length ? "Collapse" : "Expand"}
        </button>
      </div>

      <LiveStatusPanel live={live} />

      <div className="wiki-layout">
        <aside className="wiki-nav">
          {WIKI_SECTIONS.map((section) => {
            const Icon = ICONS[section.icon] || Radio;
            return (
              <button
                key={section.id}
                onClick={() => jumpTo(section.id)}
                className={`wiki-nav-item ${activeId === section.id ? "on" : ""}`}
              >
                <Icon size={12} />
                <span>{section.title}</span>
              </button>
            );
          })}
        </aside>
        <div className="wiki-main">
          {WIKI_SECTIONS.map((section) => (
            <SectionCard
              key={section.id}
              section={section}
              open={openSections.has(section.id)}
              onToggle={() => toggleSection(section.id)}
              live={live}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export default WikiPage;
