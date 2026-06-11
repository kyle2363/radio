import React from "react";

const shimmerStyle = {
  background: "linear-gradient(90deg, var(--bg-elevated) 25%, var(--bg-surface) 50%, var(--bg-elevated) 75%)",
  backgroundSize: "200px 100%",
  animation: "skeleton-shimmer 1.5s ease-in-out infinite",
};

function Skeleton({ className = "", width, height, style: extraStyle }) {
  return (
    <div
      className={`rounded ${className}`}
      style={{ width, height, ...shimmerStyle, ...extraStyle }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div style={{ borderRadius: 12, border: "1px solid var(--border)", background: "var(--bg-surface)", padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
        <Skeleton className="rounded-lg" style={{ width: 96, height: 96, flexShrink: 0 }} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 12 }}>
          <Skeleton className="rounded" style={{ height: 20, width: 192 }} />
          <Skeleton className="rounded" style={{ height: 16, width: 128 }} />
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <Skeleton className="rounded-md" style={{ height: 20, width: 64 }} />
            <Skeleton className="rounded-md" style={{ height: 20, width: 64 }} />
            <Skeleton className="rounded-md" style={{ height: 20, width: 64 }} />
          </div>
        </div>
      </div>
    </div>
  );
}

export function SkeletonQueue() {
  return (
    <div style={{ background: "var(--bg-surface)", borderRadius: 12, padding: 20, border: "1px solid var(--border)" }}>
      <Skeleton className="rounded" style={{ height: 12, width: 80, marginBottom: 16 }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {[1, 2, 3].map((i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <Skeleton className="rounded" style={{ height: 16, width: 192 }} />
            <Skeleton className="rounded" style={{ height: 12, width: 64 }} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function SkeletonRow({ count = 8 }) {
  return (
    <div style={{ borderRadius: 12, border: "1px solid var(--border)", background: "var(--bg-surface)", overflow: "hidden" }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{
          display: "flex", alignItems: "center", gap: 12,
          padding: "12px 16px",
          borderBottom: i < count - 1 ? "1px solid var(--border-subtle)" : "none",
        }}>
          <Skeleton className="rounded" style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
            <Skeleton className="rounded" style={{ height: 14, width: `${50 + Math.random() * 30}%`, minWidth: 120 }} />
            <Skeleton className="rounded" style={{ height: 11, width: `${35 + Math.random() * 20}%`, minWidth: 80 }} />
          </div>
          <Skeleton className="rounded" style={{ height: 12, width: 40 }} />
          <Skeleton className="rounded" style={{ height: 12, width: 40 }} />
          <Skeleton className="rounded" style={{ height: 12, width: 40 }} />
        </div>
      ))}
    </div>
  );
}

export function SkeletonStats() {
  return (
    <div style={{ background: "var(--bg-surface)", borderRadius: 12, padding: 20, border: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 16 }}>
      <Skeleton className="rounded" style={{ height: 12, width: 96 }} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        {[1, 2, 3].map((i) => (
          <div key={i} style={{ background: "var(--bg-elevated)", borderRadius: 8, padding: 12, display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
            <Skeleton className="rounded" style={{ height: 24, width: 48 }} />
            <Skeleton className="rounded" style={{ height: 12, width: 64 }} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function SkeletonQueueCompact({ count = 5 }) {
  return (
    <div>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{
          background: "var(--bg-elevated)",
          padding: "6px 8px",
          marginBottom: "3px",
          borderLeft: "3px solid transparent",
          borderTop: "1px solid var(--border-subtle)",
          borderRight: "1px solid var(--border-subtle)",
          borderBottom: "1px solid var(--border-subtle)",
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 5, marginBottom: 2 }}>
            <Skeleton className="rounded" style={{ height: 12, width: "60%", minWidth: 80 }} />
            <Skeleton className="rounded" style={{ height: 12, width: 40, flexShrink: 0 }} />
          </div>
          <Skeleton className="rounded" style={{ height: 8, width: "45%", marginBottom: 4 }} />
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Skeleton className="rounded" style={{ height: 8, width: 12 }} />
            <Skeleton className="rounded" style={{ height: 3, width: 34 }} />
            <Skeleton className="rounded" style={{ height: 8, width: 12 }} />
            <Skeleton className="rounded" style={{ height: 3, width: 34 }} />
            <Skeleton className="rounded" style={{ height: 8, width: 40, marginLeft: "auto" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default Skeleton;
