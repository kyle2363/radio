"""Batch generate lyrics timing data using WhisperX forced alignment.

Usage:
    python batch_lyrics_timing.py                         # All songs with lyrics
    python batch_lyrics_timing.py --song-id 42            # Single song
    python batch_lyrics_timing.py --resume                # Skip already-timed songs
    python batch_lyrics_timing.py --dry-run               # Preview what would be processed

Output: data/lyrics_timing/{song_id}.json

The acestep JSON files remain read-only. Each output file contains
per-line timing data derived from WhisperX word-level forced alignment
matched against the original lyrics text.
"""

import argparse
import json
import os
import sys
import time
from tqdm import tqdm

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

import config
from db import database

# ── Word-level matching ─────────────────────────────────────


def _normalize_word(w: str) -> str:
    """Normalize a word for fuzzy comparison."""
    return w.lower().strip(".,!?;:'\"()[]‹›«»-…”“").replace("'", "").replace("’", "").replace("`", "")


def _is_section_marker(line: str) -> bool:
    s = line.strip()
    return s.startswith("[") and s.endswith("]")


def _build_stored_word_index(lyrics_text: str):
    """Split stored lyrics into a flat list of normalized words with line index.

    Returns (lines, word_index) where word_index is list of
    (normalized_word, original_word, line_idx).
    """
    lines = lyrics_text.split("\n")
    word_index = []
    for li, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _is_section_marker(stripped):
            continue
        for w in stripped.split():
            nw = _normalize_word(w)
            if nw:
                word_index.append((nw, w, li))
    return lines, word_index


def align_lyrics(lyrics_text: str, wx_words: list, duration_ms: float) -> list:
    """Align original lyrics lines to WhisperX word timestamps using difflib.

    Returns list of dicts with keys: text, start_ms, end_ms, words.
    """
    if not lyrics_text or not wx_words:
        return []

    lines, stored_index = _build_stored_word_index(lyrics_text)
    if not stored_index:
        return [{"text": l, "start_ms": 0, "end_ms": 0, "words": []} for l in lines]

    # Normalize WhisperX words
    wx_norm = [_normalize_word(w.get("word", "")) for w in wx_words]
    wx_orig = wx_words

    # Use difflib to find matching blocks between stored words and wx words
    import difflib

    stored_norm = [s[0] for s in stored_index]
    matcher = difflib.SequenceMatcher(None, stored_norm, wx_norm, autojunk=False)
    matching_blocks = matcher.get_matching_blocks()

    # Build a mapping: stored word index -> wx word index
    stored_to_wx = {}  # stored_idx -> wx_idx (first matching wx word)
    block_map = {}  # stored_idx -> (wx_idx, count of matched words in this block)
    for block in matching_blocks:
        si, wi, n = block
        if n > 0:
            for k in range(n):
                block_map[si + k] = wi + k

    # For each stored word, assign timestamps from matching wx word
    line_timings = {}  # line_idx -> {start_ms, end_ms, words}
    for stored_idx, (nw, orig_w, li) in enumerate(stored_index):
        if li not in line_timings:
            line_timings[li] = {"start_ms": None, "end_ms": None, "words": []}
        wx_idx = block_map.get(stored_idx)
        if wx_idx is not None and wx_idx < len(wx_orig):
            wxo = wx_orig[wx_idx]
            ts = round(wxo["start"] * 1000)
            te = round(wxo["end"] * 1000)
            lt = line_timings[li]
            if lt["start_ms"] is None or ts < lt["start_ms"]:
                lt["start_ms"] = ts
            if lt["end_ms"] is None or te > lt["end_ms"]:
                lt["end_ms"] = te
            lt["words"].append({
                "word": wxo["word"],
                "start_ms": ts,
                "end_ms": te,
            })

    # Build result
    result = []
    for li, line in enumerate(lines):
        stripped = line.strip()
        if li in line_timings:
            lt = line_timings[li]
            result.append({
                "text": stripped,
                "start_ms": lt["start_ms"] or 0,
                "end_ms": lt["end_ms"] or 0,
                "words": lt["words"],
            })
        else:
            result.append({"text": stripped if stripped else "", "start_ms": 0, "end_ms": 0, "words": []})

    _interpolate_timing(result, duration_ms)
    return result


def _interpolate_timing(lines: list, duration_ms: float):
    """Fill timing for section markers, blank lines, and untimed content lines."""
    n = len(lines)

    # 1) Section markers — copy next content line time
    for i in range(n):
        line = lines[i]
        if line["start_ms"] == 0 and line["end_ms"] == 0 and _is_section_marker(line["text"]):
            for j in range(i + 1, n):
                if lines[j]["start_ms"] > 0:
                    line["start_ms"] = lines[j]["start_ms"]
                    line["end_ms"] = lines[j]["start_ms"]
                    break
            if line["start_ms"] == 0:
                for j in range(i - 1, -1, -1):
                    if lines[j]["end_ms"] > 0:
                        line["start_ms"] = lines[j]["end_ms"]
                        line["end_ms"] = lines[j]["end_ms"]
                        break

    # 2) Blank lines — midpoint of surrounding content
    for i in range(n):
        line = lines[i]
        if line["start_ms"] == 0 and line["end_ms"] == 0 and line["text"] == "":
            prev_end = None
            next_start = None
            for j in range(i - 1, -1, -1):
                if lines[j]["end_ms"] > 0:
                    prev_end = lines[j]["end_ms"]
                    break
            for j in range(i + 1, n):
                if lines[j]["start_ms"] > 0:
                    next_start = lines[j]["start_ms"]
                    break
            if prev_end is not None and next_start is not None:
                mid = (prev_end + next_start) // 2
                line["start_ms"] = mid
                line["end_ms"] = mid
            elif prev_end is not None:
                line["start_ms"] = prev_end
                line["end_ms"] = min(prev_end + 500, duration_ms)
            elif next_start is not None:
                line["start_ms"] = max(0, next_start - 500)
                line["end_ms"] = next_start

    # 3) Untimed content lines — distribute evenly within gaps between timed regions
    # Find stretches of consecutive untimed content lines
    i = 0
    while i < n:
        if lines[i]["start_ms"] == 0 and lines[i]["end_ms"] == 0 and lines[i]["text"] and not _is_section_marker(lines[i]["text"]):
            # Find start of this untimed stretch
            stretch_start = i
            while i < n and lines[i]["start_ms"] == 0 and lines[i]["end_ms"] == 0 and lines[i]["text"]:
                i += 1
            stretch_end = i
            untimed_indices = list(range(stretch_start, stretch_end))

            # Find previous timed boundary
            prev_end = 0
            for j in range(stretch_start - 1, -1, -1):
                if lines[j]["end_ms"] > 0:
                    prev_end = lines[j]["end_ms"]
                    break

            # Find next timed boundary
            next_start = duration_ms
            for j in range(stretch_end, n):
                if lines[j]["start_ms"] > 0:
                    next_start = lines[j]["start_ms"]
                    break

            # Distribute evenly
            gap = next_start - prev_end
            if untimed_indices and gap > 0:
                step = gap // (len(untimed_indices) + 1)
                for k, idx in enumerate(untimed_indices):
                    t = prev_end + (k + 1) * step
                    lines[idx]["start_ms"] = t
                    lines[idx]["end_ms"] = t
        else:
            i += 1


# ── WhisperX forced alignment ──────────────────────────────

_MODEL_CACHE = {}  # model_name -> whisper model
_ALIGN_CACHE = {}  # (language, device) -> (model, metadata)


def _get_whisper_model(model_name: str, device: str, compute_type: str):
    """Load and cache Whisper model with tuned parameters."""
    key = (model_name, device)
    if key not in _MODEL_CACHE:
        import whisperx
        _MODEL_CACHE[key] = whisperx.load_model(
            model_name, device, compute_type=compute_type,
            asr_options={
                "beam_size": config.WHISPERX_BEAM_SIZE,
                "patience": config.WHISPERX_PATIENCE,
                "no_speech_threshold": config.WHISPERX_NO_SPEECH_THRESHOLD,
                "compression_ratio_threshold": config.WHISPERX_COMPRESSION_RATIO_THRESHOLD,
            },
            vad_options={
                "vad_onset": config.WHISPERX_VAD_ONSET,
                "vad_offset": config.WHISPERX_VAD_OFFSET,
            },
        )
    return _MODEL_CACHE[key]


def _get_align_model(language: str, device: str):
    """Load and cache alignment model."""
    key = (language, device)
    if key not in _ALIGN_CACHE:
        try:
            from whisperx.alignment import load_align_model
            model, metadata = load_align_model(language_code=language, device=device)
            _ALIGN_CACHE[key] = (model, metadata)
        except Exception as e:
            raise RuntimeError(f"Failed to load alignment model for '{language}': {e}")
    return _ALIGN_CACHE[key]


def run_whisperx_pipeline(audio_path: str, language: str = "en", separate_vocals: bool = True) -> list:
    """Run full WhisperX pipeline (transcribe → align).

    If separate_vocals is True and config.WHISPERX_SEPARATE_VOCALS is set,
    Demucs-based vocal separation is applied first for cleaner transcription.

    Returns list of {word, start, end, score} dicts with word-level timestamps.
    """
    import whisperx

    # Optional vocal separation to improve transcription accuracy
    input_path = audio_path
    if separate_vocals and config.WHISPERX_SEPARATE_VOCALS:
        try:
            from engine.audio_separator import get_vocals_path
            vocals_path = get_vocals_path(audio_path)
            if vocals_path and os.path.exists(vocals_path):
                input_path = vocals_path
        except Exception as e:
            print(f"    Vocal separation failed (falling back to original): {e}")

    device = "cuda" if __import__("torch").cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    # Step 1: Transcribe with Whisper
    model = _get_whisper_model("large-v3", device, compute_type)
    result = model.transcribe(input_path, batch_size=14, language=language)

    # Step 2: Align with wav2vec2
    align_model, metadata = _get_align_model(language, device)
    result = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        input_path,
        device,
        return_char_alignments=False,
    )

    # Extract word-level segments
    word_segments = []
    for seg in result.get("segments", []):
        for word in seg.get("words", []):
            word_segments.append({
                "word": word.get("word", "").strip(),
                "start": word.get("start", 0.0),
                "end": word.get("end", 0.0),
                "score": word.get("score", 0.0),
            })

    return word_segments


# ── File I/O ────────────────────────────────────────────────


def load_timing(song_id: int) -> dict | None:
    """Load existing timing data for a song, if any."""
    path = os.path.join(config.LYRICS_TIMING_DIR, f"{song_id}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_timing(song_id: int, data: dict):
    """Save timing data for a song."""
    os.makedirs(config.LYRICS_TIMING_DIR, exist_ok=True)
    path = os.path.join(config.LYRICS_TIMING_DIR, f"{song_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Main processing ────────────────────────────────────────


def process_song(song_id: int, dry_run: bool = False) -> dict:
    """Process a single song: align lyrics, save timing data.

    Returns status dict with keys: song_id, status, message.
    """
    song = database.get_song(song_id)
    if not song:
        return {"song_id": song_id, "status": "skipped", "message": "Song not found in DB"}

    filepath = song.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        return {"song_id": song_id, "status": "skipped", "message": "Audio file not found"}

    lyrics_text = song.get("lyrics", "")
    if not lyrics_text or lyrics_text in ("lyrics not found", "TODO"):
        return {"song_id": song_id, "status": "skipped", "message": "No valid lyrics"}

    duration_secs = song.get("duration_secs") or 0
    duration_ms = duration_secs * 1000

    if dry_run:
        return {"song_id": song_id, "status": "dry_run", "message": f"Would process {len(lyrics_text.split(chr(10)))} lines"}

    # Get vocal language from metadata if available
    language = "en"  # default
    try:
        from ingest.metadata import load_all_metadata, get_metadata_for_mp3
        meta_index = load_all_metadata()
        meta = get_metadata_for_mp3(filepath, meta_index)
        if meta and meta.get("vocal_language"):
            lang = meta["vocal_language"]
            # Map to alignment model codes that WhisperX supports
            SUPPORTED = {"en", "fr", "de", "es", "it", "ja", "ko", "pt", "zh", "nl", "uk", "pl"}
            if lang in SUPPORTED:
                language = lang
    except Exception:
        pass  # fall back to "en"

    try:
        # Run forced alignment
        wx_words = run_whisperx_pipeline(filepath, language=language)

        if not wx_words:
            return {"song_id": song_id, "status": "failed", "message": "WhisperX returned no word segments"}

        # Align to original lyrics
        timed_lines = align_lyrics(lyrics_text, wx_words, duration_ms)

        # Build output
        output = {
            "song_id": song_id,
            "source": "whisperx",
            "language": language,
            "duration_ms": round(duration_ms),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lines": timed_lines,
        }

        save_timing(song_id, output)
        return {"song_id": song_id, "status": "done", "message": f"Timed {len(timed_lines)} lines"}

    except Exception as e:
        return {"song_id": song_id, "status": "failed", "message": str(e)}


def run_batch(song_ids: list[int], dry_run: bool = False):
    """Process a batch of songs with a progress bar."""
    results = {"done": 0, "skipped": 0, "failed": 0, "details": []}
    for sid in tqdm(song_ids, desc="Timing lyrics"):
        result = process_song(sid, dry_run=dry_run)
        results["details"].append(result)
        results[result["status"]] = results.get(result["status"], 0) + 1
        tqdm.write(f"  [{result['status']}] Song {sid}: {result['message']}")
    return results


# ── CLI ─────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Generate lyrics timing data via WhisperX")
    parser.add_argument("--song-id", type=int, help="Process only this song ID")
    parser.add_argument("--resume", action="store_true", help="Skip songs that already have timing data")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without doing it")
    parser.add_argument("--limit", type=int, help="Max songs to process (for testing)")
    args = parser.parse_args()

    # Gather target songs
    target_ids = []
    if args.song_id:
        target_ids = [args.song_id]
    else:
        conn = database.get_connection()
        try:
            rows = conn.execute(
                "SELECT id, lyrics FROM songs WHERE lyrics IS NOT NULL AND lyrics != '' AND lyrics != 'lyrics not found' AND lyrics != 'TODO'"
            ).fetchall()
            target_ids = [r["id"] for r in rows]
        finally:
            conn.close()

    if args.resume:
        existing = set()
        for sid in target_ids:
            data = load_timing(sid)
            if data and data.get("lines") and any(l.get("start_ms") for l in data["lines"]):
                existing.add(sid)
        filtered = [sid for sid in target_ids if sid not in existing]
        skipped = len(target_ids) - len(filtered)
        target_ids = filtered
        print(f"Skipping {skipped} already-timed songs")

    if args.limit and len(target_ids) > args.limit:
        target_ids = target_ids[:args.limit]

    if not target_ids:
        print("No songs to process.")
        return

    print(f"Processing {len(target_ids)} songs...")

    if args.dry_run:
        print(f"Would process {len(target_ids)} songs:")
        for sid in target_ids:
            song = database.get_song(sid)
            title = f"{song.get('artist', '?')} - {song.get('title', '?')}" if song else "?"
            print(f"  {sid}: {title}")
        return

    results = run_batch(target_ids)
    print(f"\nDone: {results.get('done', 0)} timed, {results.get('skipped', 0)} skipped, {results.get('failed', 0)} failed")

    failures = [d for d in results.get("details", []) if d.get("status") == "failed"]
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  Song {f['song_id']}: {f['message']}")


if __name__ == "__main__":
    main()
