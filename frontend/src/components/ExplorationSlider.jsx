import React, { useState, useEffect, useRef } from "react";
import { Compass } from "lucide-react";
import * as api from "../api";

const COMMIT_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);

function describe(value) {
  if (value < 0.2) return "Favourites";
  if (value < 0.45) return "Mostly familiar";
  if (value < 0.7) return "Mixed";
  return "Adventurous";
}

// How far Radio strays from your taste. Saves when you let go, not on every step.
const ExplorationSlider = React.memo(function ExplorationSlider({ level, onChange }) {
  const [value, setValue] = useState(level ?? 0.3);
  const savedRef = useRef(level ?? 0.3);

  useEffect(() => { setValue(level ?? 0.3); savedRef.current = level ?? 0.3; }, [level]);

  const commit = () => {
    if (value === savedRef.current) return;
    savedRef.current = value;
    if (onChange) onChange(value);
    else api.updateSetting("exploration_level", String(value)).catch(() => {});
  };

  return (
    <div className="dial">
      <div className="dial-head">
        <span className="dial-name"><Compass size={14} />Exploration</span>
        <span className="dial-value">{describe(value)} · {Math.round(value * 100)}%</span>
      </div>
      <input type="range" min="0" max="1" step="0.05" value={value} aria-label="Exploration"
        aria-valuetext={`${describe(value)}, ${Math.round(value * 100)} percent`}
        className="ui-slider" style={{ "--fill": `${value * 100}%` }}
        onChange={(e) => setValue(parseFloat(e.target.value))}
        onPointerUp={commit} onKeyUp={(e) => { if (COMMIT_KEYS.has(e.key)) commit(); }} />
      <div className="dial-ends"><span>Safe</span><span>Adventurous</span></div>
    </div>
  );
});

export default ExplorationSlider;
