"""Talk segment generation for the Radio Talk Show feature.

Generates three types of segments between songs:
- CALLER: Fake phone-in with DJ + caller (2 voices)
- DJ_MONOLOGUE: Longer reflective DJ segment (1 voice)
- GUEST_TAKEOVER: Guest persona takes over for 3-5 songs

All segments are rendered to WAV via TTS and played through play_chain().
"""

import enum
import json
import random
from dataclasses import dataclass, field
from datetime import datetime

import config
from llm import llm_chat
from announcer.tts import tts_say
from announcer.nonverbal import inject_nonverbal_tags
from announcer.personas import PERSONAS, Persona, get_persona, get_guest_personas, get_caller_personas, get_enabled_caller_personas
import logging
logger = logging.getLogger(__name__)

# Default cooldown: don't reuse a caller/guest within N segments
DEFAULT_CALLER_COOLDOWN = 2


class TalkSegmentType(enum.Enum):
    CALLER = "caller"
    DJ_MONOLOGUE = "dj_monologue"
    GUEST_TAKEOVER_INTRO = "guest_takeover_intro"
    GUEST_TAKEOVER_OUTRO = "guest_takeover_outro"


@dataclass
class TalkSegment:
    """A talk segment that plays between songs."""
    type: TalkSegmentType
    wav_paths: list[str]                     # Ordered WAV files to chain
    duration_secs: float                     # Estimated duration
    text: str | None = None                  # Raw text (for debugging)
    persona_id: str | None = None            # DJ persona used
    secondary_persona_id: str | None = None  # Caller or guest persona
    mood_shift: str | None = None            # Optional: "energetic"|"chill"|etc
    energy_direction: str | None = None      # Optional: "up"|"down"
    guest_persona_id: str | None = None      # For takeover: which guest
    reason: str = ""

    def to_queue_item(self) -> dict:
        """Convert to a queue item dict for QueueManager."""
        item = {
            "type": "talk_segment",
            "segment_type": self.type.value,
            "wav_paths": self.wav_paths,
            "duration_secs": self.duration_secs,
            "slot": "talk_segment",
            "reason": self.reason,
            "persona_id": self.persona_id,
            "secondary_persona_id": self.secondary_persona_id,
        }
        if self.mood_shift:
            item["mood_shift"] = self.mood_shift
        if self.energy_direction:
            item["energy_direction"] = self.energy_direction
        if self.guest_persona_id:
            item["guest_persona_id"] = self.guest_persona_id
        return item


# ---- Helpers ----

def _get_recent_songs_for_context(count: int = 3) -> list[dict]:
    """Get recently played songs for LLM context."""
    try:
        from db import database
        conn = database.get_connection()
        try:
            rows = conn.execute(
                """SELECT DISTINCT qh.song_id, s.artist, s.title, s.lyrics
                   FROM queue_history qh
                   JOIN songs s ON qh.song_id = s.id
                   WHERE qh.song_id IS NOT NULL
                   ORDER BY qh.played_at DESC
                   LIMIT ?""",
                (count,),
            ).fetchall()
            songs = []
            for r in rows:
                lyrics_snippet = ""
                if r["lyrics"]:
                    words = r["lyrics"].split()[:config.LYRICS_TALK_WORDS]
                    lyrics_snippet = " ".join(words)
                songs.append({
                    "artist": r["artist"] or "Unknown",
                    "title": r["title"] or "Unknown",
                    "lyrics_snippet": lyrics_snippet,
                })
            return songs or []
        finally:
            conn.close()
    except Exception as e:
        print(f"talk_segments: failed to get recent songs: {e}")
        return []


def _get_session_context() -> dict:
    """Build session context for monologue generation."""
    try:
        from db import database
        session_id = database.get_setting("last_session_id")
        if not session_id or session_id == "NULL":
            return {}

        # Get session details directly
        conn = database.get_connection()
        try:
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
            if not session:
                return {}

            started_str = session["started_at"]
            ended_str = session.get("ended_at")
            duration_minutes = 0
            if started_str:
                fmt = "%Y-%m-%d %H:%M:%S"
                try:
                    started = datetime.strptime(started_str, fmt) if "T" not in started_str else datetime.fromisoformat(started_str)
                    if ended_str:
                        ended = datetime.strptime(ended_str, fmt) if "T" not in ended_str else datetime.fromisoformat(ended_str)
                        duration_minutes = int((ended - started).total_seconds() / 60)
                except Exception:
                    pass

            # Count songs played in this session
            song_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM queue_history WHERE session_id = ?", (int(session_id),)
            ).fetchone()
            count_val = song_count["cnt"] if song_count else 0

            # Get top artists from this session
            top_rows = conn.execute(
                """SELECT s.artist, COUNT(*) as cnt
                   FROM queue_history qh
                   JOIN songs s ON qh.song_id = s.id
                   WHERE qh.session_id = ? AND s.artist IS NOT NULL
                   GROUP BY s.artist
                   ORDER BY cnt DESC
                   LIMIT 3""",
                (int(session_id),),
            ).fetchall()
            top_artists = [r["artist"] for r in top_rows if r["artist"]]

            return {
                "session_minutes": duration_minutes,
                "song_count": count_val,
                "top_artists": top_artists,
            }
        finally:
            conn.close()
    except Exception as e:
        print(f"talk_segments: session context error: {e}")
        return {}


# ---- Caller Segments ----

CALLER_PROMPT_TEMPLATE = """You are writing a short phone-in exchange for a radio show.

Context:
- DJ persona: {dj_personality}
- Caller persona: {caller_personality}
- Caller name: {caller_name}
- Recently played: {recent_songs_str}
- Recent lyrics: {recent_lyrics_str}
- Current mood: {mood_state}
- Time: {greeting}
- Next song coming up: {next_song_str}

Generate a {length_desc} phone call exchange between the DJ and the caller.
The caller is phoning in to react to what's been playing.

Return valid JSON only with this exact structure:
{{
{json_fields}    "mood_shift": null,
    "energy_direction": null
}}

Rules:
- Total should be {duration_desc} when spoken
- Caller MUST reference at least one actual song or artist from what's been playing
- Caller should reference specific lyrics from songs they mention
- Natural, conversational — use filler words, pauses, casual speech
- The caller_request should subtly suggest a direction but not be a command
- mood_shift: set to "energetic", "chill", "focus", "melancholy" or null
- energy_direction: set to "up", "down" or null
- If next song is provided, the dj_closer should tease it naturally (e.g. "Coming up next, we've got...")
- If no next song info, dj_closer should be generic ("Stay tuned" / "More great music ahead")
- NO markdown, NO backticks, NO extra text — just the JSON object"""


def generate_caller_segment(
    mood_state: str = "neutral",
    caller_mode: str = "influences_queue",
    next_song: dict | None = None,
    cooldown: int = DEFAULT_CALLER_COOLDOWN,
) -> TalkSegment | None:
    """Generate a caller phone-in segment between songs.
    
    Makes one LLM call that produces both DJ and caller dialogue.
    Each line is rendered with the appropriate persona's voice.
    
    Args:
        mood_state: Current mood state for context
        caller_mode: "influences_queue" or "narrative_only"
        next_song: Dict with 'artist' and 'title' of the upcoming song
        cooldown: Number of segments before a caller can be reused
        
    Returns:
        TalkSegment with WAV paths, or None on failure
    """
    dj = get_persona("dj")
    caller = _pick_caller(cooldown)
    if not caller:
        return None

    recent = _get_recent_songs_for_context(3)
    recent_str = "; ".join([f"{s['artist']} - {s['title']}" for s in recent]) or "no songs yet"
    recent_lyrics_str = ""
    lyrical_songs = [s for s in recent if s.get("lyrics_snippet")]
    if lyrical_songs:
        parts = []
        for s in lyrical_songs:
            parts.append(f'"{s["title"]}" lyrics: "{s["lyrics_snippet"]}..."')
        recent_lyrics_str = "\n".join(parts)

    hour = datetime.now().hour
    if hour < 6:
        greeting = "It's the early hours"
    elif hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    # 50/50 chance of providing next song info to the LLM
    if next_song and random.random() < 0.5:
        next_song_str = f"{next_song.get('artist', 'Unknown')} - {next_song.get('title', 'Unknown')}"
    else:
        next_song_str = "(not yet known)"

    # Randomly decide conversation length: 2-4 full rounds (5-9 lines)
    # 2 rounds = short (current baseline), 3 rounds = medium, 4 rounds = long
    num_rounds = random.choices([2, 3, 4], weights=[0.4, 0.4, 0.2], k=1)[0]

    # Build JSON fields and voice lines dynamically
    json_fields = []
    voice_lines = []
    for i in range(num_rounds):
        dj_key = f"dj_line_{i + 1}"
        caller_key = f"caller_line_{i + 1}"
        json_fields.append(f'    "{dj_key}": "DJ line {i + 1}",')
        json_fields.append(f'    "{caller_key}": "Caller line {i + 1}",')
        voice_lines.append((dj_key, dj.voice_id if dj else "amy"))
        voice_lines.append((caller_key, caller.voice_id))
    # Closer is always last DJ line
    json_fields.append('    "dj_closer": "DJ closing line",')
    voice_lines.append(("dj_closer", dj.voice_id if dj else "amy"))

    # Duration descriptions matching the round count
    duration_map = {2: "30-45 seconds", 3: "45-70 seconds", 4: "70-90 seconds"}
    length_map = {2: "normal", 3: "medium-length", 4: "extended"}

    prompt = CALLER_PROMPT_TEMPLATE.format(
        dj_personality=dj.personality if dj else "radio DJ",
        caller_personality=caller.personality,
        caller_name=caller.name,
        recent_songs_str=recent_str,
        recent_lyrics_str=recent_lyrics_str or "(no lyrics available)",
        mood_state=mood_state,
        greeting=greeting,
        next_song_str=next_song_str,
        length_desc=length_map[num_rounds],
        duration_desc=duration_map[num_rounds],
        json_fields="\n".join(json_fields) + "\n",
    )

    logger.info(f"generate_caller_segment: calling LLM (mood={mood_state})")
    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options=config.OLLAMA_CHAT_OPTIONS,
            think=False,
        )
        logger.info("generate_caller_segment: LLM returned, rendering TTS...")
        content = response["message"]["content"].strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        data = json.loads(content)
        # voice_lines was built dynamically above based on num_rounds

        wav_paths = []
        total_duration = 0.0
        for key, voice_id in voice_lines:
            text = data.get(key, "").strip()
            if not text:
                continue
            # Inject non-verbal tags into caller lines only (not DJ lines)
            if key.startswith("caller"):
                text = inject_nonverbal_tags(text)
            # Pass speed baseline based on whether this is a caller line or DJ line
            line_speed = caller.speed_base if key.startswith("caller") else dj.speed_base
            wav = tts_say(text, voice_id=voice_id, speed=line_speed)
            if wav:
                wav_paths.append(wav)
                # Estimate duration: ~80ms per character
                total_duration += max(1.0, len(text) * 0.08)

        if not wav_paths:
            return None

        mood_shift = data.get("mood_shift")
        energy_dir = data.get("energy_direction")

        # In narrative_only mode, don't pass mood_shift/energy_direction
        if caller_mode == "narrative_only":
            mood_shift = None
            energy_dir = None

        full_text = " | ".join([data.get(k, "") for k, _ in voice_lines])

        return TalkSegment(
            type=TalkSegmentType.CALLER,
            wav_paths=wav_paths,
            duration_secs=total_duration,
            text=full_text,
            persona_id="dj",
            secondary_persona_id=caller.id,
            mood_shift=mood_shift,
            energy_direction=energy_dir,
            reason=f"{caller.name} calling in",
        )
    except Exception as e:
        print(f"talk_segments: caller generation failed: {e}")
        return None


# ---- DJ Monologue ----

MONOLOGUE_PROMPT_TEMPLATE = """You are a late-night radio DJ delivering a personal monologue.

Context:
- Session duration: {session_minutes} minutes
- Songs played: {song_count}
- Top artists tonight: {top_artists}
- Current mood: {mood_state}
- Time: {greeting}
- Trigger: {trigger_reason}
- Recent lyrics: {recent_lyrics}

Write a 60-90 second reflective monologue. Be personal, warm, and natural — 
like a real late-night radio host talking to their listeners.
Reference specific songs, artists, or lyrical themes if available.
Reflect on the emotional journey of the lyrics that played.
Keep it flowing and thoughtful. No quotes, no labels — just the spoken text."""


def generate_dj_monologue(
    mood_state: str = "neutral",
    trigger_reason: str = "regular interlude",
) -> TalkSegment | None:
    """Generate a longer DJ monologue segment.
    
    This is a single-voice segment with richer context.
    """
    dj = get_persona("dj")
    session = _get_session_context()
    recent = _get_recent_songs_for_context(3)
    top_artists = session.get("top_artists", [])
    top_str = ", ".join(top_artists) if top_artists else "a great mix of music"

    # Build recent lyrics context
    recent_lyrics = ""
    lyrical_songs = [s for s in recent if s.get("lyrics_snippet")]
    if lyrical_songs:
        parts = []
        for s in lyrical_songs:
            parts.append(f'{s["artist"]} - {s["title"]}: "{s["lyrics_snippet"]}..."')
        recent_lyrics = "\n".join(parts)

    hour = datetime.now().hour
    if hour < 6:
        greeting = "the early hours"
    elif hour < 12:
        greeting = "the morning"
    elif hour < 17:
        greeting = "the afternoon"
    else:
        greeting = "the evening"

    prompt = MONOLOGUE_PROMPT_TEMPLATE.format(
        session_minutes=session.get("session_minutes", 0),
        song_count=session.get("song_count", 0),
        top_artists=top_str,
        mood_state=mood_state,
        greeting=greeting,
        trigger_reason=trigger_reason,
        recent_lyrics=recent_lyrics or "(no lyrics available)",
    )

    logger.info(f"generate_dj_monologue: calling LLM (mood={mood_state})")
    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options=config.OLLAMA_CHAT_OPTIONS,
            think=False,
        )
        logger.info("generate_dj_monologue: LLM returned, rendering TTS...")
        text = response["message"]["content"].strip()
        # Clean up
        text = text.strip('"\'').strip()
        text = text.encode('ascii', 'ignore').decode('ascii')

        if not text:
            return None

        voice_id = dj.voice_id if dj else "amy"
        wav = tts_say(text, voice_id=voice_id, speed=dj.speed_base)
        if not wav:
            return None

        duration = max(30.0, len(text) * 0.08)
        return TalkSegment(
            type=TalkSegmentType.DJ_MONOLOGUE,
            wav_paths=[wav],
            duration_secs=duration,
            text=text,
            persona_id="dj",
            reason="DJ monologue",
        )
    except Exception as e:
        print(f"talk_segments: monologue failed: {e}")
        return None


# ---- Guest Takeover Segments ----

GUEST_INTRO_PROMPT_TEMPLATE = """You are {guest_name}, a guest DJ taking over for the next few songs.
Your personality: {guest_personality}

Write a short 15-20 second introduction announcing your takeover.
Mention what kind of vibe you're bringing.
Keep it energetic and natural. No quotes, no labels."""

GUEST_OUTRO_PROMPT_TEMPLATE = """You are {guest_name}, handing the show back to the main DJ after your set.

Write a short 10-15 second closing announcement.
Thank the listeners and hand it back. Keep it brief and natural."""


def generate_guest_takeover_intro(guest_persona_id: str) -> TalkSegment | None:
    """Generate a guest DJ takeover intro announcement."""
    guest = get_persona(guest_persona_id)
    if not guest:
        return None

    prompt = GUEST_INTRO_PROMPT_TEMPLATE.format(
        guest_name=guest.name,
        guest_personality=guest.personality,
    )

    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options={**config.OLLAMA_CHAT_OPTIONS, "temperature": 0.5},
            think=False,
        )
        text = response["message"]["content"].strip()
        text = text.strip('"\'').strip()
        text = text.encode('ascii', 'ignore').decode('ascii')

        wav = tts_say(text, voice_id=guest.voice_id, speed=guest.speed_base)
            wav_paths=[wav],
            duration_secs=max(8.0, len(text) * 0.08),
            text=text,
            persona_id="dj",
            secondary_persona_id=guest.id,
            guest_persona_id=guest.id,
            reason=f"{guest.name} taking over",
        )
    except Exception as e:
        print(f"talk_segments: guest intro failed: {e}")
        return None


def generate_guest_takeover_outro(guest_persona_id: str) -> TalkSegment | None:
    """Generate a guest DJ takeover outro (handing back to main DJ)."""
    guest = get_persona(guest_persona_id)
    if not guest:
        return None

    prompt = GUEST_OUTRO_PROMPT_TEMPLATE.format(
        guest_name=guest.name,
        guest_personality=guest.personality,
    )

    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options={**config.OLLAMA_CHAT_OPTIONS, "temperature": 0.5},
            think=False,
        )
        text = response["message"]["content"].strip()
        text = text.strip('"\'').strip()
        text = text.encode('ascii', 'ignore').decode('ascii')

        wav = tts_say(text, voice_id=guest.voice_id)
        if not wav:
            return None

        return TalkSegment(
            type=TalkSegmentType.GUEST_TAKEOVER_OUTRO,
            wav_paths=[wav],
            duration_secs=max(5.0, len(text) * 0.08),
            text=text,
            persona_id="dj",
            secondary_persona_id=guest.id,
            reason=f"{guest.name} handing back",
        )
    except Exception as e:
        print(f"talk_segments: guest outro failed: {e}")
        return None


# ---- Decision Logic ----

# Track counters for segment scheduling
_song_counter = 0
_current_guest_takeover = None  # {"persona_id": str, "songs_remaining": int}

# Cooldown tracking: avoids repeating the same persona/type back-to-back
_recent_callers: list[str] = []       # last N caller persona IDs used
_recent_guests: list[str] = []        # last N guest persona IDs used
_recent_segment_types: list[str] = [] # last N segment types used


def _pick_caller(cooldown: int = DEFAULT_CALLER_COOLDOWN) -> Persona | None:
    """Pick a caller persona, avoiding recent repeats within cooldown window."""
    callers = get_enabled_caller_personas()
    if not callers:
        return None

    # Filter out recently used callers (within cooldown window)
    available = [c for c in callers if c.id not in _recent_callers[-cooldown:]]

    # If all callers are on cooldown, reset and use all
    if not available:
        available = callers

    # Weight: give lower weight to callers that were used more recently
    weights = []
    for c in available:
        if c.id in _recent_callers:
            weights.append(0.5)  # recently used but past cooldown — lower weight
        else:
            weights.append(1.0)

    caller = random.choices(available, weights=weights, k=1)[0]
    _recent_callers.append(caller.id)
    if len(_recent_callers) > 10:
        _recent_callers.pop(0)
    return caller


def _pick_guest(cooldown: int = DEFAULT_CALLER_COOLDOWN) -> Persona | None:
    """Pick a guest persona, avoiding recent repeats within cooldown window."""
    guests = get_guest_personas()
    if not guests:
        return None

    available = [g for g in guests if g.id not in _recent_guests[-cooldown:]]
    if not available:
        available = guests

    guest = random.choice(available)
    _recent_guests.append(guest.id)
    if len(_recent_guests) > 10:
        _recent_guests.pop(0)
    return guest


def get_next_talk_segment(
    settings: dict | None = None,
    mood_state: str = "neutral",
    caller_mode: str = "influences_queue",
    next_song: dict | None = None,
) -> TalkSegment | None:
    """Decide which talk segment to play next, if any.
    
    This is the main entry point called by QueueManager during rebuild.
    
    Args:
        settings: Talk show settings dict (or None to fetch from DB)
        mood_state: Current mood for context
        caller_mode: "influences_queue" or "narrative_only"
        next_song: Dict with 'artist' and 'title' of the upcoming song,
                   so the talk segment can reference it.
        
    Returns:
        TalkSegment to play, or None if no segment should play
    """
    global _song_counter, _current_guest_takeover
    logger.info(f"get_next_talk_segment: counter={_song_counter}, guest_active={_current_guest_takeover is not None}")

    if settings is None:
        try:
            from db import database
            enabled = database.get_setting("talk_segments_enabled") or "false"
            if enabled.lower() != "true":
                return None
            settings = {
                "talk_caller_enabled": database.get_setting("talk_caller_enabled") or "true",
                "talk_monologue_enabled": database.get_setting("talk_monologue_enabled") or "true",
                "talk_guest_takeover_enabled": database.get_setting("talk_guest_takeover_enabled") or "true",
                "talk_frequency": int(database.get_setting("talk_frequency") or "4"),
                "talk_segment_cooldown": int(database.get_setting("talk_segment_cooldown") or str(DEFAULT_CALLER_COOLDOWN)),
            }
        except Exception as e:
            print(f"talk_segments: can't read settings: {e}")
            return None

    cooldown = settings.get("talk_segment_cooldown", DEFAULT_CALLER_COOLDOWN)

    # If a guest takeover is active, check if it's time to end it
    if _current_guest_takeover:
        _current_guest_takeover["songs_remaining"] -= 1
        if _current_guest_takeover["songs_remaining"] <= 0:
            outro = generate_guest_takeover_outro(_current_guest_takeover["persona_id"])
            _recent_guests.append(_current_guest_takeover["persona_id"])
            if len(_recent_guests) > 10:
                _recent_guests.pop(0)
            _current_guest_takeover = None
            return outro
        return None  # Keep playing songs during takeover

    # Increment counter
    _song_counter += 1
    frequency = settings.get("talk_frequency", 4)
    if _song_counter < frequency:
        return None  # Not time for talk yet

    # Time for a segment — reset counter
    _song_counter = 0

    # Decide which segment type to play (with cooldown awareness)
    segment_type = _pick_segment_type(settings, cooldown)
    if not segment_type:
        return None

    _recent_segment_types.append(segment_type)
    if len(_recent_segment_types) > 10:
        _recent_segment_types.pop(0)

    if segment_type == "caller":
        return generate_caller_segment(mood_state=mood_state, caller_mode=caller_mode, next_song=next_song, cooldown=cooldown)

    elif segment_type == "dj_monologue":
        return generate_dj_monologue(mood_state=mood_state)

    elif segment_type == "guest_takeover":
        guest = _pick_guest(cooldown)
        if not guest:
            return generate_caller_segment(mood_state=mood_state, caller_mode=caller_mode, next_song=next_song, cooldown=cooldown)
        intro = generate_guest_takeover_intro(guest.id)
        if not intro:
            return None
        # Set active takeover
        try:
            from db import database
            songs_per_set = int(database.get_setting("talk_guest_songs_per_set") or "4")
        except Exception:
            songs_per_set = 4
        _current_guest_takeover = {
            "persona_id": guest.id,
            "songs_remaining": songs_per_set,
        }
        return intro

    return None


def _pick_segment_type(settings: dict, cooldown: int = DEFAULT_CALLER_COOLDOWN) -> str | None:
    """Pick which segment type to play based on settings, weights, and cooldown."""
    enabled_types = []
    if settings.get("talk_caller_enabled", "true").lower() == "true":
        enabled_types.append("caller")
    if settings.get("talk_monologue_enabled", "true").lower() == "true":
        enabled_types.append("dj_monologue")
    if settings.get("talk_guest_takeover_enabled", "true").lower() == "true":
        enabled_types.append("guest_takeover")

    if not enabled_types:
        return None

    # Base weights: caller most common (50%), monologue (30%), guest takeover (20%)
    base_weights = {
        "caller": 0.5,
        "dj_monologue": 0.3,
        "guest_takeover": 0.2,
    }

    # Apply cooldown: reduce weight for recently used segment types
    weights = []
    for t in enabled_types:
        w = base_weights.get(t, 0.1)
        if t in _recent_segment_types[-cooldown:]:
            # Reduce weight for recently used types (halve it)
            w *= 0.5
        weights.append(w)

    # Normalize
    total = sum(weights)
    if total == 0:
        return random.choice(enabled_types)
    weights = [w / total for w in weights]

    return random.choices(enabled_types, weights=weights, k=1)[0]


def reset_talk_counters():
    """Reset talk segment counters and cooldowns (called when talk is disabled)."""
    global _song_counter, _current_guest_takeover, _recent_callers, _recent_guests, _recent_segment_types
    _song_counter = 0
    _current_guest_takeover = None
    _recent_callers = []
    _recent_guests = []
    _recent_segment_types = []


def dismiss_guest_takeover() -> str | None:
    """Dismiss the current guest and return the outro segment.
    
    Called when user votes guest off with thumbs-down.
    """
    global _current_guest_takeover
    if not _current_guest_takeover:
        return None
    persona_id = _current_guest_takeover["persona_id"]
    _current_guest_takeover = None
    _song_counter = 0
    outro = generate_guest_takeover_outro(persona_id)
    return outro


def get_active_guest_slot_bias() -> dict | None:
    """Get the slot_bias of the currently active guest takeover, if any.
    
    Called by QueueManager during rebuild to apply persona slot preferences.
    """
    global _current_guest_takeover
    if not _current_guest_takeover:
        return None
    guest = get_persona(_current_guest_takeover["persona_id"])
    if not guest:
        return None
    return guest.slot_bias
