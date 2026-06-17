"""Bulk re-extract audio features and re-normalize energy across the library.

Idempotent — safe to run multiple times.  Re-extracts all audio features
(including new v2 features), re-normalizes energy using the v2 formula,
recomputes recovery scores, and optionally re-embeds and re-clusters.

This is NOT part of the standard ingest pipeline phases — it's a one-shot
management script triggered by POST /api/admin/reprocess-energy.
"""

import logging
import time

from db import database
from ingest import audio
import config

logger = logging.getLogger(__name__)

# Reuse the ingest progress tracker from pipeline
from ingest.pipeline import _ingest_progress, _reset_progress


def reprocess_energy(reembed=False, recluster=False):
    """Run the full energy reprocess pipeline.

    Args:
        reembed: if True, re-embed all songs in ChromaDB
        recluster: if True, re-run KMeans clustering

    Returns:
        dict with counts of songs processed at each step
    """
    _reset_progress()
    _ingest_progress["running"] = True
    _ingest_progress["phase"] = "reprocess:analyze"

    logger.info("=== Reprocess Energy: Phase 1 — re-analyze audio features ===")

    songs = database.get_all_songs_filepaths()
    total = len(songs)
    _ingest_progress["total"] = total
    logger.info("Found %d songs to re-analyze", total)

    for i, song in enumerate(songs):
        sid = song["id"]
        fp = song["filepath"]
        _ingest_progress["current_song"] = f"{song.get('artist', '')} - {song.get('title', '')}" if song.get('artist') else song.get('filepath', '?')
        _ingest_progress["processed"] = i + 1

        try:
            result = audio.analyze_audio(fp)
            gain_db = audio.measure_loudness(fp)
            result["gain_db"] = gain_db
            database.update_song(sid, **result)
        except Exception as e:
            logger.warning("Failed to re-analyze song %d (%s): %s", sid, fp, e)

    logger.info("Reprocess Energy: Phase 2 — normalize v2 energy + v1 valence")
    _ingest_progress["phase"] = "reprocess:normalize"

    all_features = database.get_all_raw_features()
    if not all_features:
        logger.warning("No songs with raw features found")
        _ingest_progress["running"] = False
        return {"analyzed": 0, "normalized": 0}

    v2_updates = audio.normalize_all_songs_v2(all_features)
    for upd in v2_updates:
        database.update_song(upd["id"], energy=upd["energy"])

    v1_valence = audio.normalize_all_songs(all_features)
    for upd in v1_valence:
        database.update_song(upd["id"], valence=upd["valence"])

    logger.info("Reprocess Energy: Phase 3 — recompute recovery scores")
    _ingest_progress["phase"] = "reprocess:recovery"
    conn = database.get_connection()
    try:
        ids = conn.execute("SELECT id FROM songs").fetchall()
        all_ids = [r["id"] for r in ids]
    finally:
        conn.close()

    for sid in all_ids:
        database.calculate_recovery_score(sid)

    if reembed:
        logger.info("Reprocess Energy: Phase 4 — re-embed all songs in ChromaDB")
        _ingest_progress["phase"] = "reprocess:embed"
        from ingest import embedder
        embedder.embed_described_songs(all_ids)

    if recluster:
        logger.info("Reprocess Energy: Phase 5 — re-cluster all songs")
        _ingest_progress["phase"] = "reprocess:cluster"
        from ingest import clusterer
        clusterer.cluster_songs()

    logger.info("Reprocess Energy: Phase 6 — clear in-memory caches")
    _ingest_progress["phase"] = "reprocess:done"
    try:
        from engine import constellations
        man = constellations._get_constellation_manager()
        if man:
            man.clear_cache()
    except Exception:
        pass

    _ingest_progress["running"] = False
    result = {
        "analyzed": total,
        "normalized": len(v2_updates),
        "reembedded": total if reembed else 0,
        "reclustered": total if recluster else 0,
    }
    logger.info("=== Reprocess Energy: complete === %s", result)
    return result
