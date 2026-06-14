import React, { useState, useEffect, useRef } from "react";
import { Volume2, VolumeX } from "lucide-react";

const VolumeControl = React.memo(function VolumeControl({ volume, onChange }) {
  const [value, setValue] = useState(volume ?? 70);
  const prevValueRef = useRef(volume ?? 70);

  useEffect(() => { setValue(volume ?? 70); }, [volume]);

  const handleChange = (e) => {
    const newVal = parseInt(e.target.value);
    setValue(newVal);
  };

  const handleCommit = () => {
    onChange?.(value);
  };

  const toggleMute = () => {
    if (value === 0) {
      const restore = prevValueRef.current || 50;
      setValue(restore);
      onChange?.(restore);
    } else {
      prevValueRef.current = value;
      setValue(0);
      onChange?.(0);
    }
  };

  return (
    <div className="flex items-center gap-2 flex-1" style={{ paddingLeft: "4px" }}>
      <button onClick={toggleMute} title={value === 0 ? "Unmute" : "Mute"}
        style={{ color: "var(--text-muted)", flexShrink: 0, background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex" }}>
        {value === 0 ? <VolumeX size={14} /> : <Volume2 size={14} />}
      </button>
      <div className="flex-1 relative" style={{ height: "16px" }}>
        {/* Track background with tick marks */}
        <div className="absolute" style={{ top: "50%", height: "3px", transform: "translateY(-50%)", left: 0, right: 0, borderRadius: 2, background: "var(--border)" }}>
          {/* Fill bar */}
          <div style={{
            height: "100%", borderRadius: 2, width: `${value}%`,
            background: "var(--accent)", transition: "width 80ms ease-out",
          }} />
          {/* Tick marks at 25/50/75 */}
          {[25, 50, 75].map((tick) => (
            <div key={tick} style={{
              position: "absolute", left: `${tick}%`, top: -2,
              width: 1, height: 7, borderRadius: 1,
              background: value >= tick ? "var(--accent)" : "var(--border-strong)",
              opacity: 0.4, transition: "background 200ms",
            }} />
          ))}
        </div>
        {/* Invisible range input */}
        <input type="range" min="0" max="100" value={value} onChange={handleChange}
          onMouseUp={handleCommit} onTouchEnd={handleCommit} onKeyUp={(e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "ArrowUp" || e.key === "ArrowDown") handleCommit(); }}
          className="absolute inset-0 w-full opacity-0 cursor-pointer" />
      </div>
      <span className="text-[11px] tabular-nums min-w-[28px] text-right" style={{ color: "var(--text-muted)" }}>{value}%</span>
    </div>
  );
});

export default VolumeControl;
