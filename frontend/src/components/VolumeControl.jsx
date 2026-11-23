import React, { useState, useEffect, useRef } from "react";
import { Volume1, Volume2, VolumeX } from "lucide-react";

const COMMIT_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);

const VolumeControl = React.memo(function VolumeControl({ volume, onChange }) {
  const [value, setValue] = useState(volume ?? 70);
  const prevValueRef = useRef(volume ?? 70);

  useEffect(() => { setValue(volume ?? 70); }, [volume]);

  const commit = () => onChange?.(value);

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

  const Icon = value === 0 ? VolumeX : value < 50 ? Volume1 : Volume2;

  return (
    <div className="dial">
      <div className="dial-head">
        <span className="dial-name">
          <button type="button" onClick={toggleMute} title={value === 0 ? "Unmute" : "Mute"} aria-label={value === 0 ? "Unmute" : "Mute"}>
            <Icon size={14} />
          </button>
          Volume
        </span>
        <span className="dial-value">{value}%</span>
      </div>
      <input type="range" min="0" max="100" value={value} aria-label="Volume"
        className="ui-slider" style={{ "--fill": `${value}%` }}
        onChange={(e) => setValue(parseInt(e.target.value, 10))}
        onPointerUp={commit} onKeyUp={(e) => { if (COMMIT_KEYS.has(e.key)) commit(); }} />
    </div>
  );
});

export default VolumeControl;
