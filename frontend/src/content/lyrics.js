/** Rendering always receives an array, including the timing-fetch interval. */
export function buildLyricsLines(timedLines, lyrics) {
  if (Array.isArray(timedLines)) return timedLines;
  if (typeof lyrics !== 'string' || !lyrics) return [];
  return lyrics.split('\n').map(text => ({ text, start_ms: 0, end_ms: 0, words: [] }));
}
