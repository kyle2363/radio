import React, { useState, useEffect } from "react";
import * as api from "../api";

const ExplorationSlider = React.memo(function ExplorationSlider({ level, onChange }) {
  const [value, setValue] = useState(level ?? 0.3);
  const [saved, setSaved] = useState(false);

  useEffect(() => { setValue(level ?? 0.3); }, [level]);

  const handleChange = async (e) => {
    const newVal = parseFloat(e.target.value);
    setValue(newVal);
    setSaved(false);
    try {
      await api.updateSetting("exploration_level", String(newVal));
      setSaved(true);
      if (onChange) onChange(newVal);
    } catch (err) {}
  };

  return (
    <div className="flex items-center gap-2 flex-1">
      <span className="text-[10px] whitespace-nowrap" style={{ color: "var(--text-muted)" }}>Safe</span>
      <div className="flex-1 relative h-[3px] rounded-full cursor-pointer" style={{ background: "var(--border)" }}>
        <div className="absolute h-full rounded-full" style={{ width: `${value * 100}%`, background: "var(--accent)" }} />
        <input type="range" min="0" max="1" step="0.05" value={value} onChange={handleChange}
          className="absolute inset-0 w-full opacity-0 cursor-pointer" />
      </div>
      <span className="text-[10px] tabular-nums min-w-[24px] text-right" style={{ color: "var(--text-muted)" }}>{Math.round(value * 100)}%</span>
      <span className="text-[10px] whitespace-nowrap pr-2" style={{ color: "var(--text-muted)" }}>Adv.</span>
    </div>
  );
});

export default ExplorationSlider;
