"""Orchestrate the full ingest pipeline.

Two-pass audio analysis:
  Pass 1: analyze each song for raw features (RMS, crest factor, spectral).
  Pass 2: normalize across the full library for true 0-1 energy/valence spread.
"""

import json
import os
import threading
from functools import wraps
from contextlib import contextmanager
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

_job_lock = threading.Lock()
_job_owner = None


def _finish_job():
    global _job_owner
    _ingest_progress["running"] = False
    _job_owner = None
    _job_lock.release()


def run_job(function):
    """Own admission independently of phase progress; allow nested phases."""
    global _job_owner
    if _job_owner == threading.get_ident():
        return function()
    if not _job_lock.acquire(blocking=False):
        return {"status": "already_running"}
    _job_owner = threading.get_ident()
    _reset_progress()
    try:
        return function()
    except Exception as exc:
        _ingest_progress["results"] = {"error": str(exc)}
        raise
    finally:
        _finish_job()


def exclusive_job(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return run_job(lambda: function(*args, **kwargs))
    return wrapped


def start_job(function):
    """Reserve synchronously, before the background worker can be queued."""
    global _job_owner
    if not _job_lock.acquire(blocking=False):
        return False
    _reset_progress()
    def worker():
        global _job_owner
        _job_owner = threading.get_ident()
        try:
            function()
        except Exception as exc:
            _ingest_progress["results"] = {"error": str(exc)}
        finally:
            _finish_job()
    try:
        threading.Thread(target=worker, daemon=True).start()
    except BaseException:
        _finish_job()
        raise
    return True


@contextmanager
def model_stage(name):
    """Use one managed model at a time; never eagerly restore all models."""
    from engine.process_manager import get_process_manager
    import sys
    muq = sys.modules.get("llm.muq_engine")
    if muq:
        muq.unload_model()
    pm = get_process_manager()
    for other in ("llama_chat", "llama_embed", "acestep"):
        proc = pm.get(other)
        if other != name and proc and proc.is_running:
            pm.stop(other)
    try:
        proc = pm.get(name) if name else None
        if proc and not pm.start(name):
            raise RuntimeError(f"Could not start {name}")
        yield
    finally:
        muq = sys.modules.get("llm.muq_engine")
        if muq:
            muq.unload_model()
        proc = pm.get(name) if name else None
        if proc and proc.is_running:
            pm.stop(name)


def apply_normalization_updates(updates):
    """Invalidate text vectors when normalized embedding inputs change."""
    for update in updates:
        song = database.get_song(update["id"])
        changed = song and any(song.get(k) != update[k] for k in ("energy", "valence") if k in update)
        database.update_song(update["id"], **{k: v for k, v in update.items() if k != "id"})
        if changed and song["ingest_phase"] in ("embedded", "clustered"):
            database.set_ingest_phase(update["id"], "described")


def _reset_progress():
    global _ingest_progress
    _ingest_progress.clear()
    _ingest_progress["running"] = _job_lock.locked()
    _ingest_progress["phase"] = None
    _ingest_progress["current_song"] = None
    _ingest_progress["total"] = 0
    _ingest_progress["processed"] = 0
    _ingest_progress["results"] = None


def get_ingest_progress():
    """Return current ingest progress for API polling."""
    return {**_ingest_progress, "running": _job_lock.locked()}


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
    added_count = 0
    for detail in scan_result.get("duplicate_details", []):
        print(f"  Duplicate skipped: {detail['file']} (kept: {detail['kept']})")

    # Deduplicate path variants; separate copies at different paths stay distinct.
    all_files = scanner.find_audio_files(config.MUSIC_DIR)
    dedup = scanner.deduplicate(all_files)
    kept_paths = set(dedup["unique"])

    for filepath in dedup["unique"]:
        file_hash = scanner.compute_file_hash(filepath)
        existing = database.get_song_by_filepath(filepath)
        if existing:
            new_ids.append(existing["id"])
        else:
            import os as _os
            file_mtime = _os.path.getmtime(filepath)
            inserted = database.add_song(filepath, file_hash, file_mtime=file_mtime)
            if not inserted:
                song = database.get_song_by_filepath(filepath)
                if song:
                    new_ids.append(song['id'])
                continue
            added_count += 1
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
    existing_count = len(new_ids) - added_count
    print(f"  {added_count} new songs added, {existing_count} already registered")

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
    return {"added": added_count, "existing": existing_count, "art_extracted": art_count}


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
            gain_db = audio.measure_loudness(song["filepath"])
            result["gain_db"] = gain_db
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
            "SELECT * FROM songs WHERE raw_rms IS NOT NULL"
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
    updates = audio.normalize_current_songs(songs)

    apply_normalization_updates(updates)

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
    # Legacy filename-only JSON cannot identify two distinct recordings.
    from collections import Counter
    stems = Counter(metadata.normalize_filename(os.path.splitext(os.path.basename(s["filepath"]))[0])
                    for s in database.get_all_songs_filepaths())
    described_ids = []
    total_to_tag = len(unprocessed)
    _ingest_progress["total"] = total_to_tag

    for i, song in enumerate(tqdm(unprocessed, desc="Tagging")):
        _ingest_progress["current_song"] = f"{song['artist']} - {song['title']}" if song.get('artist') else song['filepath']
        _ingest_progress["processed"] = i + 1
        try:
            # Load acestep metadata for this song
            ace_metadata_for_song = metadata.get_metadata_for_mp3(song["filepath"], ace_metadata)
            legacy_stem = metadata.normalize_filename(os.path.splitext(os.path.basename(song["filepath"]))[0])
            if stems[legacy_stem] > 1 and metadata.metadata_key(song["filepath"]) not in ace_metadata:
                raise ValueError("Ambiguous legacy metadata; run Understand for path-specific metadata")
            caption = ace_metadata_for_song.get("caption", "") if ace_metadata_for_song else ""

            # Always write acestep metadata to DB (independent of LLM tag success)
            db_tags = {}
            if ace_metadata_for_song:
                db_tags["ai_description"] = ace_metadata_for_song.get("caption")
                if ace_metadata_for_song.get("bpm"):
                    db_tags["bpm"] = ace_metadata_for_song["bpm"]
                # Playback duration belongs to the source file (tag scan), not
                # the analysis metadata, which may describe a trimmed excerpt.
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
            if db_tags:
                database.update_song(song["id"], **db_tags)
            tags = llm_tags.generate_tags(caption, lyrics=lyrics_for_tags)
            if not tags:
                raise ValueError("Tag generation returned no valid tags")
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


def _start_embed_server():
    """Start the text embedding LLM server if configured and not already running."""
    from engine.process_manager import get_process_manager
    pm = get_process_manager()
    embed = pm.get("llama_embed")
    if embed:
        ok = pm.start("llama_embed")
        if ok:
            print("  Text embedding server started")
        else:
            status = pm.get_status("llama_embed")
            if status["state"] == "running":
                print("  Text embedding server already running")
            else:
                print("  WARNING: text embedding server not available")
                return False
    return True


def _stop_embed_server():
    """Stop the text embedding LLM server to free VRAM."""
    from engine.process_manager import get_process_manager
    pm = get_process_manager()
    embed = pm.get("llama_embed")
    if embed and embed.is_running:
        pm.stop("llama_embed")
        print("  Text embedding server stopped")


def run_phase_embed():
    """Phase 4: Embed ALL songs at 'described' phase (text embeddings)."""
    _reset_progress()
    _ingest_progress["phase"] = "embedded"

    print("Phase 4: Generating text embeddings...")

    # Start the embedding server on-demand (not loaded at boot)
    _start_embed_server()

    from ingest import embedder

    # Embed ALL songs at described phase (not just new ones — in case model changed)
    described_songs = database.get_by_ingest_phase("described")
    described_ids = [s["id"] for s in described_songs]
    _ingest_progress["total"] = len(described_ids)

    if described_ids:
        print(f"  Found {len(described_ids)} songs at described phase")
        try:
            embedded_ids = embedder.embed_described_songs(described_ids)
            print(f"  Embedded {len(embedded_ids)} songs")
        except Exception as e:
            print(f"  Embedding failed: {e}")
            raise
        finally:
            # Free VRAM — stop embed server after ingest
            _stop_embed_server()
    else:
        print("  No songs at described phase")
        _stop_embed_server()

    _ingest_progress["running"] = False
    print("Phase 4 complete!")
    return {"embedded": len(embedded_ids) if described_ids else 0}


def run_phase_embed_audio():
    """Phase 4b: Generate MuQ-MuLan audio embeddings for all songs."""
    _reset_progress()
    _ingest_progress["phase"] = "audio_embedded"

    if not config.MUQ_ENABLED:
        print("Phase 4b: MuQ disabled, skipping audio embeddings")
        _ingest_progress["running"] = False
        return {"audio_embedded": 0}

    try:
        from llm.muq_engine import is_available
        if not is_available():
            print("Phase 4b: MuQ-MuLan not available (torch/muq not installed), skipping")
            _ingest_progress["running"] = False
            return {"audio_embedded": 0}
    except Exception as e:
        print(f"Phase 4b: MuQ-MuLan check failed: {e}, skipping")
        _ingest_progress["running"] = False
        return {"audio_embedded": 0}

    print("Phase 4b: Generating MuQ-MuLan audio embeddings...")
    from ingest import embedder

    # Get all songs that have been text-embedded (need filepath for audio)
    embedded_songs = database.get_by_ingest_phase("embedded")
    clustered_songs = database.get_by_ingest_phase("clustered")
    all_songs = embedded_songs + clustered_songs
    song_ids = [s["id"] for s in all_songs]
    _ingest_progress["total"] = len(song_ids)

    if song_ids:
        print(f"  Processing {len(song_ids)} songs for audio embeddings")
        embedded_ids = embedder.embed_audio_songs(song_ids)
        _ingest_progress["completed"] = len(embedded_ids)
        print(f"  Audio-embedded {len(embedded_ids)} songs")
    else:
        print("  No songs available for audio embedding")

    _ingest_progress["running"] = False
    print("Phase 4b complete!")
    return {"audio_embedded": len(embedded_ids) if song_ids else 0}


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


def run_phase_lyrics_timing():
    """Phase 6: Generate WhisperX lyrics timing for songs with lyrics."""
    _reset_progress()
    _ingest_progress["phase"] = "lyrics_timed"

    print("Phase 6: Generating lyrics timing (WhisperX forced alignment)...")

    import sys
    # Ensure project root is on path for batch_lyrics_timing imports
    if config.BASE_DIR not in sys.path:
        sys.path.insert(0, config.BASE_DIR)
    from batch_lyrics_timing_smart import process_song, should_skip_lyrics_alignment

    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, filepath, lyrics, duration_secs FROM songs "
            "WHERE lyrics IS NOT NULL AND lyrics != '' AND lyrics != 'lyrics not found' AND lyrics != 'TODO'"
        ).fetchall()
    finally:
        conn.close()

    targets = []
    for r in rows:
        sid = r["id"]
        lyrics_text = r["lyrics"] or ""
        skip, reason = should_skip_lyrics_alignment(lyrics_text)
        if skip:
            continue
        timing_path = os.path.join(config.LYRICS_TIMING_DIR, f"{sid}.json")
        if not os.path.exists(timing_path):
            targets.append(dict(r))

    _ingest_progress["total"] = len(targets)
    print(f"  Found {len(targets)} songs needing timing data")

    if not targets:
        _ingest_progress["running"] = False
        return {"lyrics_timed": 0}

    done = 0
    failed = 0
    for i, song in enumerate(tqdm(targets, desc="Timing lyrics")):
        _ingest_progress["current_song"] = f"{song.get('artist', '?')} - {song.get('title', '?')}"
        _ingest_progress["processed"] = i + 1
        try:
            result = process_song(song["id"], vocals_only=True)
            if result.get("status") == "done":
                done += 1
            else:
                failed += 1
                if failed <= 5:
                    print(f"    Failed: Song {song['id']} - {result.get('message', '')}")
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"    Error: Song {song['id']} - {e}")

    _ingest_progress["running"] = False
    print(f"Phase 6 complete! Timed: {done}, Failed: {failed}")
    return {"lyrics_timed": done, "failed": failed}


# ──────────────────────────────────────────────────────────
# Phase 0: Understand — process new MP3s through acestep.cpp
# ──────────────────────────────────────────────────────────


def _find_unprocessed_mp3s():
    """Return MP3 filepaths that don't have a matching acestep JSON."""
    ace_index = metadata.build_json_index(config.ACESTEP_JSON_DIR)
    all_files = scanner.find_audio_files(config.MUSIC_DIR)
    dedup = scanner.deduplicate(all_files)
    from collections import Counter
    counts = Counter(metadata.normalize_filename(os.path.splitext(os.path.basename(fp))[0]) for fp in dedup["unique"])
    unprocessed = []
    for fp in dedup["unique"]:
        stem = os.path.splitext(os.path.basename(fp))[0]
        normalized = metadata.metadata_key(fp)
        legacy = metadata.normalize_filename(stem)
        same_stem = counts[legacy]
        if normalized not in ace_index and not (same_stem == 1 and legacy in ace_index):
            unprocessed.append(fp)
    return unprocessed


def run_phase_understand():
    """Phase 0: Submit unprocessed MP3s to acestep.cpp for understanding.

    Manual trigger only — not part of the auto pipeline.
    Starts acestep, processes files sequentially, saves JSONs, stops acestep.
    """
    _reset_progress()
    _ingest_progress["phase"] = "understand"

    if not config.ACESTEP_EXE or not os.path.isfile(config.ACESTEP_EXE):
        print("Phase 0: acestep not configured (ACESTEP_EXE not found)")
        _ingest_progress["running"] = False
        return {"error": "acestep not configured"}

    # Find MP3s without acestep JSONs
    unprocessed = _find_unprocessed_mp3s()
    if not unprocessed:
        print("Phase 0: All MP3s already have acestep JSONs — nothing to do")
        _ingest_progress["running"] = False
        return {"understand": 0, "skipped": 0, "failed": 0}

    print(f"Phase 0: Found {len(unprocessed)} MP3s needing understanding")
    print(f"  First: {os.path.basename(unprocessed[0])}")
    print(f"  Last:  {os.path.basename(unprocessed[-1])}")

    # Start acestep via process manager
    from engine.process_manager import get_process_manager
    pm = get_process_manager()
    ace = pm.get("acestep")
    if ace and ace.state == "stopped":
        print("  Starting acestep server...")
        ok = pm.start("acestep")
        if not ok:
            # Maybe it's already running on the port — check TCP
            import socket as _socket
            try:
                sock = _socket.create_connection(("127.0.0.1", config.ACESTEP_PORT), timeout=3)
                sock.close()
                print("  acestep already running on port", config.ACESTEP_PORT)
            except OSError:
                print("  Failed to start acestep, aborting")
                _ingest_progress["running"] = False
                return {"error": "failed to start acestep"}
    elif ace and ace.state == "running":
        print("  acestep already running")

    server_url = f"http://127.0.0.1:{config.ACESTEP_PORT}"

    import httpx
    import json
    import time

    succeeded = 0
    failed = 0
    total = len(unprocessed)
    _ingest_progress["total"] = total

    for i, filepath in enumerate(unprocessed):
        _ingest_progress["current_song"] = os.path.basename(filepath)
        _ingest_progress["processed"] = i + 1

        try:
            # Submit
            with open(filepath, "rb") as f:
                resp = httpx.post(
                    f"{server_url}/understand",
                    files={"audio": (os.path.basename(filepath), f, "audio/mpeg")},
                    timeout=60,
                )
            if resp.status_code != 200:
                print(f"  [{i+1}/{total}] Submit failed ({resp.status_code}): {os.path.basename(filepath)}")
                failed += 1
                continue

            job_data = resp.json()
            job_id = job_data.get("id")
            if not job_id:
                print(f"  [{i+1}/{total}] No job ID returned: {os.path.basename(filepath)}")
                failed += 1
                continue

            # Poll until done
            deadline = time.monotonic() + 1800
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("Understanding job exceeded 30 minutes; retry this phase")
                poll_resp = httpx.get(f"{server_url}/job?id={job_id}", timeout=10)
                if poll_resp.status_code != 200:
                    break
                text = poll_resp.text.strip()
                if not text:
                    time.sleep(0.5)
                    continue
                poll_data = poll_resp.json()
                status = poll_data.get("status")
                if status == "done":
                    break
                if status in ("failed", "cancelled"):
                    raise Exception(f"Job {status}")
                time.sleep(0.5)

            # Fetch result
            result_resp = httpx.get(f"{server_url}/job?id={job_id}&result=1", timeout=30)
            if result_resp.status_code != 200:
                print(f"  [{i+1}/{total}] Fetch result failed: {os.path.basename(filepath)}")
                failed += 1
                continue

            # Parse multipart response
            content_type = result_resp.headers.get("Content-Type", "")
            boundary = ""
            if "boundary=" in content_type:
                boundary = content_type.split("boundary=", 1)[1].strip()
            body = result_resp.content
            boundary_marker = f"--{boundary}".encode()
            parts = body.split(boundary_marker)
            json_part = None
            for part in parts:
                if not part.strip():
                    continue
                idx = part.find(b"\r\n\r\n")
                if idx >= 0:
                    candidate = part[idx + 4:].strip()
                    if candidate and not candidate.startswith(b"--"):
                        json_part = candidate
                        break
            if not json_part:
                print(f"  [{i+1}/{total}] Could not parse result: {os.path.basename(filepath)}")
                failed += 1
                continue

            result_data = json.loads(json_part.decode("utf-8"))
            if isinstance(result_data, list):
                for item in result_data:
                    item.pop("audio_codes", None)
                    if not item.get("lyrics"):
                        item["lyrics"] = "TODO"
                    if not item.get("vocal_language"):
                        item["vocal_language"] = "en"
            else:
                result_data = [result_data]

            # Save JSON
            stem = os.path.splitext(os.path.basename(filepath))[0]
            os.makedirs(config.ACESTEP_JSON_DIR, exist_ok=True)
            out_path = metadata.metadata_path(filepath)
            temporary = out_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(result_data, f, indent=2, ensure_ascii=False)
            os.replace(temporary, out_path)

            succeeded += 1
            print(f"  [{i+1}/{total}] OK: {stem}")

        except Exception as e:
            failed += 1
            print(f"  [{i+1}/{total}] FAILED: {os.path.basename(filepath)} — {e}")

    # Stop acestep
    print("  Stopping acestep server...")
    if ace:
        pm.stop("acestep")

    _ingest_progress["running"] = False
    print(f"Phase 0 complete! Success: {succeeded}, Failed: {failed}")
    return {"understand": succeeded, "skipped": total - succeeded - failed, "failed": failed}


# ──────────────────────────────────────────────────────────
# Full pipeline (calls phases sequentially)
# ──────────────────────────────────────────────────────────

def run_ingest():
    """Run the full ingest pipeline: raw -> analyzed -> normalized -> described -> embedded -> clustered -> lyrics_timed."""
    phases = [
        ("raw", run_phase_raw),
        ("analyzed", run_phase_analyze),
        ("normalizing", run_phase_normalize),
        ("described", run_phase_describe),
        ("embedded", run_phase_embed),
        ("audio_embedded", run_phase_embed_audio),
        ("clustered", run_phase_cluster),
        ("lyrics_timed", run_phase_lyrics_timing),
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


def _managed_phase(function, model):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with model_stage(model):
            return function(*args, **kwargs)
    return wrapped


# Entry points share one admission boundary, including direct CLI callers.
for _phase in ("raw", "analyze", "normalize", "describe", "embed", "embed_audio", "cluster", "lyrics_timing", "understand"):
    _name = "run_phase_" + _phase
    _function = globals()[_name]
    if _phase in ("describe", "embed", "understand", "embed_audio", "lyrics_timing"):
        _model = {"describe": "llama_chat", "embed": "llama_embed", "understand": "acestep"}.get(_phase)
        _function = _managed_phase(_function, _model)
    globals()[_name] = exclusive_job(_function)
run_ingest = exclusive_job(run_ingest)
