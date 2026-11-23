import React, { useEffect, useState } from "react";
import { Cloud } from "lucide-react";
import { remoteHostingPill } from "../ui/consistency.js";

const TONE_COLORS = { ok: "var(--accent)", warn: "var(--warning)", err: "var(--danger)" };

// Small header pill shown only while Cloudflare remote hosting is running.
export default function RemoteHostingPill({ onOpenSettings }) {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    const electron = window.electron;
    if (!electron?.getRemoteHostingStatus) return undefined;
    let alive = true;
    const refresh = () => electron.getRemoteHostingStatus().then((v) => { if (alive) setStatus(v); }).catch(() => {});
    refresh();
    const timer = setInterval(refresh, 10000);
    const unsubscribe = electron.onRemoteHostingChange?.((v) => { if (alive) setStatus(v); });
    return () => { alive = false; clearInterval(timer); unsubscribe?.(); };
  }, []);

  const pill = remoteHostingPill(status);
  if (!pill) return null;
  const color = TONE_COLORS[pill.tone];
  return (
    <button type="button" onClick={onOpenSettings} data-tooltip={pill.title} aria-label={pill.title}
      className="hidden sm:inline-flex items-center gap-[5px] rounded-full text-[11px] whitespace-nowrap cursor-pointer"
      style={{ padding: "3px 9px", background: "var(--bg-muted)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color, flexShrink: 0,
        animation: pill.tone === "warn" ? "pulse 1.6s ease-in-out infinite" : undefined }} />
      <Cloud size={11} style={{ color }} />
      {pill.label}
    </button>
  );
}
