"""LLM reranking for song recommendations.

Takes candidates and returns structured picks with per-song reasons.
Uses sequential LLM calls - no parallelism.
"""

import json
import logging
from datetime import datetime

from llm import llm_chat

import config

logger = logging.getLogger(__name__)


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

Return a JSON array with exactly 5 picks:
[
  {{"id": <song_id>, "slot": "exploit", "reason": "<1 sentence why>"}},
  {{"id": <song_id>, "slot": "exploit", "reason": "<1 sentence why>"}},
  {{"id": <song_id>, "slot": "explore", "reason": "<1 sentence why>"}},
  {{"id": <song_id>, "slot": "wildcard", "reason": "<1 sentence why>"}},
  {{"id": <song_id>, "slot": "context", "reason": "<1 sentence why>"}}
]

Rules:
- Each reason must be specific to the song — you can reference lyrics
- Slots must match: 2 exploit, 1 explore, 1 wildcard, 1 context
- Return ONLY the JSON array, no preamble
"""


def build_candidates_list(candidates):
    """Build a formatted string of candidates for the prompt."""
    import config
    lines = []
    for c in candidates:
        lyrics_preview = ""
        if c.get("lyrics"):
            words = c["lyrics"].split()[:config.LYRICS_RERANKER_WORDS]
            lyrics_preview = f' | lyrics: "{" ".join(words)}..."'
        lines.append(
            f"  {c['song_id']} | {c.get('artist', '?')} - {c.get('title', '?')} "
            f"| energy={c.get('energy', 0.5):.2f} | cluster={c.get('cluster_id', '?')}"
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
    if not candidates:
        return None

    current_song_str = f"{current_song['artist']} - {current_song['title']}" if current_song else "None"
    # Build current song lyrics snippet for prompt context
    import config
    current_lyrics = ""
    if current_song and current_song.get("lyrics"):
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
        mood_state=mood_state or "neutral",
        energy_trajectory=energy_trajectory,
        hour=hour or datetime.now().hour,
        skip_streak=skip_streak,
        candidates_list=candidates_list,
    )

    # Override the hardcoded slot rules in the prompt
    prompt = prompt.replace(
        "Return a JSON array with exactly 5 picks:",
        f"Return a JSON array with exactly {5 if not is_vibe_lock else 5} picks:",
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
            text = response["message"]["content"]

            # Extract JSON from response
            json_str = text.strip()
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                json_str = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else json_str

            picks = json.loads(json_str)
            if isinstance(picks, list) and len(picks) > 0:
                # Deduplicate by song_id - keep first occurrence of each
                seen_ids = set()
                unique_picks = []
                for pick in picks:
                    pid = pick.get("id")
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        unique_picks.append(pick)

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
            print(f"Reranker attempt {attempt + 1} failed: {e}")

    return None


def fallback_assignment(candidates, slots_result):
    """Fallback deterministic slot assignment when LLM fails.

    Uses the slot allocation from slots.py to assign songs.
    """
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
    return fallback_assignment(candidates, slots_result)
