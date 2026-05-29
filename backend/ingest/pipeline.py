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


def run_normalize_only():
    """Run only the normalization pass on songs that have raw features but stale energy/valence."""
    _reset_progress()
    _ingest_progress["running"] = True
    _ingest_progress["phase"] = "normalizing"
    database.init_db()

    print("Normalizing energy/valence across library...")
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
        return

    print(f"  Found {len(songs)} songs with raw features")
    updates = audio.normalize_all_songs(songs)

    for upd in updates:
        database.update_song(upd["id"], energy=upd["energy"], valence=upd["valence"])

    print(f"  Normalized {len(updates)} songs")
    _ingest_progress["processed"] = len(updates)
    _ingest_progress["total"] = len(updates)
    _ingest_progress["running"] = False
    print("Normalization complete!")
    return updates


def run_ingest():
    """Run the full ingest pipeline: raw -> analyzed -> normalized -> described -> embedded -> clustered."""
    _reset_progress()
    _ingest_progress["running"] = True

    # Initialize database tables if they don't exist
    database.init_db()

    # Phase 1: Scan and add songs
    _ingest_progress["phase"] = "raw"
    print("Phase 1: Scanning music directory...")
    scan_result = scanner.scan_music_dir(config.MUSIC_DIR)
    print(f"  Found {scan_result['unique_files']} unique files, {scan_result['duplicates']} duplicates")

    new_ids = []
    for filepath in scan_result["duplicate_details"]:
        print(f"  Duplicate skipped: {filepath['file']} (kept: {filepath['kept']})")

    for filepath in scanner.find_audio_files(config.MUSIC_DIR):
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
    print(f"  {len(new_ids)} songs in database")

    # Phase 1b: Extract album art from ID3 tags
    print("Phase 1b: Extracting album art...")
    from ingest.tags import extract_album_art
    import config as cfg
    art_count = 0
    for song_id in new_ids:
        song = database.get_song(song_id)
        if song and song.get("filepath") and not song.get("art_file"):
            art_path = extract_album_art(song["filepath"], cfg.ART_DIR, song_id)
            if art_path:
                database.update_song(song_id, art_file=art_path)
                art_count += 1
    print(f"  Extracted album art for {art_count} songs")

    # Phase 2: Analyze audio — extract raw features
    _ingest_progress["phase"] = "analyzed"
    print("Phase 2: Analyzing audio (raw features)...")
    unprocessed = database.get_by_ingest_phase("raw")
    analyzed_ids = []
    total_to_analyze = len(unprocessed)
    for i, song in enumerate(tqdm(unprocessed, desc="Analyzing")):
        _ingest_progress["current_song"] = f"{song['artist']} - {song['title']}" if song.get('artist') else song['filepath']
        _ingest_progress["processed"] = i
        try:
            result = audio.analyze_audio(song["filepath"])
            database.update_song(song["id"], **result)
            database.set_ingest_phase(song["id"], "analyzed")
            analyzed_ids.append(song["id"])
        except Exception as e:
            print(f"  Failed to analyze {song['filepath']}: {e}")
            database.set_ingest_error(song["id"], f"analyze failed: {e}")

    # Phase 2b: Normalize energy/valence across the full library
    _ingest_progress["phase"] = "normalizing"
    print("Phase 2b: Normalizing energy/valence across library...")
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, raw_rms, crest_factor, spectral_contrast, spectral_rolloff, spectral_flatness "
            "FROM songs WHERE raw_rms IS NOT NULL"
        ).fetchall()
        all_songs_raw = [dict(r) for r in rows]
    finally:
        conn.close()

    if all_songs_raw:
        updates = audio.normalize_all_songs(all_songs_raw)
        for upd in updates:
            database.update_song(upd["id"], energy=upd["energy"], valence=upd["valence"])
        print(f"  Normalized {len(updates)} songs")
    else:
        print("  No songs with raw features to normalize")

    # Phase 3: Describe with LLM (acestep metadata + Ollama tags)
    _ingest_progress["phase"] = "described"
    print("Phase 3: Generating LLM tags...")

    # Load all acestep metadata ONCE (expensive JSON parsing)
    ace_metadata = metadata.load_all_metadata()
    print(f"  Loaded {len(ace_metadata)} acestep metadata entries")

    unprocessed = database.get_by_ingest_phase("analyzed")
    described_ids = []
    total_to_tag = len(unprocessed)
    for i, song in enumerate(tqdm(unprocessed, desc="Tagging")):
        _ingest_progress["current_song"] = f"{song['artist']} - {song['title']}" if song.get('artist') else song['filepath']
        _ingest_progress["processed"] = i
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

    # Phase 4: Embed + cluster
    _ingest_progress["phase"] = "embedded"
    print("Phase 4: Generating embeddings...")
    # Embed newly described songs
    if described_ids:
        from ingest import embedder
        embedded_ids = embedder.embed_described_songs(described_ids)
        print(f"  Embedded {len(embedded_ids)} songs")
    # Also embed any remaining songs at "described" phase that weren't just tagged
    remaining_described = database.get_by_ingest_phase("described")
    remaining_ids = [s["id"] for s in remaining_described]
    if remaining_ids:
        print(f"  Found {len(remaining_ids)} remaining songs at described phase, embedding...")
        from ingest import embedder
        embedded_remaining = embedder.embed_described_songs(remaining_ids)
        print(f"  Embedded {len(embedded_remaining)} remaining songs")
    # Phase 5: Clustering (re-cluster ALL songs, not just newly embedded)
    _ingest_progress["phase"] = "clustered"
    print("Phase 5: Clustering...")
    from ingest import clusterer
    clustered_ids = clusterer.cluster_songs()
    print(f"  Clustered {len(clustered_ids)} songs")
    described_ids = clustered_ids  # for progress reporting

    _ingest_progress["processed"] = _ingest_progress["total"]
    _ingest_progress["results"] = {
        "analyzed": len(analyzed_ids),
        "described": len(described_ids),
    }
    _ingest_progress["running"] = False

    print("Ingest complete!")
    return _ingest_progress["results"]
