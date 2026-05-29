import React from "react";
import { Moon, Bolt, Target, CloudRain, Compass } from "lucide-react";

const MOODS = [
  { id: "chill", label: "Chill", icon: Moon, color: "#4eccc4", bg: "rgba(78,204,196,0.07)", border: "rgba(78,204,196,0.3)" },
  { id: "energetic", label: "Energy", icon: Bolt, color: "#ff7840", bg: "rgba(255,120,64,0.07)", border: "rgba(255,120,64,0.3)" },
  { id: "focus", label: "Focus", icon: Target, color: "#b460f0", bg: "rgba(180,96,240,0.07)", border: "rgba(180,96,240,0.3)" },
  { id: "melancholy", label: "Melancholy", icon: CloudRain, color: "#7890f0", bg: "rgba(120,144,240,0.07)", border: "rgba(120,144,240,0.3)" },
  { id: "discovery", label: "Discover", icon: Compass, color: "#50c870", bg: "rgba(80,200,112,0.07)", border: "rgba(80,200,112,0.3)" },
];

const MoodButtons = React.memo(function MoodButtons({ activeMood, onMoodSet }) {
  return (
    <div className="flex gap-2.5">
      {MOODS.map((mood) => {
        const isActive = activeMood === mood.id;
        const Icon = mood.icon;
        return (
          <button
            key={mood.id}
            onClick={() => onMoodSet(mood.id)}
            className="flex-1 flex flex-col items-center gap-1.5 py-3 px-2.5 rounded-xl text-xs font-medium transition"
            style={{
              background: isActive ? mood.bg : "var(--bg-muted)",
              color: isActive ? mood.color : "var(--text-muted)",
              border: isActive ? `1px solid ${mood.border}` : "none",
            }}>
            <Icon size={16} />
            {mood.label}
          </button>
        );
      })}
    </div>
  );
});

export default MoodButtons;
