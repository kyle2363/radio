"""Orchestrate the full ingest pipeline.

Two-pass audio analysis:
  Pass 1: analyze each song for raw features (RMS, crest factor, spectral).
  Pass 2: normalize across the full library for true 0-1 energy/valence spread.
"""

import json
import os
from tqdm import tqdm

from db import database
from ingest import scanner, metadata, audio, llm_tags
import config

# Ingest progress tracking (shared with API)
_ingest_progress = {
    "running": False,
    "phase": None,
    "current_song": None,
    "total": 0,
    "processed": 0,
    "results": None,
}


def _reset_progress():
    global _ingest_progress
    _ingest_progress = {
        "running": False,
        "phase": None,
        "current_song": None,
        "total": 0,
        "processed": 0,
        "results": None,
    }


def get_ingest_progress():
    """Return current ingest progress for API polling."""
    return dict(_ingest_progress)


# ──────────────────────────────────────────────────────────
# Standalone phase functions (for step-by-step execution)
# ──────────────────────────────────────────────────────────

def run_phase_raw():
    """Phase 1: Scan music dir, add new songs to DB, extract album art."""
    _reset_progress()
    _ingest_progress["phase"] = "raw"

    print("Phase 1: Scanning music directory...")
    scan_result = scanner.scan_music_dir(config.MUSIC_DIR)
    print(f"  Found {scan_result['unique_files']} unique files, {scan_result['duplicates']} duplicates")

    new_ids = []
    for detail in scan_result.get("duplicate_details", []):
        print(f"  Duplicate skipped: {detail['file']} (kept: {detail['kept']})")

    # Use deduplicated file list — prevents adding the same file twice
    # when it exists in multiple folders with different paths
    all_files = scanner.find_audio_files(config.MUSIC_DIR)
    dedup = scanner.deduplicate(all_files)
    kept_paths = set(dedup["unique"])

    for filepath in dedup["unique"]:
        file_hash = scanner.compute_file_hash(filepath)
        existing = database.get_song_by_filepath(filepath)
        if existing:
            new_ids.append(existing["id"])
        else:
            database.add_song(filepath, file_hash)
            parsed = scanner.parse_filename(filepath)
            update = {"filepath": filepath}
            if parsed.get("artist"):
                update["artist"] = parsed["artist"]
            if parsed.get("title"):
                update["title"] = parsed["title"]
            song = database.get_song_by_filepath(filepath)
            if song:
                database.update_song(song["id"], **update)
                new_ids.append(song["id"])

    _ingest_progress["total"] = len(new_ids)
    _ingest_progress["processed"] = len(new_ids)
    print(f"  {len(new_ids)} songs in database")

    # Phase 1b: Extract album art from ID3 tags
    print("Phase 1b: Extracting album art...")
    from ingest.tags import extract_album_art
    art_count = 0
    for song_id in new_ids:
        song = database.get_song(song_id)
        if song and song.get("filepath") and not song.get("art_file"):
            art_path = extract_album_art(song["filepath"], config.ART_DIR, song_id)
            if art_path:
                database.update_song(song_id, art_file=art_path)
                art_count += 1
    print(f"  Extracted album art for {art_count} songs")

    _ingest_progress["running"] = False
    print("Phase 1 complete!")
    return {"added": len(new_ids), "art_extracted": art_count}


def run_phase_analyze():
    """Phase 2: Analyze audio — extract raw features for 'raw' songs."""
    _reset_progress()
    _ingest_progress["phase"] = "analyzed"

    print("Phase 2: Analyzing audio (raw features)...")
    unprocessed = database.get_by_ingest_phase("raw")
    analyzed_ids = []
    total_to_analyze = len(unprocessed)
    _ingest_progress["total"] = total_to_analyze

    for i, song in enumerate(tqdm(unprocessed, desc="Analyzing")):
        _ingest_progress["current_song"] = f"{song['artist']} - {song['title']}" if song.get('artist') else song['filepath']
        _ingest_progress["processed"] = i + 1
        try:
            result = audio.analyze_audio(song["filepath"])
            database.update_song(song["id"], **result)
            database.set_ingest_phase(song["id"], "analyzed")
            analyzed_ids.append(song["id"])
        except Exception as e:
            print(f"  Failed to analyze {song['filepath']}: {e}")
            database.set_ingest_error(song["id"], f"analyze failed: {e}")

    _ingest_progress["running"] = False
    print(f"Phase 2 complete! Analyzed {len(analyzed_ids)} songs.")
    return {"analyzed": len(analyzed_ids)}


def run_phase_normalize():
    """Phase 2b: Normalize energy/valence across the full library."""
    _reset_progress()
    _ingest_progress["phase"] = "normalizing"

    print("Phase 2b: Normalizing energy/valence across library...")
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, raw_rms, crest_factor, spectral_contrast, spectral_rolloff, spectral_flatness "
            "FROM songs WHERE raw_rms IS NOT NULL"
        ).fetchall()
        songs = [dict(r) for r in rows]
    finally:
        conn.close()

    if not songs:
        print("  No songs with raw features found.")
        _ingest_progress["running"] = False
        return {"normalized": 0}

    _ingest_progress["total"] = len(songs)
    _ingest_progress["processed"] = len(songs)
    updates = audio.normalize_all_songs(songs)

    for upd in updates:
        database.update_song(upd["id"], energy=upd["energy"], valence=upd["valence"])

    print(f"  Normalized {len(updates)} songs")
    _ingest_progress["running"] = False
    print("Phase 2b complete!")
    return {"normalized": len(updates)}


def run_phase_describe():
    """Phase 3: LLM tag 'analyzed' songs using acestep captions."""
    _reset_progress()
    _ingest_progress["phase"] = "described"

    print("Phase 3: Generating LLM tags...")

    # Load all acestep metadata ONCE
    ace_metadata = metadata.load_all_metadata()
    print(f"  Loaded {len(ace_metadata)} acestep metadata entries")

    unprocessed = database.get_by_ingest_phase("analyzed")
    described_ids = []
    total_to_tag = len(unprocessed)
    _ingest_progress["total"] = total_to_tag

    for i, song in enumerate(tqdm(unprocessed, desc="Tagging")):
        _ingest_progress["current_song"] = f"{song['artist']} - {song['title']}" if song.get('artist') else song['filepath']
        _ingest_progress["processed"] = i + 1
        try:
            # Load acestep metadata for this song
            ace_metadata_for_song = metadata.get_metadata_for_mp3(song["filepath"], ace_metadata)
            caption = ace_metadata_for_song.get("caption", "") if ace_metadata_for_song else ""

            # Always write acestep metadata to DB (independent of LLM tag success)
            db_tags = {}
            if ace_metadata_for_song:
                db_tags["ai_description"] = ace_metadata_for_song.get("caption")
                if ace_metadata_for_song.get("bpm"):
                    db_tags["bpm"] = ace_metadata_for_song["bpm"]
                if ace_metadata_for_song.get("duration"):
                    db_tags["duration_secs"] = ace_metadata_for_song["duration"]
                if ace_metadata_for_song.get("lyrics"):
                    db_tags["lyrics"] = ace_metadata_for_song["lyrics"]
                if ace_metadata_for_song.get("keyscale"):
                    db_tags["keyscale"] = ace_metadata_for_song["keyscale"]
                if ace_metadata_for_song.get("timesignature"):
                    db_tags["timesignature"] = ace_metadata_for_song["timesignature"]
                if ace_metadata_for_song.get("vocal_language"):
                    db_tags["vocal_language"] = ace_metadata_for_song["vocal_language"]
                if "dj_effect_eligible" in ace_metadata_for_song and ace_metadata_for_song["dj_effect_eligible"] is not None:
                    db_tags["dj_effect_eligible"] = 1 if ace_metadata_for_song["dj_effect_eligible"] else 0

            # Generate LLM tags from caption AND lyrics for richer analysis
            lyrics_for_tags = ace_metadata_for_song.get("lyrics", "") if ace_metadata_for_song else ""
            tags = llm_tags.generate_tags(caption, lyrics=lyrics_for_tags)
            if tags:
                db_tags["emotional_tags"] = json.dumps(tags["emotional_tags"])
                db_tags["genre_tags"] = json.dumps(tags["genre_tags"])
                db_tags["instrument_tags"] = json.dumps(tags["instrument_tags"])

            if db_tags:
                database.update_song(song["id"], **db_tags)

            database.set_ingest_phase(song["id"], "described")
            described_ids.append(song["id"])
        except Exception as e:
            print(f"  Failed to tag {song['filepath']}: {e}")
            database.set_ingest_error(song["id"], f"tagging failed: {e}")

    _ingest_progress["running"] = False
    print(f"Phase 3 complete! Tagged {len(described_ids)} songs.")
    return {"described": len(described_ids)}


def run_phase_embed():
    """Phase 4: Embed ALL songs at 'described' phase."""
    _reset_progress()
    _ingest_progress["phase"] = "embedded"

    print("Phase 4: Generating embeddings...")
    from ingest import embedder

    # Embed ALL songs at described phase (not just new ones — in case model changed)
    described_songs = database.get_by_ingest_phase("described")
    described_ids = [s["id"] for s in described_songs]
    _ingest_progress["total"] = len(described_ids)

    if described_ids:
        print(f"  Found {len(described_ids)} songs at described phase")
        embedded_ids = embedder.embed_described_songs(described_ids)
        print(f"  Embedded {len(embedded_ids)} songs")
    else:
        print("  No songs at described phase")

    _ingest_progress["running"] = False
    print("Phase 4 complete!")
    return {"embedded": _ingest_progress["total"]}


def run_phase_cluster():
    """Phase 5: Re-cluster ALL embedded songs."""
    _reset_progress()
    _ingest_progress["phase"] = "clustered"

    print("Phase 5: Clustering...")
    from ingest import clusterer

    # Count embedded songs for progress
    embedded_songs = database.get_by_ingest_phase("embedded")
    _ingest_progress["total"] = len(embedded_songs)

    clustered_ids = clusterer.cluster_songs()
    print(f"  Clustered {len(clustered_ids)} songs")

    _ingest_progress["processed"] = _ingest_progress["total"]
    _ingest_progress["running"] = False
    print("Phase 5 complete!")
    return {"clustered": len(clustered_ids)}


# ──────────────────────────────────────────────────────────
# Full pipeline (calls phases sequentially)
# ──────────────────────────────────────────────────────────

def run_ingest():
    """Run the full ingest pipeline: raw -> analyzed -> normalized -> described -> embedded -> clustered."""
    phases = [
        ("raw", run_phase_raw),
        ("analyzed", run_phase_analyze),
        ("normalizing", run_phase_normalize),
        ("described", run_phase_describe),
        ("embedded", run_phase_embed),
        ("clustered", run_phase_cluster),
    ]

    _ingest_progress["running"] = True
    results = {}
    for phase_name, phase_fn in phases:
        _ingest_progress["phase"] = phase_name
        try:
            result = phase_fn()
            results[phase_name] = result
        except Exception as e:
            print(f"Phase '{phase_name}' failed: {e}")
            results[phase_name] = {"error": str(e)}
            break

    _ingest_progress["running"] = False
    _ingest_progress["results"] = results
    return results
