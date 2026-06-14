import React from "react";

export default function HeaderStatusPill({ dotColor, children, style, ...props }) {
  return (
    <span className="hidden sm:inline-flex items-center gap-[5px] px-[10px] py-[4px] rounded-full text-[12px] whitespace-nowrap"
      style={{
        background: "rgba(255,255,255,0.04)",
        border: "0.5px solid rgba(255,255,255,0.08)",
        color: "#aab4c0",
        ...style,
      }}
      {...props}>
      {dotColor && (
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: dotColor, flexShrink: 0 }} />
      )}
      {children}
    </span>
  );
}
