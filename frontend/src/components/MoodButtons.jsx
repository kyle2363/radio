import React from "react";
import { Moon, Bolt, Target, CloudRain, Compass } from "lucide-react";

const MOODS = [
  { id: "chill", label: "Chill", icon: Moon, activeColor: "#4eccc4" },
  { id: "energetic", label: "Energy", icon: Bolt, activeColor: "#ff7840" },
  { id: "focus", label: "Focus", icon: Target, activeColor: "#b460f0" },
  { id: "melancholy", label: "Sad", icon: CloudRain, activeColor: "#7890f0" },
  { id: "discovery", label: "Disc.", icon: Compass, activeColor: "#50c870" },
];

const MoodButtons = React.memo(function MoodButtons({ activeMood, onMoodSet }) {
  return (
    <div className="mood-seg">
      {MOODS.map((mood) => {
        const isActive = activeMood === mood.id;
        const Icon = mood.icon;
        return (
          <button
            key={mood.id}
            onClick={() => onMoodSet(isActive ? "neutral" : mood.id)}
            className={`mood-opt ${isActive ? "active" : ""}`}
            title={mood.label}
            style={isActive ? { boxShadow: `0 0 14px ${mood.activeColor}25` } : undefined}>
            <Icon size={16} />
            <span>{mood.label}</span>
          </button>
        );
      })}
    </div>
  );
});

export default MoodButtons;
