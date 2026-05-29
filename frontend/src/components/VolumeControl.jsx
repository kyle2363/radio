import React, { useState, useEffect } from "react";
import { Volume2, VolumeX } from "lucide-react";

const VolumeControl = React.memo(function VolumeControl({ volume, onChange }) {
  const [value, setValue] = useState(volume ?? 70);

  useEffect(() => { setValue(volume ?? 70); }, [volume]);

  const handleChange = (e) => {
    const newVal = parseInt(e.target.value);
    setValue(newVal);
    onChange?.(newVal);
  };

  return (
    <div className="flex items-center gap-3 rounded-xl px-5 py-3" style={{ background: "var(--bg-surface)" }}>
      <span style={{ color: "var(--text-muted)" }}>{value === 0 ? <VolumeX size={14} /> : <Volume2 size={14} />}</span>
      <div className="flex-1 relative h-[3px] rounded-full cursor-pointer" style={{ background: "var(--border)" }}>
        <div className="absolute h-full rounded-full" style={{ width: `${value}%`, background: "var(--accent)" }} />
        <input type="range" min="0" max="100" value={value} onChange={handleChange}
          className="absolute inset-0 w-full opacity-0 cursor-pointer" />
      </div>
      <span className="text-[11px] tabular-nums min-w-[28px] text-right" style={{ color: "var(--text-muted)" }}>{value}%</span>
    </div>
  );
});

export default VolumeControl;
