"""LLM reranking for song recommendations.

Takes candidates and returns structured picks with per-song reasons.
Uses sequential LLM calls - no parallelism.
"""

import json
import logging
from datetime import datetime

import config

logger = logging.getLogger(__name__)


def llm_chat():
    """Keep deterministic helpers importable without optional provider packages."""
    from llm import llm_chat as resolve
    return resolve()


RERANK_PROMPT = """\
You are an expert radio DJ. Pick the best songs for right now from the candidates below.

CURRENT STATE:
- Current song: {current_song}
- Current song lyrics: {current_lyrics}
- Mood state: {mood_state}
- Energy trajectory: {energy_trajectory}
- Hour of day: {hour}
- Skip streak: {skip_streak}

CANDIDATES (id | artist - title | energy | genre | lyrics):
{candidates_list}

Return a JSON object with a \"picks\" array containing exactly 5 entries:
{{
  "picks": [
    {{"id": <song_id>, "slot": "exploit", "reason": "<1 sentence why>"}},
    {{"id": <song_id>, "slot": "exploit", "reason": "<1 sentence why>"}},
    {{"id": <song_id>, "slot": "explore", "reason": "<1 sentence why>"}},
    {{"id": <song_id>, "slot": "wildcard", "reason": "<1 sentence why>"}},
    {{"id": <song_id>, "slot": "context", "reason": "<1 sentence why>"}}
  ]
}}

Rules:
- Each reason must be specific to the song — you can reference lyrics
- Slots must match: 2 exploit, 1 explore, 1 wildcard, 1 context
- Return ONLY the JSON object, no preamble
"""


def build_candidates_list(candidates):
    """Build a formatted string of candidates for the prompt."""
    import config
    lines = []
    for c in candidates:
        try:
            energy = float(c["energy"]) if c.get("energy") is not None else 0.5
        except (TypeError, ValueError):
            energy = 0.5
        lyrics_preview = ""
        if isinstance(c.get("lyrics"), str) and c["lyrics"]:
            words = c["lyrics"].split()[:config.LYRICS_RERANKER_WORDS]
            lyrics_preview = f' | lyrics: "{" ".join(words)}..."'
        lines.append(
            f"  {c['song_id']} | {c.get('artist') or ''} - {c.get('title') or ''} "
            f"| energy={energy:.2f} | cluster={c.get('cluster_id', '?')}"
            f"{lyrics_preview}"
        )
    return "\n".join(lines)


def rerank(candidates, current_song=None, mood_state=None, energy_trajectory="stable", hour=None, skip_streak=0, vibe_lock=None):
    """Rerank candidates using LLM.

    Args:
        candidates: list of candidate dicts (may include 'lyrics' key)
        current_song: dict with artist, title or None
        mood_state: string mood name or None
        energy_trajectory: "rising", "falling", or "stable"
        hour: current hour (0-23)
        skip_streak: number of consecutive skips

    Returns:
        list of pick dicts: [{"id": N, "slot": "exploit", "reason": "..."}, ...]
        or None if LLM fails after retries.
    """
    # Truncate vibe_lock for logging (avoid dumping huge embedding vectors)
    _vl_log = None
    if isinstance(vibe_lock, dict):
        _vl_log = {k: v for k, v in vibe_lock.items() if k not in ("embedding", "audio_embedding")}
        emb = vibe_lock.get("embedding")
        aemb = vibe_lock.get("audio_embedding")
        _vl_log["embedding"] = f"[...{len(emb)}d vector...]" if emb else "None"
        _vl_log["audio_embedding"] = f"[...{len(aemb)}d vector...]" if aemb else "None"
    logger.debug("rerank: candidates=%d mood=%s energy_traj=%s hour=%s skip_streak=%d vibe_lock=%r",
                 len(candidates) if candidates else 0, mood_state, energy_trajectory, hour, skip_streak, _vl_log)
    if not candidates:
        logger.debug("rerank: no candidates, returning None")
        return None

    current_song_str = f"{current_song.get('artist') or ''} - {current_song.get('title') or ''}" if current_song else "None"
    # Build current song lyrics snippet for prompt context
    import config
    current_lyrics = ""
    if current_song and isinstance(current_song.get("lyrics"), str) and current_song["lyrics"]:
        words = current_song["lyrics"].split()[:config.LYRICS_CURRENT_WORDS]
        current_lyrics = " ".join(words)

    candidates_list = build_candidates_list(candidates)

    # Adjust slot distribution for vibe lock mode
    is_vibe_lock = vibe_lock and vibe_lock.get("active")
    if is_vibe_lock:
        slot_desc = "4 exploit, 1 explore"
        slot_rules = "Slots must match: 4 exploit, 1 explore"
    else:
        slot_desc = "2 exploit, 1 explore, 1 wildcard, 1 context"
        slot_rules = "Slots must match: 2 exploit, 1 explore, 1 wildcard, 1 context"

    prompt = RERANK_PROMPT.format(
        current_song=current_song_str,
        current_lyrics=current_lyrics,
        mood_state="none" if mood_state == "neutral" else (mood_state or "none"),
        energy_trajectory=energy_trajectory,
        hour=hour or datetime.now().hour,
        skip_streak=skip_streak,
        candidates_list=candidates_list,
    )

    # Override the hardcoded slot rules in the prompt
    prompt = prompt.replace(
        'Return a JSON object with a "picks" array containing exactly 5 entries:',
        f'Return a JSON object with a "picks" array containing exactly {5 if not is_vibe_lock else 5} entries:',
    )
    prompt = prompt.replace(
        "Slots must match: 2 exploit, 1 explore, 1 wildcard, 1 context",
        slot_rules,
    )

    # Adjust needed slots for fixup
    if is_vibe_lock:
        needed = {"exploit": 4, "explore": 1, "wildcard": 0, "context": 0}
    else:
        needed = {"exploit": 2, "explore": 1, "wildcard": 1, "context": 1}

    # Retry up to 3 times
    logger.info(f"Reranker: calling LLM with {len(candidates)} candidates, vibe_lock={is_vibe_lock}")
    candidate_ids = {c["song_id"] for c in candidates}
    for attempt in range(3):
        logger.info(f"Reranker: attempt {attempt + 1}/3")
        try:
            logger.info("Reranker: sending LLM request...")
            response = llm_chat().chat(
                model=None,
                messages=[{"role": "user", "content": prompt}],
                options=config.OLLAMA_CHAT_OPTIONS,
                think=False,
                response_format={"type": "json_object"},
            )
            text = response.content

            # Extract JSON from response
            json_str = text.strip()
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                json_str = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else json_str

            parsed = json.loads(json_str)
            picks = parsed.get("picks", []) if isinstance(parsed, dict) else parsed
            if isinstance(picks, list) and len(picks) > 0:
                # Deduplicate by song_id - keep first occurrence of each
                seen_ids = set()
                unique_picks = []
                for pick in picks:
                    if not isinstance(pick, dict):
                        continue
                    pid = pick.get("id")
                    if type(pid) is int and pid in candidate_ids and pid not in seen_ids:
                        seen_ids.add(pid)
                        unique_picks.append(pick)
                        if len(unique_picks) == 5:
                            break

                # Validate slot distribution
                valid_slots = {"exploit", "explore", "wildcard", "context"}
                slot_counts = {}
                for p in unique_picks:
                    s = p.get("slot", "context")
                    if s not in valid_slots:
                        s = "context"
                        p["slot"] = s
                    slot_counts[s] = slot_counts.get(s, 0) + 1

                # Fix over-represented slots: move excess picks to missing slots
                for slot, count in list(slot_counts.items()):
                    excess = count - needed.get(slot, 0)
                    if excess > 0:
                        movable = [p for p in unique_picks if p.get("slot") == slot]
                        for i in range(min(excess, len(movable))):
                            for target_slot, target_count in needed.items():
                                if slot_counts.get(target_slot, 0) < target_count:
                                    movable[i]["slot"] = target_slot
                                    slot_counts[slot] -= 1
                                    slot_counts[target_slot] = slot_counts.get(target_slot, 0) + 1
                                    break

                # Fill missing slots from candidates
                for slot, count in needed.items():
                    current = slot_counts.get(slot, 0)
                    if current < count:
                        for c in candidates:
                            sid = c["song_id"]
                            if sid not in seen_ids:
                                unique_picks.append({
                                    "id": sid,
                                    "slot": slot,
                                    "reason": f"Selected as {slot} pick based on taste similarity and cluster diversity.",
                                })
                                seen_ids.add(sid)
                                slot_counts[slot] = slot_counts.get(slot, 0) + 1
                                if slot_counts[slot] >= count:
                                    break

                return unique_picks

        except Exception as e:
            logger.warning("Reranker attempt %d failed: %s", attempt + 1, e)

    return None


def fallback_assignment(candidates, slots_result):
    """Fallback deterministic slot assignment when LLM fails.

    Uses the slot allocation from slots.py to assign songs.
    """
    logger.debug("fallback_assignment: candidates=%d slots_result=%r", len(candidates) if candidates else 0, {k: len(v) for k, v in slots_result.items()} if slots_result else None)
    picks = []
    for slot_name, slot_songs in slots_result.items():
        for song in slot_songs:
            picks.append({
                "id": song["song_id"],
                "slot": slot_name,
                "reason": f"Selected as {slot_name} pick based on taste similarity and cluster diversity.",
            })
    return picks


def deterministic_assignment(candidates, slots_result):
    """Deterministic slot assignment for normal mode (no LLM).

    Same as fallback_assignment but always used in normal mode.
    """
    logger.debug("deterministic_assignment: candidates=%d slots_result=%r", len(candidates) if candidates else 0, {k: len(v) for k, v in slots_result.items()} if slots_result else None)
    return fallback_assignment(candidates, slots_result)
