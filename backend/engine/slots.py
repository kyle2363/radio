"""Slot allocator for recommendation pipeline.

Allocates songs into exploit/explore/wildcard/context slots
based on taste similarity, recency, and vibe lock state.
"""

import logging

logger = logging.getLogger(__name__)


def _get_song_id(song):
    """Extract song_id from a candidate dict."""
    return song.get("song_id") or song.get("id")


def _is_assigned(song, assigned_ids):
    """Check if a song has already been assigned to a slot."""
    return _get_song_id(song) in assigned_ids


def allocate_slots(candidates, current_song, vibe_lock=None, exploration_level=0.3,
                    archetype=None, slot_bias=None, active_pocket=None, hop_pocket=None, counts=None):
    """Allocate candidates into named slots.

    Args:
        candidates: list of dicts with song_id, score, energy, valence, cluster_id
        current_song: dict with id, energy, cluster_id
        vibe_lock: dict with active, embedding, songs_remaining or None
        exploration_level: float 0-1, higher = more exploration
        archetype: str - listener archetype (explorer, comfort_listener, deep_listener, mood_listener)
        slot_bias: dict with optional overrides from guest persona:
            - energy_range: (min, max) tuple
            - valence_range: (min, max) tuple
            - wildcard_boost: float score multiplier for wildcard
            - explore_boost: float score multiplier for explore
        active_pocket: TastePocket or None — current constellation pocket
        hop_pocket: TastePocket or None — target pocket for taste hopping
        counts: optional per-slot budget from portable.slots.slot_counts (phone tuning)

    Returns:
        dict with slot names as keys and song dicts as values, e.g.:
        {
            "exploit": [...],
            "explore": [...],
            "wildcard": [...],
            "context": [...]
        }
    """
    if not candidates:
        return {"exploit": [], "explore": [], "wildcard": [], "context": []}

    # Apply slot_bias filters if provided (from guest persona)
    filtered = candidates
    # Truncate vibe_lock embeddings for logging (avoid dumping huge vectors)
    _vl_log = vibe_lock
    if isinstance(vibe_lock, dict) and "embedding" in vibe_lock:
        _vl_log = {k: v for k, v in vibe_lock.items() if k not in ("embedding", "audio_embedding")}
        emb = vibe_lock["embedding"]
        aemb = vibe_lock.get("audio_embedding")
        _vl_log["embedding"] = f"[...{len(emb)}d vector...]" if emb else "None"
        _vl_log["audio_embedding"] = f"[...{len(aemb)}d vector...]" if aemb else "None"
    logger.debug("allocate_slots: candidates=%d vibe_lock=%r exploration=%f archetype=%r slot_bias=%r",
                 len(candidates), _vl_log, exploration_level, archetype, slot_bias)
    if slot_bias:
        logger.debug("allocate_slots: slot_bias type=%s", type(slot_bias).__name__)
        energy_range = slot_bias.get("energy_range")
        valence_range = slot_bias.get("valence_range")
        if energy_range or valence_range:
            filtered = []
            for c in candidates:
                if energy_range:
                    e = c.get("energy", 0.5)
                    if e < energy_range[0] or e > energy_range[1]:
                        continue
                if valence_range:
                    v = c.get("valence", 0.5)
                    if v < valence_range[0] or v > valence_range[1]:
                        continue
                filtered.append(c)
        # Apply boost multipliers to scores
        wildcard_boost = slot_bias.get("wildcard_boost", 0.0)
        explore_boost = slot_bias.get("explore_boost", 0.0)
        if wildcard_boost > 0 or explore_boost > 0:
            for c in filtered:
                base = c.get("score", 0.5)
                c["_explore_weight"] = base * (1 + explore_boost)
                c["_wildcard_weight"] = base * (1 + wildcard_boost)

    if not filtered:
        return {"exploit": [], "explore": [], "wildcard": [], "context": []}

    from portable.slots import allocate_ranked_slots

    # Preserve constellation weighting and effective-energy context on desktop.
    current_cluster = current_song.get("cluster_id") if current_song else None
    for candidate in filtered:
        sid = _get_song_id(candidate)
        if candidate.get("cluster_id") != current_cluster:
            if active_pocket and sid in active_pocket.song_ids:
                candidate["_explore_weight"] = candidate.get("score", .5) * 1.2
            if hop_pocket and sid in hop_pocket.song_ids:
                candidate["_wildcard_weight"] = candidate.get("score", .5) * 1.3
    effective_current = dict(current_song) if current_song else None
    if effective_current and effective_current.get("id"):
        try:
            from db import database
            effective_current["energy"] = database.get_effective_energy(effective_current["id"])
        except Exception:
            pass
    return allocate_ranked_slots(filtered, effective_current,
                                 vibe_lock=bool(vibe_lock and vibe_lock.get("active")),
                                 exploration_level=exploration_level, archetype=archetype, counts=counts)


def score_candidate(candidate, current_song, vibe_lock=None):
    """Compute a final score for a candidate song.

    Combines taste similarity, transition score, and vibe lock weight.
    """
    base_score = candidate.get("score", 0.5)
    current_cluster = current_song.get("cluster_id") if current_song else None
    candidate_cluster = candidate.get("cluster_id", -1)

    # Cluster diversity bonus (wildcard preference)
    cluster_bonus = 0.05 if current_cluster and candidate_cluster != current_cluster else 0.0

    # Vibe lock weight
    vibe_weight = 0.45  # normal
    if vibe_lock and vibe_lock.get("active"):
        vibe_weight = 0.70  # heavier weight toward vibe lock embedding

    final_score = base_score * vibe_weight + cluster_bonus
    return final_score
