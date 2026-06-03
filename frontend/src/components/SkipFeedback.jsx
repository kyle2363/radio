import React from "react";

const REASONS = [
  { key: "too_intense", label: "Too intense?" },
  { key: "too_slow", label: "Too slow?" },
  { key: "too_samey", label: "Too samey?" },
  { key: "wrong_mood", label: "Wrong mood?" },
  { key: "just_skip", label: "Just skip it." },
];

function SkipFeedback({ visible, onReason, onDismiss }) {
  if (!visible) return null;

  return (
    <div className="fixed bottom-20 right-4 z-[99] max-w-xs">
      <div className="bg-blue-600/90 border border-blue-400/60 rounded-xl px-4 py-3 shadow-lg backdrop-blur-sm" style={{animation: "fadeIn 200ms ease-out"}}>
        <p className="text-sm text-white font-medium mb-2">What's not working?</p>
        <div className="flex flex-col gap-1.5">
          {REASONS.map((r) => (
            <button
              key={r.key}
              onClick={() => onReason(r.key)}
              className="text-xs text-left text-white/90 hover:text-white hover:bg-white/10 rounded-lg px-3 py-1.5 transition-colors"
            >
              {r.label}
            </button>
          ))}
        </div>
        <button
          onClick={onDismiss}
          className="mt-2 text-xs text-white/50 hover:text-white/80"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

export default SkipFeedback;