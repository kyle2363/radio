import React, { useState, useEffect, useRef, useCallback } from "react";
import "./BootScreen.css";

const BOOT_MSGS = [
  { text: "initializing audio context", progress: 10 },
  { text: "loading mood engine", progress: 24 },
  { text: "calibrating bpm analyzer", progress: 40 },
  { text: "syncing library index", progress: 55 },
  { text: "connecting to radio core", progress: 68 },
  { text: "activating mood_listener", progress: 82 },
  { text: "loading dj module", progress: 92 },
  { text: "all systems ready", progress: 100 },
];

const ARCHETYPES = [
  "mood_listener",
  "explorer",
  "comfort_listener",
  "deep_listener",
];

function getArchetype() {
  try {
    return localStorage.getItem("archetype") || ARCHETYPES[0];
  } catch {
    return ARCHETYPES[0];
  }
}

function getBuildId() {
  try {
    return localStorage.getItem("build_id") || "";
  } catch {
    return "";
  }
}

function getSemVer() {
  try {
    return localStorage.getItem("app_version") || "v2.4.1";
  } catch {
    return "v2.4.1";
  }
}

function getBuildNum() {
  try {
    return localStorage.getItem("build_num") || "#20260602";
  } catch {
    return "#20260602";
  }
}

/* ── Equalizer ─────────────────────────────────────────── */
function Equalizer() {
  const prefersReducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const config = [
    [14,0.55,0.38],[22,0.12,0.45],[30,0.08,0.52],[26,0.20,0.31],[18,0.40,0.42],
    [38,0.06,0.37],[24,0.15,0.49],[34,0.09,0.44],[20,0.30,0.56],[40,0.07,0.36],
    [28,0.18,0.48],[16,0.45,0.41],[36,0.08,0.38],[22,0.25,0.53],[32,0.10,0.46],
    [18,0.38,0.35],[28,0.12,0.50],[24,0.20,0.43],[20,0.32,0.39],[14,0.48,0.47],
    [36,0.06,0.55],[30,0.14,0.34],[26,0.22,0.51],[40,0.07,0.40],
  ];

  return (
    <div className="eq" id="eq">
      {config.map(([h, lo, d], i) => (
        <div
          key={i}
          className="eq-bar"
          style={{
            height: h + "px",
            "--lo": lo,
            "--d": prefersReducedMotion ? "0s" : d + "s",
            "--delay": prefersReducedMotion ? "0s" : (Math.random() * 0.25).toFixed(3) + "s",
            animation: prefersReducedMotion ? "none" : undefined,
          }}
        />
      ))}
    </div>
  );
}

/* ── Boot Terminal ─────────────────────────────────────── */
function BootTerminal() {
  const [lines, setLines] = useState([]);
  const [activeIdx, setActiveIdx] = useState(-1);
  const timerRef = useRef(null);
  const doneRef = useRef(false);

  useEffect(() => {
    doneRef.current = false;
    let idx = 0;

    const step = () => {
      if (doneRef.current) return;
      if (idx >= BOOT_MSGS.length) return;

      const msg = BOOT_MSGS[idx];
      const isLast = idx === BOOT_MSGS.length - 1;

      setLines((prev) => {
        const newLines = [...prev];
        if (idx > 0) {
          const prevLine = newLines[newLines.length - 1];
          if (prevLine) prevLine.loading = false;
        }
        newLines.push({ text: msg.text, progress: msg.progress, loading: !isLast, ok: !isLast });
        return newLines;
      });

      setActiveIdx(idx);
      idx++;

      if (idx < BOOT_MSGS.length) {
        timerRef.current = setTimeout(step, isLast ? 540 : 602);
      }
    };

    const startTimer = setTimeout(step, 800);
    return () => {
      clearTimeout(startTimer);
      clearTimeout(timerRef.current);
      doneRef.current = true;
    };
  }, []);

  return (
    <div className="terminal">
      {lines.map((line, i) => (
        <div key={i} className={`t-line ${line.loading ? "loading" : ""}`}>
          <span className="t-prefix">&gt;</span>
          <span className="t-msg">{line.text}</span>
          {line.ok && <span className="t-ok" />}
        </div>
      ))}
    </div>
  );
}

/* ── Progress Bar ──────────────────────────────────────── */
function ProgressBar() {
  const [displayProgress, setDisplayProgress] = useState(0);
  const [label, setLabel] = useState("0%");
  const timerRef = useRef(null);

  useEffect(() => {
    let idx = 0;
    const tick = () => {
      if (idx >= BOOT_MSGS.length) return;
      const msg = BOOT_MSGS[idx];
      setDisplayProgress(msg.progress);
      if (msg.progress === 100) {
        setLabel("ready");
      } else {
        setLabel(msg.progress + "%");
      }
      idx++;
      if (idx < BOOT_MSGS.length) {
        timerRef.current = setTimeout(tick, idx === BOOT_MSGS.length - 1 ? 540 : 602);
      }
    };
    const startTimer = setTimeout(tick, 800);
    return () => clearTimeout(startTimer);
  }, []);

  return (
    <div className="prog-wrap">
      <div className="prog-track">
        <div className="prog-fill" style={{ width: displayProgress + "%" }} />
      </div>
      <div className="prog-meta">{label}</div>
    </div>
  );
}

/* ── Error State ───────────────────────────────────────── */
function ErrorState({ message, onRetry }) {
  return (
    <div className="boot-center">
      <div className="boot-logo error">
        <div className="dot error" />
        <div className="logo-word">Radio</div>
      </div>
      <div className="boot-error">
        <div className="error-icon">!</div>
        <p className="error-text">{message}</p>
        <button className="retry-btn" onClick={onRetry}>Retry</button>
      </div>
    </div>
  );
}

/* ── Main BootScreen ───────────────────────────────────── */
export default function BootScreen({ onReady, onError, initialProgress = 0 }) {
  const [archetype] = useState(getArchetype);
  const [semVer] = useState(getSemVer);
  const [buildId] = useState(getBuildNum);
  const [statusText, setStatusText] = useState(archetype);
  const doneRef = useRef(false);

  useEffect(() => {
    doneRef.current = false;
    const t1 = setTimeout(() => setStatusText("online"), 3500);
    const t2 = setTimeout(() => {
      if (!doneRef.current) {
        doneRef.current = true;
        onReady?.();
      }
    }, 4200);

    const handleKey = (e) => {
      if (e.key === "Escape" && !doneRef.current) {
        doneRef.current = true;
        clearTimeout(t1);
        clearTimeout(t2);
        onReady?.();
      }
    };
    window.addEventListener("keydown", handleKey);

    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      window.removeEventListener("keydown", handleKey);
      doneRef.current = true;
    };
  }, [onReady]);

  const handleSkip = () => {
    if (!doneRef.current) {
      doneRef.current = true;
      onReady?.();
    }
  };

  if (onError) {
    return (
      <ErrorState message={onError} onRetry={() => onError(null)} />
    );
  }

  return (
    <div className="boot-screen" onClick={handleSkip}
      style={{ cursor: "pointer" }}
      title="Click or press Esc to skip">
      <div className="glow-top" />
      <div className="glow-bottom" />
      <div className="scanlines" />
      <div className="grid" />

      <div className="boot-center">
        <div className="boot-logo">
          <div className="dot" />
          <div className="logo-word">Radio</div>
        </div>

        <div className="status-tag">
          <span className="s-dot" />
          <span>{statusText}</span>
        </div>

        <Equalizer />

        <BootTerminal />

        <ProgressBar />
      </div>

      <div className="version">{semVer}</div>
      <div className="build-id">{buildId}</div>
    </div>
  );
}
