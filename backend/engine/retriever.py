"""ChromaDB vector retrieval for song recommendations.

Supports dual embedding paths:
  - Text embeddings (LLM provider) — metadata/caption/lyrics similarity
  - Audio embeddings (MuQ-MuLan) — musical similarity

Both searches run in parallel and scores are blended using MUQ_AUDIO_BLEND.
"""

import json
import logging
import math
import random
from datetime import datetime, timedelta
import config
from db.chroma import get_chroma_collection, get_chroma_client
from db import database
from engine.utils import cosine_similarity

logger = logging.getLogger(__name__)


def get_embedding_dimension(collection_name: str = "songs") -> int:
    """Get the embedding dimension from a ChromaDB collection."""
    try:
        collection = get_chroma_collection(collection_name)
        sample = collection.get(limit=1, include=["embeddings"])
        if sample and sample.get("embeddings") is not None:
            emb = sample["embeddings"][0]
            if emb is not None and hasattr(emb, "__len__"):
                return len(emb)
    except Exception:
        pass
    return 1024


def get_audio_embedding_dimension() -> int:
    """Get the embedding dimension from the audio collection."""
    if not config.MUQ_ENABLED:
        return 0
    return get_embedding_dimension(config.MUQ_AUDIO_COLLECTION)


def _jitter_vector(v, strength=0.12):
    """Add random noise to a vector for variety between calls.

    Default strength 0.12. Configurable via 'jitter_strength' setting
    (0.0 = old behavior 0.03, 1.0 = 0.16 max).
    """
    # Map 0-1 setting range to 0.03-0.16 actual strength
    actual = 0.03 + strength * 0.13
    noise = [random.gauss(0, actual) for _ in v]
    noise_mag = math.sqrt(sum(x * x for x in noise))
    if noise_mag > 0:
        noise = [x / noise_mag * actual for x in noise]
    return [a + b for a, b in zip(v, noise)]


def search_by_vector(taste_vector, n=60, exclude_ids=None,
                     min_energy=None, max_energy=None,
                     min_valence=None, max_valence=None,
                     subfolder_filter=None,
                     mtime_min=None, mtime_max=None,
                     artist_mode=None, artists=None,
                     collection_name="songs"):
    """Search ChromaDB by embedding vector with optional filters.

    When subfolder_filter is set (list of folder names), only songs from
    those subfolders are returned. ChromaDB handles the filtering natively
    within the HNSW search, so every result is already restricted.

    Returns list of dicts with song_id, score, metadata.
    """
    if exclude_ids is None:
        exclude_ids = []

    collection = get_chroma_collection(collection_name)
    jitter_override = database._get_cached_setting("jitter_override")
    if jitter_override:
        jitter_setting = float(jitter_override)
    else:
        exploration = float(database._get_cached_setting("exploration_level") or "0.3")
        jitter_setting = 0.03 + exploration * 0.13
    query_vector = _jitter_vector(taste_vector, strength=jitter_setting)

    where_clause = None
    if subfolder_filter:
        where_clause = {"subfolder": {"$in": subfolder_filter}}

    inflated_n = min(max(n * 2, 120), 200)
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=inflated_n,
        where=where_clause,
        include=["distances", "metadatas"],
    )

    song_ids = []
    ids_row = results["ids"][0] if results and results.get("ids") else []
    for i in range(len(ids_row)):
        song_id = int(ids_row[i])
        if song_id in exclude_ids:
            continue
        song_ids.append(song_id)

    # Fetch artist/title from SQLite
    song_info = {}
    if song_ids:
        conn = database.get_connection()
        try:
            placeholders = ",".join("?" for _ in song_ids)
            rows = conn.execute(
                f"SELECT id, artist, title FROM songs WHERE id IN ({placeholders})",
                song_ids,
            ).fetchall()
            song_info = {r["id"]: {"artist": r["artist"] or "", "title": r["title"] or ""} for r in rows}
        finally:
            conn.close()

    # Fetch effective energies (blends base + user_energy into one score)
    effective_map = {}
    if song_ids:
        try:
            effective_map = database.get_effective_energy_batch(song_ids)
        except Exception:
            pass

    candidates = []
    for i in range(len(ids_row)):
        song_id = int(ids_row[i])
        if song_id in exclude_ids:
            continue

        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        score = 1.0 - distance

        energy = effective_map.get(song_id, float(meta.get("energy", 0.5)))
        valence = float(meta.get("valence", 0.5))

        if min_energy is not None and energy < min_energy:
            continue
        if max_energy is not None and energy > max_energy:
            continue
        if min_valence is not None and valence < min_valence:
            continue
        if max_valence is not None and valence > max_valence:
            continue

        info = song_info.get(song_id, {})
        candidates.append({
            "song_id": song_id,
            "score": score,
            "energy": energy,
            "valence": valence,
            "cluster_id": int(meta.get("cluster_id", -1)),
            "artist": info.get("artist", meta.get("artist", "")),
            "title": info.get("title", meta.get("title", "")),
        })

    # Apply mtime range post-filter (mtime not in ChromaDB metadata yet)
    if mtime_min is not None or mtime_max is not None:
        candidate_ids = [c["song_id"] for c in candidates]
        if candidate_ids:
            conn = database.get_connection()
            try:
                placeholders = ",".join("?" * len(candidate_ids))
                rows = conn.execute(
                    f"SELECT id, file_mtime FROM songs WHERE id IN ({placeholders})",
                    candidate_ids,
                ).fetchall()
                mtime_map = {r["id"]: r["file_mtime"] for r in rows}
            finally:
                conn.close()

            # Fallback: synthetic mtime from subfolder rank for songs without file_mtime
            if any(mtime_map.get(sid) is None for sid in candidate_ids):
                all_subfolders = database.get_subfolders()
                folder_rank = {name: i for i, name in enumerate(all_subfolders)}
                # Derive min/max mtime from known mtime range
                known_mtimes = [m for m in mtime_map.values() if m is not None]
                min_known = min(known_mtimes) if known_mtimes else 0
                max_known = max(known_mtimes) if known_mtimes else 1
                # Normalize: rank 0 -> min_known, rank N -> max_known
                if len(all_subfolders) > 1 and max_known > min_known:
                    for sid, c in zip(candidate_ids, candidates):
                        if mtime_map.get(sid) is None:
                            song_row = database.get_song(sid)
                            if song_row:
                                subf = song_row.get("subfolder", "")
                                rank = folder_rank.get(subf, 0)
                                frac = rank / (len(all_subfolders) - 1)
                                mtime_map[sid] = min_known + frac * (max_known - min_known)

            filtered = []
            now_ts = datetime.now().timestamp()
            for c in candidates:
                m = mtime_map.get(c["song_id"])
                if m is None:
                    continue
                song_age_days = (now_ts - m) / 86400.0
                if mtime_min is not None and song_age_days < mtime_min:
                    continue
                if mtime_max is not None and song_age_days > mtime_max:
                    continue
                filtered.append(c)
            candidates = filtered

    # Apply artist mode filter (post-filter on artist field)
    if artist_mode and artists:
        lower_artists = [a.lower() for a in artists if a.strip()]
        if lower_artists:
            if artist_mode == "include":
                candidates = [c for c in candidates if any(a in c.get("artist", "").lower() for a in lower_artists)]
            elif artist_mode == "exclude":
                candidates = [c for c in candidates if not any(a in c.get("artist", "").lower() for a in lower_artists)]

    # Apply cluster staleness bonus
    staleness = database.get_cluster_staleness()
    if staleness:
        for c in candidates:
            cid = c.get("cluster_id", -1)
            hours_ago = staleness.get(cid, 24.0)
            if hours_ago > 6 and cid >= 0:
                boost = min(0.08, hours_ago * 0.005)
                c["score"] = min(1.0, c.get("score", 0.5) + boost)

    if len(candidates) > n + 40:
        pool = candidates[:n + 40]
        random.shuffle(pool)
        candidates = pool[:n]
    else:
        candidates = candidates[:n]
    return candidates


def _merge_duo_candidates(text_cands, audio_cands, blend):
    """Merge candidates from text and audio searches with score blending.

    For songs appearing in both result sets, blend scores.
    For songs in only one set, use that score directly.
    """
    text_map = {c["song_id"]: c for c in text_cands}
    audio_map = {c["song_id"]: c for c in audio_cands}
    all_ids = set(text_map) | set(audio_map)

    merged = []
    for sid in all_ids:
        tc = text_map.get(sid)
        ac = audio_map.get(sid)

        if tc and ac:
            # Song in both sets — blend scores
            ts = tc["score"]
            as_ = ac["score"]
            blended_score = (1.0 - blend) * ts + blend * as_
            # Prefer text metadata (usually richer)
            merged.append({
                "song_id": sid,
                "score": blended_score,
                "energy": tc.get("energy", ac.get("energy", 0.5)),
                "valence": tc.get("valence", ac.get("valence", 0.5)),
                "cluster_id": tc.get("cluster_id", ac.get("cluster_id", -1)),
                "artist": tc.get("artist") or ac.get("artist", ""),
                "title": tc.get("title") or ac.get("title", ""),
                "source": "blended",
            })
        elif tc:
            # Text only — apply blend as discount
            merged.append({
                **tc,
                "score": tc["score"] * (1.0 - blend * 0.3),
                "source": "text",
            })
        elif ac:
            # Audio only — apply blend as discount
            merged.append({
                **ac,
                "score": ac["score"] * (1.0 - (1.0 - blend) * 0.3),
                "source": "audio",
            })

    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged


def search_by_centroid(centroid_energy=0.5, centroid_valence=0.5, n=60):
    """Get songs near a neutral centroid (energy/valence based).

    Used for cold start when no taste vector exists.
    """
    collection = get_chroma_collection()
    results = collection.get(
        where={"$and": [
            {"energy": {"$gte": centroid_energy - 0.2}},
            {"energy": {"$lte": centroid_energy + 0.2}},
            {"valence": {"$gte": centroid_valence - 0.2}},
            {"valence": {"$lte": centroid_valence + 0.2}},
        ]},
        limit=min(n, 200),
        include=["metadatas", "ids"],
    )

    candidates = []
    for i in range(len(results["ids"])):
        meta = results["metadatas"][i]
        candidates.append({
            "song_id": int(meta.get("song_id", 0)),
            "score": 0.5,
            "energy": float(meta.get("energy", 0.5)),
            "valence": float(meta.get("valence", 0.5)),
            "cluster_id": int(meta.get("cluster_id", -1)),
            "artist": meta.get("artist", ""),
            "title": meta.get("title", ""),
        })

    return candidates[:n]


def get_neutral_centroid_songs(n=60):
    """Get songs near the neutral centroid (energy~0.5, valence~0.5).

    Pre-computed at startup for cold start.
    """
    return database.get_neutral_centroid_songs(n)


def _apply_skip_feedback_bias(mood_energy_range, mood_valence_range, skip_feedback=None):
    """Adjust energy/valence ranges based on recent skip feedback."""
    if not skip_feedback:
        return mood_energy_range, mood_valence_range

    for fb in skip_feedback:
        reason = fb.get("reason", "")
        if reason == "too_intense" and mood_energy_range:
            lo, hi = mood_energy_range
            mood_energy_range = (lo, max(lo, hi - 0.15))
        elif reason == "too_slow" and mood_energy_range:
            lo, hi = mood_energy_range
            mood_energy_range = (min(hi, lo + 0.15), hi)
        elif reason == "too_samey":
            # Widen both energy and valence ranges for more diversity
            if mood_energy_range:
                lo, hi = mood_energy_range
                mood_energy_range = (max(0.0, lo - 0.05), min(1.0, hi + 0.05))
            if mood_valence_range:
                lo, hi = mood_valence_range
                mood_valence_range = (max(0.0, lo - 0.05), min(1.0, hi + 0.05))

    return mood_energy_range, mood_valence_range


def build_candidate_list(text_taste_vector=None, audio_taste_vector=None,
                         current_song_id=None, exclude_ids=None,
                         mood_energy_range=None, mood_valence_range=None,
                         text_repulsion_vector=None, audio_repulsion_vector=None,
                         skip_feedback=None, subfolder_filter=None,
                         vibe_lock=None, mood_state=None,
                         mtime_min=None, mtime_max=None,
                         artist_mode=None, artists=None,
                         # Legacy params for backward compatibility
                         taste_vector=None, repulsion_vector=None):
    """Build a candidate list for slot allocation.

    Runs two ChromaDB searches (text + audio) when both vectors are available,
    then blends scores using MUQ_AUDIO_BLEND.

    Supports both new dual-vector API and legacy single-vector API:
      - New: pass text_taste_vector + audio_taste_vector
      - Legacy: pass taste_vector (text only, no audio search)

    Returns up to 40 candidates for LLM reranker.
    """
    # Handle legacy API: taste_vector → text_taste_vector
    if text_taste_vector is None and taste_vector is not None:
        text_taste_vector = taste_vector
    if text_repulsion_vector is None and repulsion_vector is not None:
        text_repulsion_vector = repulsion_vector

    if exclude_ids is None:
        exclude_ids = set()
    elif isinstance(exclude_ids, list):
        exclude_ids = set(exclude_ids)

    if current_song_id:
        exclude_ids.add(current_song_id)

    # Get recently played with graduated penalty
    # recency_exclude_hours: songs played within this window are excluded entirely
    # Beyond that, a score penalty applies based on how recently they were played
    exclude_hours = float(database._get_cached_setting("recency_exclude_hours") or "1")
    recently_played = set()
    recently_played_times = {}  # song_id -> hours ago
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT song_id, played_at FROM play_events WHERE played_at > ?",
            ((datetime.now() - timedelta(days=3)).isoformat(),),
        ).fetchall()
        now = datetime.now()
        for r in rows:
            sid = r["song_id"]
            try:
                ts = datetime.fromisoformat(r["played_at"])
                hours_ago = (now - ts).total_seconds() / 3600.0
            except (ValueError, TypeError):
                hours_ago = 0.0
            if hours_ago < exclude_hours:
                recently_played.add(sid)
            else:
                recently_played_times[sid] = hours_ago
    finally:
        conn.close()

    # Get blacklisted song IDs
    blacklisted = set()
    mood_blacklisted = set()
    cluster_blacklisted = set()
    artist_blacklisted = set()

    for b in database.get_blacklisted():
        level = b.get("level", "never_play")
        if level == "never_play":
            blacklisted.add(b["song_id"])
        elif level in ("avoid_week", "avoid_today"):
            blacklisted.add(b["song_id"])
        elif level == "avoid_mood":
            if b.get("mood"):
                mood_blacklisted.add(b["song_id"])
        elif level == "avoid_artist" and b.get("artist"):
            artist_blacklisted.add(b["artist"].lower())
        elif level == "avoid_similar" and b.get("cluster_ids"):
            try:
                cids = json.loads(b["cluster_ids"])
                if isinstance(cids, list):
                    for cid in cids:
                        cluster_blacklisted.add(cid)
            except (json.JSONDecodeError, TypeError):
                pass

    if artist_blacklisted:
        artist_song_ids = database.get_songs_by_artists(list(artist_blacklisted))
        blacklisted.update(artist_song_ids)
    if cluster_blacklisted:
        cluster_song_ids = database.get_songs_by_clusters(list(cluster_blacklisted))
        blacklisted.update(cluster_song_ids)

    # Filter out temporally suppressed songs (skip for 24h, session, etc.)
    try:
        suppressed = database.get_suppressed_song_ids()
        exclude_ids |= suppressed
    except Exception:
        pass

    exclude_ids |= recently_played | blacklisted | mood_blacklisted

    mood_energy_range, mood_valence_range = _apply_skip_feedback_bias(
        mood_energy_range, mood_valence_range, skip_feedback,
    )

    blend = config.MUQ_AUDIO_BLEND if config.MUQ_ENABLED else 0.0
    has_audio = (audio_taste_vector is not None and blend > 0)

    # Apply repulsion vectors
    if text_taste_vector and text_repulsion_vector:
        strength = 0.3
        text_taste_vector = [a - strength * b for a, b in zip(text_taste_vector, text_repulsion_vector)]
    if has_audio and audio_taste_vector and audio_repulsion_vector:
        strength = 0.3
        audio_taste_vector = [a - strength * b for a, b in zip(audio_taste_vector, audio_repulsion_vector)]

    # Apply mood-specific skip repulsion (push away from rejected sonic neighborhood)
    if mood_state and mood_state != "neutral" and config.MUQ_ENABLED:
        try:
            mood_repulsion = database.get_mood_repulsion_vector(mood_state)
            if mood_repulsion and audio_taste_vector and has_audio:
                audio_taste_vector = [
                    a - 0.15 * b
                    for a, b in zip(audio_taste_vector, mood_repulsion)
                ]
        except Exception:
            pass  # Best effort

    if has_audio:
        # Dual search: text + audio, then blend
        text_search_vector = text_taste_vector if text_taste_vector else [0.0] * get_embedding_dimension("songs")
        audio_search_vector = audio_taste_vector

        text_candidates = search_by_vector(
            text_search_vector, n=60,
            exclude_ids=list(exclude_ids),
            min_energy=mood_energy_range[0] if mood_energy_range else None,
            max_energy=mood_energy_range[1] if mood_energy_range else None,
            min_valence=mood_valence_range[0] if mood_valence_range else None,
            max_valence=mood_valence_range[1] if mood_valence_range else None,
            subfolder_filter=subfolder_filter,
            mtime_min=mtime_min, mtime_max=mtime_max,
            artist_mode=artist_mode, artists=artists,
            collection_name="songs",
        )

        try:
            audio_candidates = search_by_vector(
                audio_search_vector, n=60,
                exclude_ids=list(exclude_ids),
                min_energy=mood_energy_range[0] if mood_energy_range else None,
                max_energy=mood_energy_range[1] if mood_energy_range else None,
                min_valence=mood_valence_range[0] if mood_valence_range else None,
                max_valence=mood_valence_range[1] if mood_valence_range else None,
                subfolder_filter=subfolder_filter,
                mtime_min=mtime_min, mtime_max=mtime_max,
                artist_mode=artist_mode, artists=artists,
                collection_name=config.MUQ_AUDIO_COLLECTION,
            )
        except Exception:
            audio_candidates = []
            has_audio = False

        if has_audio:
            candidates = _merge_duo_candidates(text_candidates, audio_candidates, blend)
        else:
            candidates = text_candidates
    else:
        # Text-only search (legacy path)
        if text_taste_vector:
            candidates = search_by_vector(
                text_taste_vector, n=60,
                exclude_ids=list(exclude_ids),
                min_energy=mood_energy_range[0] if mood_energy_range else None,
                max_energy=mood_energy_range[1] if mood_energy_range else None,
                min_valence=mood_valence_range[0] if mood_valence_range else None,
                max_valence=mood_valence_range[1] if mood_valence_range else None,
                subfolder_filter=subfolder_filter,
                mtime_min=mtime_min, mtime_max=mtime_max,
                artist_mode=artist_mode, artists=artists,
                collection_name="songs",
            )
        else:
            candidates = search_by_vector(
                [0.0] * get_embedding_dimension("songs"),
                n=60,
                exclude_ids=list(exclude_ids),
                min_energy=mood_energy_range[0] if mood_energy_range else None,
                max_energy=mood_energy_range[1] if mood_energy_range else None,
                min_valence=mood_valence_range[0] if mood_valence_range else None,
                max_valence=mood_valence_range[1] if mood_valence_range else None,
                subfolder_filter=subfolder_filter,
                mtime_min=mtime_min, mtime_max=mtime_max,
                artist_mode=artist_mode, artists=artists,
                collection_name="songs",
            )

    # Apply vibe lock audio similarity boost
    if vibe_lock and vibe_lock.get("active") and vibe_lock.get("audio_embedding"):
        candidates = _apply_vibe_lock_boost(candidates, vibe_lock, exclude_ids)

    # Apply graduated recency penalty (beyond the hard exclude window)
    # Penalties: 1-6h: -0.15, 6-24h: -0.08, 1-3 days: -0.03
    if recently_played_times:
        for c in candidates:
            sid = c["song_id"]
            if sid in recently_played_times:
                hours_ago = recently_played_times[sid]
                if hours_ago < 6:
                    penalty = 0.15
                elif hours_ago < 24:
                    penalty = 0.08
                else:
                    penalty = 0.03
                c["score"] = max(0.0, c.get("score", 0.5) - penalty)

    return candidates[:40]


def _apply_vibe_lock_boost(candidates, vibe_lock, exclude_ids):
    """Boost candidates that sound like the vibe-locked song using audio embeddings.

    Queries the audio embedding collection directly with the locked song's
    audio_embedding and boosts/adds candidates based on sonic similarity.
    """
    if not vibe_lock or not vibe_lock.get("active") or not vibe_lock.get("audio_embedding"):
        return candidates

    if not config.MUQ_ENABLED:
        return candidates

    try:
        audio_collection = get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
        vibe_results = audio_collection.query(
            query_embeddings=[vibe_lock["audio_embedding"]],
            n_results=20,
            include=["distances", "metadatas"],
        )
        # Build a map of song_id -> vibe similarity score
        vibe_scores = {}
        for i in range(len(vibe_results["ids"][0])):
            sid = int(vibe_results["ids"][0][i])
            dist = vibe_results["distances"][0][i]
            vibe_scores[sid] = 1.0 - dist

        # Boost existing candidates and add high-scoring new ones
        candidate_map = {c["song_id"]: c for c in candidates}
        for sid, vibe_score in vibe_scores.items():
            if sid in exclude_ids:
                continue
            if sid in candidate_map:
                # Boost existing candidate: 70% vibe similarity weight
                existing = candidate_map[sid]
                existing["score"] = (1.0 - 0.70) * existing["score"] + 0.70 * vibe_score
            elif len(candidates) < 60:
                # Add as new candidate if scoring well
                if vibe_score > 0.5:
                    meta = None
                    for i in range(len(vibe_results["ids"][0])):
                        if int(vibe_results["ids"][0][i]) == sid:
                            meta = vibe_results["metadatas"][0][i]
                            break
                    song_info = database.get_song(sid)
                    if song_info:
                        candidates.append({
                            "song_id": sid,
                            "score": vibe_score * 0.85,
                            "energy": database.get_effective_energy(sid),
                            "valence": float((meta or {}).get("valence", 0.5)),
                            "cluster_id": int((meta or {}).get("cluster_id", -1)),
                            "artist": song_info.get("artist", ""),
                            "title": song_info.get("title", ""),
                            "source": "vibe_lock",
                        })

        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    except Exception as e:
        logger.debug("Vibe lock audio boost failed: %s", e)

    return candidates
