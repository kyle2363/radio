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
                    archetype=None, slot_bias=None, active_pocket=None, hop_pocket=None):
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

    # Sort candidates by score (descending)
    sorted_candidates = sorted(filtered, key=lambda x: x.get("score", 0.5), reverse=True)

    # Track assigned song IDs to prevent duplicates
    assigned_ids = set()

    # Vibe lock mode: 4 exploit, 1 explore, 0 wildcard, 0 context
    if vibe_lock and vibe_lock.get("active"):
        exploit = []
        for c in sorted_candidates:
            sid = _get_song_id(c)
            if sid not in assigned_ids:
                exploit.append(c)
                assigned_ids.add(sid)
                if len(exploit) >= 4:
                    break

        explore = []
        for c in sorted_candidates:
            sid = _get_song_id(c)
            if sid not in assigned_ids:
                explore.append(c)
                assigned_ids.add(sid)
                break

        return {
            "exploit": exploit,
            "explore": explore,
            "wildcard": [],
            "context": [],
        }

    # Normal mode: 2 exploit, 1 explore, 1 wildcard, 1 context
    # Adjust based on exploration_level and archetype
    exploit_count_target = max(1, int(2 * (1 - exploration_level * 0.5)))
    explore_count_target = 1 + (1 if exploration_level > 0.6 else 0)
    wildcard_count_target = 1 if exploration_level > 0.4 else 0
    context_count_target = 1

    # Archetype overrides
    if archetype == "explorer":
        explore_count_target = max(2, explore_count_target + 1)
        wildcard_count_target = max(1, wildcard_count_target)
        exploit_count_target = max(1, exploit_count_target - 1)
    elif archetype == "comfort_listener":
        exploit_count_target = min(4, exploit_count_target + 2)
        explore_count_target = 0
        wildcard_count_target = 0
    elif archetype == "deep_listener":
        context_count_target = 2
        exploit_count_target = min(3, exploit_count_target + 1)
    elif archetype == "mood_listener":
        context_count_target = max(1, context_count_target)

    result = {
        "exploit": [],
        "explore": [],
        "wildcard": [],
        "context": [],
    }

    # Exploit: top similarity to taste vector
    exploit_count = 0
    for c in sorted_candidates:
        sid = _get_song_id(c)
        if sid not in assigned_ids:
            result["exploit"].append(c)
            assigned_ids.add(sid)
            exploit_count += 1
            if exploit_count >= exploit_count_target:
                break

    # Explore: different cluster than current song and not already assigned
    # Use explore_weight if slot_bias has explore_boost
    current_cluster = current_song.get("cluster_id") if current_song else None

    # Constellation-aware explore: boost candidates from same pocket but different cluster
    if active_pocket:
        for c in sorted_candidates:
            sid = _get_song_id(c)
            if sid not in assigned_ids and c.get("cluster_id") != current_cluster:
                if sid in active_pocket.song_ids:
                    c["_explore_weight"] = c.get("score", 0.5) * 1.2  # soft boost

    explore_sorted = sorted(
        sorted_candidates,
        key=lambda x: x.get("_explore_weight", x.get("score", 0.5)),
        reverse=True,
    )
    explore_count = 0
    for c in explore_sorted:
        sid = _get_song_id(c)
        if sid not in assigned_ids and c.get("cluster_id") != current_cluster:
            result["explore"].append(c)
            assigned_ids.add(sid)
            explore_count += 1
            if explore_count >= explore_count_target:
                break

    # Fallback explore: when current_cluster is None, pick from most different cluster
    if not result["explore"]:
        if current_cluster is None and sorted_candidates:
            # Find the cluster with the fewest candidates (most different from taste)
            from collections import Counter
            cluster_counts = Counter(c.get("cluster_id", -1) for c in sorted_candidates if _get_song_id(c) not in assigned_ids)
            if cluster_counts:
                rarest_cluster = min(cluster_counts, key=cluster_counts.get)
                for c in explore_sorted:
                    sid = _get_song_id(c)
                    if sid not in assigned_ids and c.get("cluster_id") == rarest_cluster:
                        result["explore"].append(c)
                        assigned_ids.add(sid)
                        break
        # Ultimate fallback: any unassigned song
        if not result["explore"]:
            for c in explore_sorted:
                sid = _get_song_id(c)
                if sid not in assigned_ids:
                    result["explore"].append(c)
                    assigned_ids.add(sid)
                    break

    # Wildcard: different cluster than current song, not already assigned
    # Use wildcard_weight if slot_bias has wildcard_boost
    # Constellation hop: prefer candidates from the hop pocket
    if hop_pocket:
        for c in sorted_candidates:
            sid = _get_song_id(c)
            if sid not in assigned_ids and c.get("cluster_id") != current_cluster:
                if sid in hop_pocket.song_ids:
                    c["_wildcard_weight"] = c.get("score", 0.5) * 1.3  # strong hop boost

    wildcard_sorted = sorted(
        sorted_candidates,
        key=lambda x: x.get("_wildcard_weight", x.get("score", 0.5)),
        reverse=True,
    )
    wildcard_count = 0
    for c in wildcard_sorted:
        sid = _get_song_id(c)
        if sid not in assigned_ids and c.get("cluster_id") != current_cluster:
            result["wildcard"].append(c)
            assigned_ids.add(sid)
            wildcard_count += 1
            if wildcard_count >= wildcard_count_target:
                break

    # Fallback wildcard: when current_cluster is None, pick from most different cluster
    if not result["wildcard"]:
        if current_cluster is None and sorted_candidates:
            from collections import Counter
            cluster_counts = Counter(c.get("cluster_id", -1) for c in sorted_candidates if _get_song_id(c) not in assigned_ids)
            if cluster_counts:
                rarest_cluster = min(cluster_counts, key=cluster_counts.get)
                for c in wildcard_sorted:
                    sid = _get_song_id(c)
                    if sid not in assigned_ids and c.get("cluster_id") == rarest_cluster:
                        result["wildcard"].append(c)
                        assigned_ids.add(sid)
                        break
        # Ultimate fallback: any remaining unassigned song
        if not result["wildcard"]:
            for c in wildcard_sorted:
                sid = _get_song_id(c)
                if sid not in assigned_ids:
                    result["wildcard"].append(c)
                    assigned_ids.add(sid)
                    break

    # Context: matches energy trajectory, not already assigned
    if current_song:
        target_energy = current_song.get("energy", 0.5)
        if current_song.get("id"):
            try:
                from db import database as _db
                target_energy = _db.get_effective_energy(current_song["id"])
            except Exception:
                pass
        for c in sorted_candidates:
            sid = _get_song_id(c)
            if sid not in assigned_ids:
                energy_diff = abs(c.get("energy", 0.5) - target_energy)
                if energy_diff < 0.2:
                    result["context"].append(c)
                    assigned_ids.add(sid)
                    break

    # Fallback context: any remaining unassigned song
    if not result["context"]:
        for c in sorted_candidates:
            sid = _get_song_id(c)
            if sid not in assigned_ids:
                result["context"].append(c)
                assigned_ids.add(sid)
                break

    return result


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
