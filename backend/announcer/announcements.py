"""DJ announcement generation and trigger logic."""

import enum
import logging
import random
import time
from datetime import datetime

import config
from llm import llm_chat
from .tts import tts_say

logger = logging.getLogger(__name__)

# Announcement trigger types
class AnnounceTrigger(enum.Enum):
    MOOD_CHANGE = "mood_change"
    SKIP_STREAK = "skip_streak"
    WILDCARD = "wildcard"
    RECOVERY = "recovery"
    RADIO_INTRO = "radio_intro"
    SONG_INTRO = "song_intro"


# Track last announcement time to avoid spam
_last_announcement_time = 0
_DEFAULT_MIN_INTERVAL = 30  # default seconds between announcements
_radio_intro_count = 0  # counter for every-N-songs radio intros
_songs_since_last_skip = 0  # adaptive interval tracker


def get_announcement(context: dict, trigger: AnnounceTrigger = AnnounceTrigger.RADIO_INTRO) -> str:
    """Generate a DJ announcement using Ollama.

    Args:
        context: Dict with keys like:
            - current_song: {"artist": "...", "title": "..."}
            - next_song: {"artist": "...", "title": "..."}
            - mood_state: str
            - skip_streak: int
            - hour: int
            - liked_summary: str (recently liked songs)
            - trigger: AnnounceTrigger
        trigger: What triggered this announcement

    Returns:
        DJ announcement text, or fallback text on failure
    """
    global _last_announcement_time
    logger.debug(f"get_announcement START: trigger={trigger.value}")

    # Respect minimum interval between announcements
    now = time.time()
    interval = get_announcement_interval()
    if now - _last_announcement_time < interval:
        logger.debug("get_announcement: skipped (interval not elapsed)")
        return None

    current_song = context.get("current_song", {})
    next_song = context.get("next_song", {})
    mood_state = context.get("mood_state", "neutral")
    skip_streak = context.get("skip_streak", 0)
    hour = context.get("hour", datetime.now().hour)
    trigger_type = trigger.value

    # Build time-of-day greeting
    if hour < 6:
        greeting = "It's the early hours"
    elif hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    elif hour < 21:
        greeting = "Good evening"
    else:
        greeting = "It's late"

    # Build prompt based on trigger — each trigger has multiple prompt variants
    # for natural variety in announcement style
    if trigger_type == "mood_change":
        prompts = [
            f"""You are a radio DJ. Write a short 1-sentence transition announcement.

Context:
- Time: {greeting}
- Current mood: {mood_state}
- Previous song: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Be natural and conversational
- Mention the mood change briefly
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ smoothly segueing between two songs.

Context:
- Time: {greeting}
- Mood shifting to: {mood_state}
- Just played: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Up next: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Make the transition feel intentional and smooth
- Reference the contrast or connection between the songs
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ reflecting on the vibe shift.

Context:
- Time: {greeting}
- New mood: {mood_state}
- Previous song: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Be reflective and observational
- Like you're noticing the change in real-time
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
        ]
        prompt = random.choice(prompts)

    elif trigger_type == "skip_streak":
        prompts = [
            f"""You are a radio DJ. Write a short recovery announcement after several skips.

Context:
- Skip streak: {skip_streak} skips in a row
- Current mood: {mood_state}
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Acknowledge the skips lightly with humor
- Present the recovery song confidently
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ winning back your listener after they skipped a few tracks.

Context:
- Skips in a row: {skip_streak}
- Current mood: {mood_state}
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Be empathetic, like you understand their taste
- Show confidence that this next one will land
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ making a bold comeback after some skips.

Context:
- Skip streak: {skip_streak}
- Current mood: {mood_state}
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Be playful and self-deprecating about the skips
- Present the next track as a sure thing
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
        ]
        prompt = random.choice(prompts)

    elif trigger_type == "wildcard":
        prompts = [
            f"""You are a radio DJ. Write a short wildcard/exploration announcement.

Context:
- Current song: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Mood: {mood_state}

Rules:
- Hype up the discovery aspect
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ about to play something unexpected.

Context:
- Last track: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Curveball incoming: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Mood: {mood_state}

Rules:
- Build anticipation for the surprise
- Make it sound like a deliberate choice, not random
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ sharing a hidden gem with your listener.

Context:
- Just played: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Deep cut coming up: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Mood: {mood_state}

Rules:
- Sound like you're letting them in on a secret
- Be enthusiastic but not over-the-top
- Keep it under 20 words
- No quotes, no labels, just the announcement text""",
        ]
        prompt = random.choice(prompts)

    elif trigger_type == "recovery":
        prompts = [
            f"""You are a radio DJ. Write a short recovery announcement.

Context:
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Present something familiar and reliable
- Keep it under 15 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ bringing the listener back to familiar territory.

Context:
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}

Rules:
- Sound warm and reassuring
- This is a safe, well-loved track
- Keep it under 15 words
- No quotes, no labels, just the announcement text""",
        ]
        prompt = random.choice(prompts)

    elif trigger_type == "radio_intro":
        prompts = [
            f"""You are a radio DJ. Write a short song introduction for the next track.

Context:
- Song coming up: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Just played: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Mood: {mood_state}
- Time: {greeting}

Rules:
- Introduce the upcoming song naturally
- Be engaging and brief
- Keep it under 15 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ teeing up the next track.

Context:
- Up next: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Previous: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Mood: {mood_state}
- Time: {greeting}

Rules:
- Build a little anticipation
- Keep it under 15 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ setting up a song with a quick personal take.

Context:
- Song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Just played: {current_song.get('artist', 'Unknown')} - {current_song.get('title', 'Unknown')}
- Mood: {mood_state}
- Time: {greeting}

Rules:
- Share a brief thought about why this track matters
- Keep it under 15 words
- No quotes, no labels, just the announcement text""",
        ]
        prompt = random.choice(prompts)

    elif trigger_type == "song_intro":
        prompts = [
            f"""You are a radio DJ. Introduce the next song briefly.

Context:
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Mood: {mood_state}

Rules:
- Be brief and natural
- Keep it under 15 words
- No quotes, no labels, just the announcement text""",
            f"""You are a radio DJ giving a quick nod to the next track.

Context:
- Playing next: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Mood: {mood_state}

Rules:
- Sound casual and effortless
- Keep it under 15 words
- No quotes, no labels, just the announcement text""",
        ]
        prompt = random.choice(prompts)

    else:
        prompt = f"""You are a radio DJ. Write a short announcement.

Context:
- Next song: {next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}
- Mood: {mood_state}

Rules:
- Keep it under 20 words
- No quotes, no labels, just the announcement text"""

    # Call LLM
    logger.debug("get_announcement: calling LLM chat...")
    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options=config.OLLAMA_CHAT_OPTIONS,
            think=False,
        )
        logger.debug("get_announcement: LLM returned")
        announcement = response["message"]["content"].strip()
        # Remove any quotes or formatting, strip non-ASCII (emojis)
        announcement = announcement.strip('"\'').strip()
        announcement = announcement.encode('ascii', 'ignore').decode('ascii')
        _last_announcement_time = time.time()
        logger.debug(f"get_announcement: returning text ({len(announcement)} chars)")
        return announcement
    except Exception as e:
        logger.warning(f"get_announcement: LLM failed: {e}, using fallback")
        return _fallback_announcement(trigger, next_song)


def get_announcement_interval():
    """Get the minimum announcement interval from settings, adapted to listening behavior."""
    global _songs_since_last_skip
    try:
        from db import database
        val = database.get_setting("announcement_interval")
        base = int(val) if val is not None else _DEFAULT_MIN_INTERVAL
    except Exception:
        base = _DEFAULT_MIN_INTERVAL

    # Adaptive multiplier based on skip behavior
    # Few skips = tighten interval (more announcements), many skips = widen interval (fewer)
    if _songs_since_last_skip >= 20:
        multiplier = 3.0
    elif _songs_since_last_skip >= 10:
        multiplier = 2.0
    elif _songs_since_last_skip >= 5:
        multiplier = 1.5
    else:
        multiplier = 1.0
    return int(base * multiplier)


def should_announce(context: dict) -> bool:
    """Determine if an announcement should be played.

    Args:
        context: Dict with keys like:
            - trigger: AnnounceTrigger
            - skip_streak: int
            - hour: int
            - is_first_in_session: bool

    Returns:
        True if announcement should be played
    """
    global _songs_since_last_skip
    trigger = context.get("trigger")
    skip_streak = context.get("skip_streak", 0)
    hour = context.get("hour", datetime.now().hour)

    # Track songs since last skip for adaptive interval
    if skip_streak > 0:
        _songs_since_last_skip = 0
    else:
        _songs_since_last_skip += 1

    # Always announce mood changes and skip streaks
    if trigger in (AnnounceTrigger.MOOD_CHANGE, AnnounceTrigger.SKIP_STREAK,
                   AnnounceTrigger.WILDCARD, AnnounceTrigger.RECOVERY):
        return True

    # Radio intro: announce every 3-4 songs
    if trigger == AnnounceTrigger.RADIO_INTRO:
        global _radio_intro_count
        _radio_intro_count = (_radio_intro_count + 1) % 4
        return _radio_intro_count == 0

    # Song intro: announce every other song
    if trigger == AnnounceTrigger.SONG_INTRO:
        return (hour >= 6 and hour < 23)  # Only during waking hours

    return False


def play_announcement(text: str, next_song_filepath: str, trigger_type: str | None = None):
    """Render TTS for announcement text and return the WAV path.

    Args:
        text: Announcement text to synthesize
        next_song_filepath: Path to the next song's audio file
        trigger_type: Optional trigger type for context-based voice selection
                     (e.g. "mood_change", "skip_streak", "recovery")

    Returns:
        Path to WAV file, or None on failure
    """
    logger.debug(f"play_announcement: text_len={len(text)}, TTS_ENABLED={config.TTS_ENABLED}, trigger={trigger_type}")
    if not config.TTS_ENABLED:
        logger.debug("play_announcement: TTS disabled")
        return None
    logger.debug("play_announcement: calling tts_say()...")
    result = tts_say(text, song_id=None, trigger_type=trigger_type)
    logger.debug(f"play_announcement: tts_say returned {'path' if result else 'None'}")
    return result


def _fallback_announcement(trigger, next_song):
    """Return a simple fallback announcement when Ollama is unavailable."""
    artist = next_song.get("artist", "the artist") if next_song else "the artist"
    title = next_song.get("title", "a song") if next_song else "a song"

    fallbacks = {
        "mood_change": f"Switching things up. Here's {artist} - {title}.",
        "skip_streak": f"Back on track with {artist} - {title}.",
        "wildcard": f"Something different coming up: {artist} - {title}.",
        "recovery": f"Back to something solid. {artist} - {title}.",
        "radio_intro": f"Next up, {artist} - {title}.",
        "song_intro": f"Coming through, {artist} - {title}.",
    }
    return fallbacks.get(trigger.value, f"Here's {artist} - {title}.")
