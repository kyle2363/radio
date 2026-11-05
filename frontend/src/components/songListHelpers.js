export function buildMetadataPayload(form) {
  const payload = {};
  for (const field of ["artist", "title", "album", "keyscale", "timesignature", "vocal_language"]) {
    if (form[field] !== undefined) payload[field] = String(form[field]).trim();
  }
  if (form.bpm !== "") payload.bpm = parseInt(form.bpm, 10);
  if (form.lyrics !== undefined) payload.lyrics = form.lyrics;
  if (form.caption !== undefined) payload.caption = form.caption;
  return payload;
}

export function metadataSaveMessage(response) {
  if (response?.error || (response?.status && response.status !== "updated")) {
    return response.error || "Metadata update failed";
  }
  const failures = [];
  const id3Failures = Object.entries(response?.id3 || {}).filter(([, status]) => status === "error").map(([field]) => field);
  if (id3Failures.length) failures.push(`MP3 tags (${id3Failures.join(", ")})`);
  const ace = response?.acestep;
  if (ace?.error || ["error", "no_match", "invalid_format"].includes(ace?.status) || ace?.fields_error?.length) {
    failures.push(`metadata JSON: ${ace.error || ace.fields_error?.join(", ") || ace.status}`);
  }
  for (const [label, result] of Object.entries(response?.rename || {})) {
    if (result?.error || result?.status === "error") failures.push(`${label} rename: ${result.error || result.reason || "rename failed"}`);
  }
  if (failures.length) return `Metadata saved to library, but ${failures.join("; ")}`;
  return null;
}
