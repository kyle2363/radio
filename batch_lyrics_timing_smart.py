"""Batch lyrics timing with vocals-first strategy.

Default mode (vocals_only=True) runs WhisperX only on the separated vocal
stem — faster and generally more accurate. Falls back to original audio if
vocal separation fails.

Legacy dual-transcribe mode (vocals_only=False) runs both vocals and
original, then picks whichever produces more word-level segments.

Usage:
    python batch_lyrics_timing_smart.py                        # All songs (vocals-only)
    python batch_lyrics_timing_smart.py --song-id 42           # Single song
    python batch_lyrics_timing_smart.py --limit 50             # First 50 songs
    python batch_lyrics_timing_smart.py --resume               # Skip already-timed
    python batch_lyrics_timing_smart.py --dry-run              # Preview only
    python batch_lyrics_timing_smart.py --force                # Re-process even if timed
    python batch_lyrics_timing_smart.py --dual                 # Compare both sources
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

from batch_lyrics_timing import (
    run_whisperx_pipeline,
    align_lyrics,
    _get_whisper_model,
    _get_align_model,
    load_timing,
    save_timing,
    _build_stored_word_index,
    should_skip_lyrics_alignment,
)


def _safe(s):
    return s.encode("ascii", "replace").decode("ascii")


def score_transcription(wx_words: list) -> dict:
    """Score a WhisperX transcription result for quality.

    Returns dict with word_count (primary metric) and avg_confidence (tiebreaker).
    """
    if not wx_words:
        return {"word_count": 0, "avg_confidence": 0.0}

    scores = [w.get("score", 0) for w in wx_words if w.get("score", 0) > 0]
    avg_conf = sum(scores) / len(scores) if scores else 0.0

    return {
        "word_count": len(wx_words),
        "avg_confidence": round(avg_conf, 4),
    }


def pick_best_source(result_vocals: list, result_original: list) -> tuple:
    """Compare two transcription results and pick the best one.

    Returns (winning_words, source_name, scores_dict).
    Primary metric: word count (more = better coverage).
    Tiebreaker: avg confidence (higher = more reliable).
    """
    sv = score_transcription(result_vocals)
    so = score_transcription(result_original)

    # Primary: word count
    if sv["word_count"] > so["word_count"]:
        winner = "vocals"
        winning_words = result_vocals
    elif so["word_count"] > sv["word_count"]:
        winner = "original"
        winning_words = result_original
    else:
        # Tiebreaker: avg confidence
        if sv["avg_confidence"] >= so["avg_confidence"]:
            winner = "vocals"
            winning_words = result_vocals
        else:
            winner = "original"
            winning_words = result_original

    return winning_words, winner, {"vocals": sv, "original": so}


def process_song(song_id: int, dry_run: bool = False, force: bool = False, vocals_only: bool = True) -> dict:
    """Process a single song: transcribe (vocals or both), align, save.

    When vocals_only=True (default), runs WhisperX only on the separated
    vocal stem. Falls back to original if separation fails.
    When vocals_only=False, runs both sources and picks the best.

    Returns status dict.
    """
    if not force:
        existing = load_timing(song_id)
        if existing and existing.get("lines") and any(l.get("start_ms") for l in existing["lines"]):
            return {"song_id": song_id, "status": "skipped", "message": "Already timed"}

    song = database.get_song(song_id)
    if not song:
        return {"song_id": song_id, "status": "skipped", "message": "Song not found in DB"}

    filepath = song.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        return {"song_id": song_id, "status": "skipped", "message": "Audio file not found"}

    lyrics_text = song.get("lyrics", "")
    skip, reason = should_skip_lyrics_alignment(lyrics_text)
    if skip:
        return {"song_id": song_id, "status": "skipped", "message": reason}

    duration_secs = song.get("duration_secs") or 0
    duration_ms = duration_secs * 1000

    if dry_run:
        skip, reason = should_skip_lyrics_alignment(lyrics_text)
        if skip:
            return {"song_id": song_id, "status": "dry_run_skip", "message": reason}
        return {"song_id": song_id, "status": "dry_run", "message": "Would process"}

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

    try:
        # Step 1: Separate vocals
        from engine.audio_separator import get_vocals_path
        vocals_path = get_vocals_path(filepath)
        has_vocals = vocals_path and os.path.exists(vocals_path)
    except Exception as e:
        has_vocals = False
        print(f"    Separation failed, will use original only: {e}")

    if vocals_only and has_vocals:
        # Step 2: Run WhisperX on vocals only (faster, generally better)
        print(f"    Transcribing vocals...", end=" ", flush=True)
        t0 = time.time()
        winning_words = run_whisperx_pipeline(vocals_path, language=language, separate_vocals=False)
        t_tot = time.time() - t0
        print(f"{len(winning_words)} words ({t_tot:.1f}s)")
        source = "vocals"
        scores = {"vocals": score_transcription(winning_words)}
    elif has_vocals and not vocals_only:
        # Step 2: Run WhisperX on original (always, for comparison)
        print(f"    Transcribing original...", end=" ", flush=True)
        t0 = time.time()
        result_original = run_whisperx_pipeline(filepath, language=language, separate_vocals=False)
        t_orig = time.time() - t0
        print(f"{len(result_original)} words ({t_orig:.1f}s)")

        # Step 3: Run WhisperX on vocals
        print(f"    Transcribing vocals...", end=" ", flush=True)
        t0 = time.time()
        result_vocals = run_whisperx_pipeline(vocals_path, language=language, separate_vocals=False)
        t_vocals = time.time() - t0
        print(f"{len(result_vocals)} words ({t_vocals:.1f}s)")

        # Step 4: Pick best source
        winning_words, source, scores = pick_best_source(result_vocals, result_original)
        print(f"    Winner: {source} (vocals={scores['vocals']['word_count']}w, "
              f"original={scores['original']['word_count']}w)")
    else:
        # Fallback: run on original audio
        print(f"    No vocals available, transcribing original...", end=" ", flush=True)
        t0 = time.time()
        winning_words = run_whisperx_pipeline(filepath, language=language, separate_vocals=False)
        t_tot = time.time() - t0
        print(f"{len(winning_words)} words ({t_tot:.1f}s)")
        source = "original"
        scores = {"original": score_transcription(winning_words)}

    # Step 5: Align against original lyrics
    timed_lines = align_lyrics(lyrics_text, winning_words, duration_ms)

    # Step 6: Save
    output = {
        "song_id": song_id,
        "source": f"whisperx_{source}",
        "language": language,
        "duration_ms": round(duration_ms),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lines": timed_lines,
        "scores": scores,
    }
    save_timing(song_id, output)

    timed_count = sum(1 for l in timed_lines if l.get("words") and len(l["words"]) > 0)
    return {"song_id": song_id, "status": "done",
            "message": f"Timed {len(timed_lines)} lines ({timed_count} with words), source={source}"}


def run_batch(song_ids: list[int], force: bool = False, vocals_only: bool = True):
    """Process a batch of songs with a progress bar."""
    results = {"done": 0, "skipped": 0, "failed": 0, "details": [],
               "source_counts": {"vocals": 0, "original": 0}}
    for sid in tqdm(song_ids, desc="Smart timing"):
        result = process_song(sid, force=force, vocals_only=vocals_only)
        results["details"].append(result)
        results[result["status"]] = results.get(result["status"], 0) + 1
        # Track which source was used
        msg = result.get("message", "")
        if "source=vocals" in msg:
            results["source_counts"]["vocals"] += 1
        elif "source=original" in msg:
            results["source_counts"]["original"] += 1
        tqdm.write(f"  [{result['status']}] Song {sid}: {result['message']}")
    return results


def main():
    parser = argparse.ArgumentParser(description="Smart lyrics timing (vocals vs original)")
    parser.add_argument("--song-id", type=int, help="Process only this song ID")
    parser.add_argument("--limit", type=int, help="Max songs to process")
    parser.add_argument("--resume", action="store_true", help="Skip already-timed songs")
    parser.add_argument("--force", action="store_true", help="Re-process even if already timed")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    parser.add_argument("--dual", action="store_true", help="Compare both vocals and original (default: vocals-only)")
    args = parser.parse_args()

    # Gather target songs
    target_ids = []
    if args.song_id:
        target_ids = [args.song_id]
    else:
        conn = database.get_connection()
        try:
            rows = conn.execute(
                "SELECT id FROM songs WHERE lyrics IS NOT NULL AND lyrics != '' "
                "AND lyrics != 'lyrics not found' AND lyrics != 'TODO'"
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

    vocals_only = not getattr(args, "dual", False)
    mode = "vocals-only" if vocals_only else "dual (vocals vs original)"
    print(f"Processing {len(target_ids)} songs ({mode})...")

    if args.dry_run:
        print("Would process:")
        for sid in target_ids[:20]:
            song = database.get_song(sid)
            title = f"{song.get('artist','?')} - {song.get('title','?')}" if song else "?"
            print(f"  {sid}: {title}")
        if len(target_ids) > 20:
            print(f"  ... and {len(target_ids) - 20} more")
        return

    results = run_batch(target_ids, force=args.force, vocals_only=vocals_only)
    done = results.get("done", 0)
    skipped = results.get("skipped", 0)
    failed = results.get("failed", 0)
    sc = results.get("source_counts", {})
    print(f"\nDone: {done} timed, {skipped} skipped, {failed} failed")
    print(f"Source breakdown: vocals={sc.get('vocals',0)}, original={sc.get('original',0)}")

    failures = [d for d in results.get("details", []) if d.get("status") == "failed"]
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  Song {f['song_id']}: {f['message']}")


if __name__ == "__main__":
    main()
