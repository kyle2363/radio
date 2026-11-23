import React, { useCallback, useEffect, useState } from "react";
import { Moon, Bolt, Target, CloudRain, Compass, Wind } from "lucide-react";
import * as api from "../api";
import { MOODS } from "../ui/consistency.js";

const ICONS = { neutral: Wind, chill: Moon, energetic: Bolt, focus: Target, melancholy: CloudRain, discovery: Compass };

function activeStyle(color) {
  return {
    color,
    background: `color-mix(in srgb, ${color} 14%, transparent)`,
    boxShadow: `0 0 14px color-mix(in srgb, ${color} 18%, transparent)`,
  };
}

// Station picker under the command box: the six moods, or (Checkpoint 9D) your
// feels. A feel station narrows desktop Radio to that feel's songs and
// neighbours; normal Radio rules still apply.
const MoodButtons = React.memo(function MoodButtons({ activeMood, onMoodSet, addToast }) {
  const [feels, setFeels] = useState([]);
  const [activeFeel, setActiveFeel] = useState(null);
  const [view, setView] = useState("moods");

  const loadFeels = useCallback(() => (
    Promise.all([api.fetchFeels(), api.fetchFeelStation()])
      .then(([f, s]) => {
        setFeels((f.feels || []).filter((x) => x.members > 0));
        setActiveFeel(s.feel_id ?? null);
        return s.feel_id ?? null;
      })
      .catch(() => null)
  ), []);

  useEffect(() => {
    loadFeels().then((id) => { if (id) setView("feels"); });
  }, [loadFeels]);

  async function pickFeel(id) {
    const next = id === activeFeel ? null : id;
    try { await api.setFeelStation(next); setActiveFeel(next); }
    catch (e) { addToast?.("Could not change station: " + e.message, "error"); }
  }

  const hasFeels = feels.length > 0;
  const showFeels = hasFeels && view === "feels";
  const activeFeelName = feels.find((f) => f.id === activeFeel)?.name;
  const moodLabel = MOODS.find((m) => m.id === activeMood)?.label;
  const hint = activeFeelName
    ? `Feel station: ${activeFeelName}`
    : showFeels ? "Pick a feel to narrow Radio to it" : moodLabel && activeMood !== "neutral" ? `Mood: ${moodLabel}` : "Mood";

  return (
    <div className="station-picker">
      {hasFeels && (
        <div className="station-picker-head">
          <span className="station-picker-hint">{hint}</span>
          <div className="seg" role="tablist" aria-label="Station type">
            <button type="button" role="tab" aria-selected={!showFeels} className={`seg-opt ${!showFeels ? "on" : ""}`}
              onClick={() => setView("moods")}>Moods</button>
            <button type="button" role="tab" aria-selected={showFeels} className={`seg-opt ${showFeels ? "on" : ""}`}
              onClick={() => { setView("feels"); loadFeels(); }}>
              Feels{activeFeel ? " •" : ""}
            </button>
          </div>
        </div>
      )}

      {!showFeels && (
        <div className="mood-seg">
          {MOODS.map((mood) => {
            const isActive = activeMood === mood.id;
            const Icon = ICONS[mood.id];
            return (
              <button key={mood.id} type="button"
                onClick={() => onMoodSet(isActive || mood.id === "neutral" ? "neutral" : mood.id)}
                className={`mood-opt ${isActive ? "active" : ""}`}
                title={mood.label} aria-pressed={isActive}
                style={isActive ? activeStyle(mood.color) : undefined}>
                <Icon size={16} />
                <span>{mood.label}</span>
              </button>
            );
          })}
        </div>
      )}

      {showFeels && (
        <div className="mood-seg scrolling" aria-label="Your feels">
          {feels.map((f) => {
            const on = f.id === activeFeel;
            return (
              <button key={f.id} type="button" onClick={() => pickFeel(f.id)} aria-pressed={on}
                className={`mood-opt ${on ? "active" : ""}`}
                title={on ? "Back to normal Radio" : `Play a ${f.name} station`}
                style={on ? activeStyle(f.color) : undefined}>
                <span className="feel-swatch" style={{ background: f.color, opacity: on ? 1 : 0.55 }} />
                <span style={{ maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
});

export default MoodButtons;
