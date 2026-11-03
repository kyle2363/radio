import React from "react";

export default function HeaderStatusPill({ dotColor, children, style, ...props }) {
  return (
    <span className="hidden sm:inline-flex items-center gap-[5px] px-[10px] py-[4px] rounded-full text-[12px] whitespace-nowrap"
      style={{
        background: "var(--bg-muted)",
        border: "1px solid var(--border)",
        color: "var(--text-secondary)",
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
