"""Recovery song selection with fallback.

Used when the system needs to recover from a bad selection
(e.g., after multiple skips). Falls back to top completion
songs if no recovery-scored songs are available.
"""

from db import database


def get_recovery_song(current_song_id=None, exclude_ids=None):
    """Get a recovery song for right after a skip.

    Primary: songs with recovery_score > 0.75
    Fallback: top 5% by avg_completion

    Args:
        current_song_id: the song that was just skipped
        exclude_ids: set of song IDs to exclude

    Returns:
        dict with song info or None if no candidates.
    """
    if exclude_ids is None:
        exclude_ids = set()

    if current_song_id:
        exclude_ids.add(current_song_id)

    # Primary: high recovery_score, not played recently
    candidates = database.get_recovery_candidates(n=20)
    candidates = [s for s in candidates if s["id"] not in exclude_ids]

    if candidates:
        # Pick the one with lowest play_count among high recovery_score
        candidates.sort(key=lambda x: x.get("play_count", 0))
        return candidates[0], "recovery"

    # Fallback: top avg_completion
    fallback = database.get_top_completion(n=50)
    fallback = [s for s in fallback if s["id"] not in exclude_ids]

    if fallback:
        # Pick from middle to avoid over-playing the most popular
        mid = len(fallback) // 2
        return fallback[mid], "fallback"

    return None, "fallback"
