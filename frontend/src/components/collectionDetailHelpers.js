export const COMPLETION_PAGE_SIZE = 50;

export function completionPercent(song) {
  return song?.avg_completion == null ? 0 : Math.round(song.avg_completion);
}

export function songSearchParams(search, page) {
  return {
    page: String(page),
    page_size: String(COMPLETION_PAGE_SIZE),
    sort_by: "title",
    summary: "true",
    ...(search.trim() ? { search: search.trim() } : {}),
  };
}
