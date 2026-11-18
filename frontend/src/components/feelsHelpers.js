// Pure helpers for the desktop Feels panel (checkpoints 9D–9E).
export const ENERGY_FEEL_ID = "00000000-0000-4000-8000-0000000000e1";

/** Energy buckets shared with the phone (FeelCatalog.ENERGY_BUCKETS). */
export const ENERGY_BUCKETS = [
  { key: "hot", label: "Hot", value: 0.875 },
  { key: "warm", label: "Warm", value: 0.625 },
  { key: "mellow", label: "Mellow", value: 0.375 },
  { key: "cold", label: "Cold", value: 0.125 },
];

export const SUGGESTION_MODES = [
  { value: "off", label: "Off" },
  { value: "song_page", label: "On the song page" },
  { value: "after_session", label: "After a session" },
];

export function songLabel(song) {
  const title = song?.title || "Unknown title";
  return song?.artist ? `${song.artist} — ${title}` : title;
}

/** "Hype since March 2026 · Wind down since October 2026" for current members. */
export function historySummary(history, locale = undefined) {
  const fmt = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" });
  return (history || [])
    .filter((h) => h.member && h.since)
    .map((h) => `${h.feel.name} since ${fmt.format(new Date(h.since * 1000))}`)
    .join(" · ");
}

export function settledLabel(percent) {
  const p = Math.round(percent || 0);
  if (p >= 70) return `${p}% settled — more rounds change little`;
  return `${p}% settled`;
}

/** Map a this-or-that choice to the API outcome. */
export function pairOutcome(choice) {
  return { left: "a", right: "b", tie: "tie", unfitLeft: "doesnt_fit_a", unfitRight: "doesnt_fit_b" }[choice] ?? null;
}

export function validFeelName(name) {
  const trimmed = (name || "").trim();
  return trimmed.length >= 1 && trimmed.length <= 40;
}
