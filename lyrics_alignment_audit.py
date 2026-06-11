"""Audit lyrics alignment quality by comparing WhisperX transcription to original lyrics.

Runs WhisperX on a sample of songs, captures the raw transcription, and compares
against the original lyrics text to diagnose where timing issues come from.

Usage:
    python lyrics_alignment_audit.py --limit 5             # audit 5 songs
    python lyrics_alignment_audit.py --song-id 42          # audit one song
    python lyrics_alignment_audit.py --sample random 10    # random 10 songs
    python lyrics_alignment_audit.py --worst 10            # pick songs with worst existing timing
    python lyrics_alignment_audit.py --compare-existing     # also load existing timing data
    python lyrics_alignment_audit.py --skip-whisperx        # skip WhisperX re-run, use existing data only

Output: data/lyrics_audit/{song_id}.json + console summary
"""

import argparse
import json
import os
import random
import sys
import time
from collections import Counter

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

import config
from db import database

# Reuse WhisperX pipeline from batch_lyrics_timing
from batch_lyrics_timing import (
    run_whisperx_pipeline,
    _normalize_word,
    _is_section_marker,
    _build_stored_word_index,
    load_timing,
)

AUDIT_DIR = os.path.join(config.DATA_DIR, "lyrics_audit")


def _safe(s: str) -> str:
    """Sanitize string for Windows console (cp1252) output."""
    return s.encode("ascii", "replace").decode("ascii")


# ── Word-level comparison ──────────────────────────────────


def _build_whisperx_word_list(wx_words: list) -> list:
    """Build a flat list of normalized WhisperX words with their indices."""
    return [(i, _normalize_word(w.get("word", "")), w) for i, w in enumerate(wx_words)]


def compare_words(lyrics_text: str, wx_words: list):
    """Compare original lyrics words against WhisperX word list.

    Returns dict with match stats, matched pairs, and unmatched words.
    """
    # Build original lyrics word list
    _, stored_index = _build_stored_word_index(lyrics_text)
    if not stored_index:
        return {
            "original_words": [],
            "whisperx_words": [w.get("word", "") for w in wx_words],
            "matches": [],
            "unmatched_original": [],
            "extra_whisperx": [],
            "metrics": {
                "original_word_count": 0,
                "whisperx_word_count": len(wx_words),
                "matched_words": 0,
                "match_rate": 0.0,
                "avg_alignment_score": 0.0,
                "min_alignment_score": 0.0,
                "low_score_words": [],
                "levenshtein_distance": 0,
            },
        }

    stored_norm = [s[0] for s in stored_index]  # (normalized_word, original_word, line_idx)
    wx_norm = [_normalize_word(w.get("word", "")) for w in wx_words]

    # Use difflib to find matching blocks
    import difflib

    matcher = difflib.SequenceMatcher(None, stored_norm, wx_norm, autojunk=False)
    matching_blocks = matcher.get_matching_blocks()

    # Build mapping: stored_idx -> wx_idx
    block_map = {}
    for block in matching_blocks:
        si, wi, n = block
        if n > 0:
            for k in range(n):
                block_map[si + k] = wi + k

    # Collect matches and unmatched
    matches = []
    matched_wx_indices = set()
    unmatched_original = []
    low_score_words = []

    for stored_idx, (nw, orig_w, li) in enumerate(stored_index):
        wx_idx = block_map.get(stored_idx)
        if wx_idx is not None and wx_idx < len(wx_words):
            wxo = wx_words[wx_idx]
            score = wxo.get("score", 0.0)
            matches.append({
                "original_word": orig_w,
                "whisperx_word": wxo.get("word", "").strip(),
                "start_ms": round(wxo["start"] * 1000),
                "end_ms": round(wxo["end"] * 1000),
                "score": round(score, 4),
                "matched": True,
                "line_idx": li,
            })
            matched_wx_indices.add(wx_idx)
            if score < 0.5:
                low_score_words.append({
                    "word": orig_w,
                    "whisperx_word": wxo.get("word", "").strip(),
                    "score": round(score, 4),
                    "start_ms": round(wxo["start"] * 1000),
                })
        else:
            unmatched_original.append({
                "word": orig_w,
                "line_idx": li,
            })

    # Extra WhisperX words not matched to original
    extra_whisperx = []
    for i, w in enumerate(wx_words):
        if i not in matched_wx_indices:
            extra_whisperx.append({
                "word": w.get("word", "").strip(),
                "start_ms": round(w["start"] * 1000),
                "end_ms": round(w["end"] * 1000),
                "score": round(w.get("score", 0.0), 4),
            })

    # Metrics
    orig_count = len(stored_norm)
    wx_count = len(wx_words)
    matched_count = len(matches)
    match_rate = matched_count / orig_count if orig_count > 0 else 0.0

    scores = [m["score"] for m in matches]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    min_score = min(scores) if scores else 0.0

    # Levenshtein distance between original and WhisperX text
    from rapidfuzz.distance import Levenshtein

    original_text = " ".join(s[0] for s in stored_index)
    wx_text = " ".join(w.get("word", "").strip() for w in wx_words if w.get("word", "").strip())
    lev_dist = Levenshtein.distance(original_text, wx_text)

    return {
        "original_words": [s[1] for s in stored_index],
        "whisperx_words": [w.get("word", "").strip() for w in wx_words],
        "matches": matches,
        "unmatched_original": unmatched_original,
        "extra_whisperx": extra_whisperx,
        "metrics": {
            "original_word_count": orig_count,
            "whisperx_word_count": wx_count,
            "matched_words": matched_count,
            "match_rate": round(match_rate, 4),
            "avg_alignment_score": round(avg_score, 4),
            "min_alignment_score": round(min_score, 4),
            "low_score_words": low_score_words,
            "levenshtein_distance": lev_dist,
        },
    }


def compute_line_timing_quality(original_lyrics: str, timed_lines: list) -> dict:
    """Compute quality metrics on the existing timed_lines output."""
    lines_with_words = [l for l in timed_lines if l.get("words") and len(l["words"]) > 0]
    lines_with_timing = [l for l in timed_lines if l.get("start_ms", 0) > 0]
    content_lines = [l for l in timed_lines if l.get("text", "").strip() and not _is_section_marker(l.get("text", ""))]

    # Check for gaps between timed lines
    timed_starts = sorted([l["start_ms"] for l in lines_with_timing])
    max_gap = 0
    for i in range(1, len(timed_starts)):
        gap = timed_starts[i] - timed_starts[i - 1]
        if gap > max_gap:
            max_gap = gap

    return {
        "total_lines": len(timed_lines),
        "content_lines": len(content_lines),
        "lines_with_words": len(lines_with_words),
        "lines_with_timing": len(lines_with_timing),
        "timed_content_pct": round(len(lines_with_words) / len(content_lines), 4) if content_lines else 0,
        "max_gap_ms": max_gap,
    }


# ── Song selection ─────────────────────────────────────────


def select_songs(args) -> list:
    """Select songs to audit based on CLI args."""
    conn = database.get_connection()
    try:
        # All songs with lyrics
        rows = conn.execute(
            "SELECT id, artist, title, lyrics, filepath, duration_secs "
            "FROM songs WHERE lyrics IS NOT NULL AND lyrics != '' "
            "AND lyrics != 'lyrics not found' AND lyrics != 'TODO'"
        ).fetchall()
        songs = [dict(r) for r in rows]
    finally:
        conn.close()

    if not songs:
        print("No songs with lyrics found in database.")
        return []

    if args.song_id:
        return [s for s in songs if s["id"] == args.song_id]

    if args.worst:
        # Pick songs with worst existing timing coverage
        scored = []
        for s in songs:
            timing = load_timing(s["id"])
            if timing and timing.get("lines"):
                qt = compute_line_timing_quality(s["lyrics"], timing["lines"])
                scored.append((s, qt["timed_content_pct"]))
            else:
                scored.append((s, -1))  # no timing data = worst
        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: args.worst]]

    if args.sample == "random":
        count = args.limit or 10
        return random.sample(songs, min(count, len(songs)))

    # Default: first N songs
    count = args.limit or 5
    return songs[:count]


# ── Audit a single song ───────────────────────────────────


def audit_song(song: dict, skip_whisperx: bool = False, compare_existing: bool = False) -> dict:
    """Run full audit on a single song. Returns audit result dict."""
    song_id = song["id"]
    filepath = song.get("filepath", "")
    lyrics_text = song.get("lyrics", "")
    duration_ms = (song.get("duration_secs") or 0) * 1000

    result = {
        "song_id": song_id,
        "artist": song.get("artist") or "Unknown",
        "title": song.get("title") or "Unknown",
        "filepath": filepath,
        "duration_ms": round(duration_ms),
        "original_lyrics": lyrics_text,
        "whisperx_transcription": "",
        "word_comparison": None,
        "existing_timing_quality": None,
    }

    # Load WhisperX raw words
    wx_words = []
    if not skip_whisperx:
        if not filepath or not os.path.exists(filepath):
            result["error"] = "Audio file not found"
            return result

        # Detect language
        language = "en"
        try:
            from ingest.metadata import load_all_metadata, get_metadata_for_mp3
            meta_index = load_all_metadata()
            meta = get_metadata_for_mp3(filepath, meta_index)
            if meta and meta.get("vocal_language"):
                lang = meta["vocal_language"]
                SUPPORTED = {"en", "fr", "de", "es", "it", "ja", "ko", "pt", "zh", "nl", "uk", "pl"}
                if lang in SUPPORTED:
                    language = lang
        except Exception:
            pass

        print(f"  Running WhisperX (lang={language})...", end=" ", flush=True)
        t0 = time.time()
        try:
            wx_words = run_whisperx_pipeline(filepath, language=language)
            elapsed = time.time() - t0
            print(f"done ({len(wx_words)} words, {elapsed:.1f}s)")
        except Exception as e:
            print(f"FAILED: {e}")
            result["error"] = f"WhisperX failed: {e}"
            return result

        # Build transcription text from WhisperX words
        result["whisperx_transcription"] = " ".join(
            w.get("word", "").strip() for w in wx_words if w.get("word", "").strip()
        )
        result["whisperx_language"] = language

    # Compare words
    if wx_words:
        result["word_comparison"] = compare_words(lyrics_text, wx_words)

    # Compare with existing timing data
    if compare_existing:
        existing = load_timing(song_id)
        if existing and existing.get("lines"):
            result["existing_timing_quality"] = compute_line_timing_quality(
                lyrics_text, existing["lines"]
            )
            # Also reconstruct what words the existing timing actually timed
            existing_timed_words = set()
            for line in existing["lines"]:
                for w in line.get("words", []):
                    existing_timed_words.add(_normalize_word(w.get("word", "")))
            result["existing_timed_word_count"] = len(existing_timed_words)

    return result


# ── Output ─────────────────────────────────────────────────


def print_summary(results: list):
    """Print a console summary table."""
    if not results:
        print("No results to display.")
        return

    # Header
    print(f"\n{'='*90}")
    print(f"  LYRICS ALIGNMENT AUDIT REPORT")
    print(f"  {len(results)} songs analyzed")
    print(f"{'='*90}")

    # Column headers
    print(
        f"  {'ID':>4}  {'Artist - Title':<30}  {'Orig':>5}  {'WX':>5}  "
        f"{'Match%':>6}  {'AvgScr':>6}  {'LevD':>5}  {'LowSc':>5}"
    )
    print(f"  {'-'*4}  {'-'*30}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*5}")

    total_orig = 0
    total_wx = 0
    total_matched = 0
    all_scores = []

    for r in results:
        label = _safe(f"{r['artist'][:15]} - {r['title'][:13]}")
        if len(label) > 30:
            label = label[:30]

        if r.get("error"):
            print(f"  {r['song_id']:>4}  {label:<30}  {'ERR':>5}  {'':>5}  {'':>6}  {'':>6}  {'':>5}  {'':>5}")
            continue

        wc = r.get("word_comparison", {})
        m = wc.get("metrics", {})

        orig = m.get("original_word_count", 0)
        wx = m.get("whisperx_word_count", 0)
        matched = m.get("matched_words", 0)
        rate = m.get("match_rate", 0)
        avg = m.get("avg_alignment_score", 0)
        lev = m.get("levenshtein_distance", 0)
        low = len(m.get("low_score_words", []))

        total_orig += orig
        total_wx += wx
        total_matched += matched
        all_scores.extend([s["score"] for s in wc.get("matches", [])])

        print(
            f"  {r['song_id']:>4}  {label:<30}  {orig:>5}  {wx:>5}  "
            f"{rate*100:>5.1f}%  {avg:>6.3f}  {lev:>5}  {low:>5}"
        )

    # Summary stats
    print(f"\n  {'-'*86}")
    overall_rate = total_matched / total_orig if total_orig > 0 else 0
    overall_avg = sum(all_scores) / len(all_scores) if all_scores else 0
    print(f"  TOTAL: {total_orig} original words, {total_wx} WhisperX words, {total_matched} matched")
    print(f"  Overall match rate: {overall_rate*100:.1f}%")
    print(f"  Overall avg alignment score: {overall_avg:.3f}")
    print(f"  Songs with errors: {sum(1 for r in results if r.get('error'))}")

    # Top unmatched words (potential lyrics source issues)
    all_unmatched = Counter()
    all_extra = Counter()
    for r in results:
        wc = r.get("word_comparison", {})
        for u in wc.get("unmatched_original", []):
            all_unmatched[u["word"].lower()] += 1
        for e in wc.get("extra_whisperx", []):
            all_extra[e["word"].lower()] += 1

    if all_unmatched:
        print(f"\n  TOP UNMATCHED ORIGINAL WORDS (lyrics not heard by WhisperX):")
        for word, count in all_unmatched.most_common(10):
            print(f"    {word:<25} {count:>3}x")

    if all_extra:
        print(f"\n  TOP EXTRA WHISPERX WORDS (heard but not in lyrics):")
        for word, count in all_extra.most_common(10):
            print(f"    {word:<25} {count:>3}x")

    print(f"{'='*90}\n")


def save_results(results: list):
    """Save per-song audit JSONs and summary."""
    os.makedirs(AUDIT_DIR, exist_ok=True)

    for r in results:
        path = os.path.join(AUDIT_DIR, f"{r['song_id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)

    # Aggregate summary
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "song_count": len(results),
        "songs": [],
    }
    for r in results:
        wc = r.get("word_comparison", {})
        m = wc.get("metrics", {})
        summary["songs"].append({
            "song_id": r["song_id"],
            "artist": r.get("artist", ""),
            "title": r.get("title", ""),
            "match_rate": m.get("match_rate", 0),
            "avg_alignment_score": m.get("avg_alignment_score", 0),
            "levenshtein_distance": m.get("levenshtein_distance", 0),
            "low_score_count": len(m.get("low_score_words", [])),
            "error": r.get("error"),
        })

    summary_path = os.path.join(AUDIT_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"Results saved to {AUDIT_DIR}/")
    print(f"  Per-song: {len(results)} JSON files")
    print(f"  Summary:  summary.json")


# ── CLI ─────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Audit lyrics alignment quality")
    parser.add_argument("--song-id", type=int, help="Audit a single song by ID")
    parser.add_argument("--limit", type=int, default=5, help="Max songs to audit (default: 5)")
    parser.add_argument("--sample", choices=["random", "first"], default="first",
                        help="Sampling method (default: first)")
    parser.add_argument("--worst", type=int, metavar="N",
                        help="Pick N songs with worst existing timing coverage")
    parser.add_argument("--compare-existing", action="store_true",
                        help="Also load and compare existing timing data")
    parser.add_argument("--skip-whisperx", action="store_true",
                        help="Skip WhisperX re-run, use existing timing data only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show selected songs without processing")
    args = parser.parse_args()

    songs = select_songs(args)
    if not songs:
        print("No songs selected for audit.")
        return

    print(f"Selected {len(songs)} songs for audit:")
    for s in songs:
        print(f"  {s['id']}: {_safe(s.get('artist', '?'))} - {_safe(s.get('title', '?'))}")

    if args.dry_run:
        return

    results = []
    for i, song in enumerate(songs, 1):
        print(f"\n[{i}/{len(songs)}] Auditing song {song['id']}: {_safe(song.get('artist', '?'))} - {_safe(song.get('title', '?'))}")
        result = audit_song(song, skip_whisperx=args.skip_whisperx, compare_existing=args.compare_existing)
        results.append(result)

    print_summary(results)
    save_results(results)


if __name__ == "__main__":
    main()
