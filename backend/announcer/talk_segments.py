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
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import config
from llm import llm_chat
from announcer.tts import tts_say
from announcer.nonverbal import inject_nonverbal_tags
from announcer.personas import PERSONAS, Persona, get_persona, get_guest_personas, get_caller_personas, get_enabled_caller_personas
import threading
import logging
logger = logging.getLogger(__name__)

# Lock for module-level mutable state (_song_counter, _current_guest_takeover,
# cooldown tracking lists).  get_next_talk_segment() runs in background threads
# from QueueManager._rebuild_queue() and these globals must not be mutated
# concurrently.
_talk_lock = threading.RLock()

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

def _get_recent_songs_for_context(count: int = 3, recent_context: list[dict] | None = None) -> list[dict]:
    """Get recently played songs for LLM context.

    Args:
        count: Number of recent songs to return.
        recent_context: Pre-fetched context from the PlaybackContext snapshot.
                        When provided, bypasses the DB query entirely.  This
                        ensures the talk segment uses the same context that
                        was frozen at rebuild start, rather than what the
                        queue_history table says now.
    """
    if recent_context is not None:
        return recent_context[:count]
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

CALL_REASONS_WEIGHTED = {
    "skip_defense": {
        "reason": "a song they love just got skipped and they need to defend it",
        "weight_when": {"skip_streak_gt": 0},
    },
    "mood_reaction": {
        "reason": "the mood just shifted and it reminded them of something",
        "weight_when": {"mood_changed_recently": True},
    },
    "late_night_connection": {
        "reason": "it's late and the music is the only company they have right now",
        "weight_when": {"hour_ge": 22},
    },
    "driving_companion": {
        "reason": "they've been driving for hours and the playlist has been perfect company",
        "weight_when": {"session_minutes_gt": 30},
    },
    "lyric_hit": {
        "reason": "a lyric just hit them out of nowhere and they needed to tell someone",
        "weight_when": None,
    },
    "person_reminder": {
        "reason": "a song reminded them of a specific person — they want to say so",
        "weight_when": None,
    },
    "pushback": {
        "reason": "they want to gently push back on the direction the show is going",
        "weight_when": None,
    },
    "worked_up_nerve": {
        "reason": "they've been listening all night and finally worked up the nerve to call",
        "weight_when": {"session_minutes_gt": 15},
    },
    "going_through_something": {
        "reason": "they're going through something and the music is weirdly, precisely right",
        "weight_when": None,
    },
    "just_want_to_be_heard": {
        "reason": "they just want to be heard at this hour — the music is an excuse",
        "weight_when": None,
    },
}

CALLER_OPENINGS = [
    "mid_thought",
    "nervous",
    "already_talking",
    "raw_reaction",
    "quiet_reflection",
]


def _pick_caller_reason(session_state: dict | None) -> str:
    """Pick a call reason weighted by session context and last segment mood.
    
    Each reason has a base weight of 1.0. Contextual conditions
    add bonus weight when they match (up to +2.0). Pure random
    as baseline, skewed by what's happening in the session.
    Cross-segment mood tracking prevents same-tone calls back-to-back.
    """
    global _last_segment_mood
    if not session_state:
        session_state = {}
    skip_streak = session_state.get("skip_streak", 0)
    mood_changed = session_state.get("mood_changed_recently", False)
    hour = session_state.get("hour", 12)
    session_mins = session_state.get("session_duration_minutes", 0)

    with _talk_lock:
        last_mood = _last_segment_mood or ""

    candidates = []
    for key, entry in CALL_REASONS_WEIGHTED.items():
        weight = 1.0
        cond = entry.get("weight_when")
        if cond:
            if cond.get("skip_streak_gt", -1) >= 0 and skip_streak > cond["skip_streak_gt"]:
                weight += 2.0
            if cond.get("mood_changed_recently") and mood_changed:
                weight += 1.5
            if cond.get("hour_ge", 99) <= hour:
                weight += 1.5
            if cond.get("session_minutes_gt", -1) >= 0 and session_mins > cond["session_minutes_gt"]:
                weight += 1.0

        # Cross-segment mood continuity: if last segment was melancholy,
        # boost reasons that lean into it; if energetic, boost opposition
        if last_mood == "melancholy" and key in ("lyric_hit", "person_reminder", "late_night_connection"):
            weight += 1.0
        elif last_mood == "energetic" and key in ("pushback", "mood_reaction", "skip_defense"):
            weight += 1.0

        candidates.append((entry["reason"], weight))

    reasons = [c[0] for c in candidates]
    weights = [c[1] for c in candidates]
    return random.choices(reasons, weights=weights, k=1)[0]


def _pick_caller_opening() -> str:
    """Pick a caller opening style."""
    return random.choice(CALLER_OPENINGS)


CALLER_PROMPT_TEMPLATE = """You are writing a short phone-in exchange for a radio show.

Context:
- DJ persona: {dj_personality}
- Caller name: {caller_name}
- Caller personality: {caller_personality}
- Why they called tonight: {call_reason}
- Caller opening style: {caller_opening_style}
- Recently played: {recent_songs_str}
- Recent lyrics: {recent_lyrics_str}
- Current mood: {mood_state}
- Time: {greeting}
- Next song coming up: {next_song_str}

Generate a {length_desc} phone call exchange between the DJ and the caller.

Return valid JSON only with this exact structure:
{{
{json_fields}    "mood_shift": null,
    "energy_direction": null
}}

Rules:
- The reason they called shapes everything: their energy, what they lead with, what they leave unsaid
- The opening style must be audible in the first line — it defines how the caller enters the conversation
- Opening style '{caller_opening_style}' means:
  * mid_thought: caller is already mid-sentence, continuing a thought ("...and that's when I realized")
  * nervous: caller is hesitant, says "um", "hey is this...", "sorry to bother you"
  * already_talking: caller jumps in without introduction ("No no no, but listen—")
  * raw_reaction: caller blurts out an emotional reaction ("Oh MAN, that track—")
  * quiet_reflection: caller starts slow and thoughtful ("I've been sitting here thinking about...")
- Caller MUST reference at least one actual song or artist from what's been playing
- Caller should echo a specific lyric fragment naturally (not as a direct quote)
- The DJ should respond in character — warm but not identical each time
- Use filler words, restarts, and incomplete thoughts where natural
- The caller_request should feel like something they arrived at, not something they planned to say
- mood_shift: set to "energetic", "chill", "focus", "melancholy" or null
- energy_direction: set to "up", "down" or null
- If next song is provided, the dj_closer should tease it as if it's a natural response to the call
- If no next song info, dj_closer should be generic ("Stay tuned" / "More great music ahead")
- NO markdown, NO backticks, NO extra text — just the JSON object"""


def generate_caller_segment(
    mood_state: str = "neutral",
    caller_mode: str = "influences_queue",
    next_song: dict | None = None,
    cooldown: int = DEFAULT_CALLER_COOLDOWN,
    recent_context: list[dict] | None = None,
    session_state: dict | None = None,
) -> TalkSegment | None:
    """Generate a caller phone-in segment between songs.
    
    Makes one LLM call that produces both DJ and caller dialogue.
    Each line is rendered with the appropriate persona's voice.
    
    Args:
        mood_state: Current mood state for context
        caller_mode: "influences_queue" or "narrative_only"
        next_song: Dict with 'artist' and 'title' of the upcoming song
        cooldown: Number of segments before a caller can be reused
        recent_context: Pre-fetched recent songs from PlaybackContext snapshot.
                        Passed to _get_recent_songs_for_context to avoid
                        querying queue_history.
        session_state: Dict with skip_streak, mood_changed_recently, hour,
                       session_duration_minutes for weighting call reasons.
        
    Returns:
        TalkSegment with WAV paths, or None on failure
    """
    dj = get_persona("dj")
    caller = _pick_caller(cooldown)
    if not caller:
        return None

    call_reason = _pick_caller_reason(session_state)
    caller_opening = _pick_caller_opening()

    recent = _get_recent_songs_for_context(3, recent_context=recent_context)
    recent_str = "; ".join([f"{s['artist']} - {s['title']}" for s in recent]) or "no songs yet"
    recent_lyrics_str = ""
    lyrical_songs = [s for s in recent if s.get("lyrics_snippet")]
    if lyrical_songs:
        parts = []
        for s in lyrical_songs:
            parts.append(f'"{s["title"]}" lyrics: "{s["lyrics_snippet"]}..."')
        recent_lyrics_str = "\n".join(parts)

    hour = datetime.now().hour
    greeting = _build_greeting_caller(hour)

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
        call_reason=call_reason,
        caller_opening_style=caller_opening,
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
        content = response.content.strip()

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

        # Track last segment mood for cross-segment arc
        global _last_segment_mood
        with _talk_lock:
            _last_segment_mood = mood_shift if mood_shift else mood_state

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
            reason=f"{caller.name} calling in ({call_reason[:40]}...)",
        )
    except Exception as e:
        print(f"talk_segments: caller generation failed: {e}")
        return None


# ---- DJ Monologue ----

MONOLOGUE_TYPES = {
    "memory": {
        "instruction": "A specific personal memory or story that one of tonight's songs triggered. Be concrete and vivid — a place, a person, a moment. The music is the doorway in.",
        "tone": "nostalgic, specific, unhurried",
    },
    "artist_dive": {
        "instruction": "Pick one artist from tonight and make the case for what makes them singular. Use specific lyric fragments as evidence, woven into your argument — not recited.",
        "tone": "passionate, analytical, fan-like",
    },
    "late_night_confession": {
        "instruction": "Something honest about this particular hour — a feeling the music stirred, or something you've been sitting with. Vulnerable but not melodramatic.",
        "tone": "quiet, intimate, slightly unguarded",
    },
    "listener_address": {
        "instruction": "Speak directly to whoever is listening right now — as if it's just one person. What do you want them to feel before the next song starts?",
        "tone": "warm, direct, unhurried",
    },
    "lyric_meditation": {
        "instruction": "Take one or two specific lyric fragments from tonight and unpack them — what they mean to you, why they land, what they say about the artist or the moment.",
        "tone": "reflective, close-reading, poetic without being pretentious",
    },
    "arc_trace": {
        "instruction": "Map the emotional arc of tonight so far — where it started, where it's arrived, where it might be going. Make the listener feel they've been on a journey.",
        "tone": "observational, winding, slightly cinematic",
    },
}

MONOLOGUE_PROMPT_TEMPLATE = """You are a late-night radio DJ delivering a personal monologue.

Context:
- Session duration: {session_minutes} minutes
- Songs played so far: {song_count}
- Top artists tonight: {top_artists}
- Current mood: {mood_state}
- Time: {greeting}
- Trigger: {trigger_reason}
- Recent lyrics to draw from: {recent_lyrics}

Monologue type: {monologue_type}
What this means: {monologue_instruction}
Tone to aim for: {monologue_tone}

Write a 60-90 second spoken monologue.

Rules:
- Write for one listener, not a crowd
- Use natural spoken rhythm: incomplete sentences, "you know", "I mean", trailing thoughts
- DO NOT open with: "You know,", "It's [time]", "Tonight", or "Welcome back"
- Weave in lyric references naturally — don't quote them like recitation
- End with something that makes the next song feel like it had to come next
- Avoid these opening patterns used recently: {forbidden_openings}

No quotes, no labels — just the spoken text."""


def _pick_monologue_type() -> tuple[str, dict]:
    """Pick a monologue type, avoiding the last 3 types used.
    
    Also deprioritizes types that clash with the last segment's mood
    to prevent emotional stacking (e.g. two melancholy segments in a row).
    
    Thread-safe: guarded by _talk_lock.
    """
    with _talk_lock:
        available = {k: v for k, v in MONOLOGUE_TYPES.items() if k not in _recent_monologue_types}
        if not available:
            available = dict(MONOLOGUE_TYPES)

        # Deprioritize types that clash with last segment mood
        weights = {}
        for key, type_dict in available.items():
            weight = 1.0
            last = _last_segment_mood or ""
            if last == "melancholy" and key in ("memory", "late_night_confession"):
                weight = 0.3
            elif last == "energetic" and key == "arc_trace":
                weight = 0.4
            elif last == "focus" and key == "arc_trace":
                weight = 0.5
            weights[key] = weight

        keys = list(available.keys())
        w = [weights[k] for k in keys]
        chosen_key = random.choices(keys, weights=w, k=1)[0]
        _recent_monologue_types.append(chosen_key)
        return chosen_key, available[chosen_key]


def generate_dj_monologue(
    mood_state: str = "neutral",
    trigger_reason: str = "regular interlude",
    recent_context: list[dict] | None = None,
) -> TalkSegment | None:
    """Generate a longer DJ monologue segment.
    
    This is a single-voice segment with richer context.

    Args:
        recent_context: Pre-fetched recent songs from PlaybackContext snapshot.
                        Passed to _get_recent_songs_for_context to avoid
                        querying queue_history.
    """
    dj = get_persona("dj")
    session = _get_session_context()
    recent = _get_recent_songs_for_context(3, recent_context=recent_context)
    top_artists = session.get("top_artists", [])
    top_str = ", ".join(top_artists) if top_artists else "a great mix of music"

    # Pick monologue type
    mono_key, mono_type = _pick_monologue_type()

    # Build forbidden openings string
    with _talk_lock:
        forbidden = "; ".join(_recent_monologue_openings) or "none yet"

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
        monologue_type=mono_key.replace("_", " ").title(),
        monologue_instruction=mono_type["instruction"],
        monologue_tone=mono_type["tone"],
        forbidden_openings=forbidden,
    )

    logger.info(f"generate_dj_monologue: calling LLM (mood={mood_state}, type={mono_key})")
    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options=config.OLLAMA_CHAT_OPTIONS,
            think=False,
        )
        logger.info("generate_dj_monologue: LLM returned, rendering TTS...")
        text = response.content.strip()
        # Clean up
        text = text.strip('"\'').strip()
        text = text.encode('ascii', 'ignore').decode('ascii')

        if not text:
            return None

        # Track opening for future forbidden list
        from announcer.nonverbal import inject_nonverbal_tags_monologue
        opening = " ".join(text.split()[:6])
        with _talk_lock:
            _recent_monologue_openings.append(opening)

        # Track last segment mood for cross-segment arc
        global _last_segment_mood
        with _talk_lock:
            _last_segment_mood = mood_state

        # Apply restrained nonverbal tags
        text = inject_nonverbal_tags_monologue(text)

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
            reason=f"DJ monologue ({mono_key.replace('_', ' ')})",
        )
    except Exception as e:
        print(f"talk_segments: monologue failed: {e}")
        return None


# ---- Guest Takeover Segments ----

GUEST_INTRO_STYLES = [
    "jump straight into the energy — no preamble",
    "build anticipation slowly before revealing what's coming",
    "open with a personal confession or memory",
    "challenge the listener to stay with you",
    "drop a cryptic hook that the set will pay off",
]

GUEST_INTRO_PROMPT_TEMPLATE = """You are {guest_name}, a guest DJ taking over for the next few songs.
Your personality: {guest_personality}

Context:
- Time of day: {greeting}
- Last song that just played: {last_song}
- The vibe you're bringing: {mood_state}
- Songs coming up in your set: {upcoming_songs_hint}

Write a 15-20 second spoken intro for your takeover.

Rules:
- Reference the transition from what just played — agree with it, contrast it, or riff off it
- Hint at the vibe without listing track names
- Opening style this time: {intro_style}
- DO NOT open with your own name or "Hey everyone" or "What's up"
- Sound like yourself, not a generic hype voice
- No quotes, no labels, no stage directions — just the spoken words"""

GUEST_OUTRO_PROMPT_TEMPLATE = """You are {guest_name}, handing the show back after your set.
Your personality: {guest_personality}

Context:
- Songs you just played: {played_songs}
- Main DJ: {dj_name}
- How the set felt: {set_mood}

Write a 10-15 second spoken handoff.
- Reference at least one moment or track from your set
- Feel like a natural ending, not a formal sign-off
- Let your personality come through in the final seconds
- No quotes, no labels — just the spoken words"""


def _describe_upcoming_vibe(songs: list[dict]) -> str:
    """Generate a short vibe description from a list of upcoming songs."""
    if not songs:
        return "eclectic energy"
    energies = [s.get("energy", 0.5) for s in songs if isinstance(s.get("energy"), (int, float))]
    avg_energy = sum(energies) / len(energies) if energies else 0.5
    if avg_energy > 0.7:
        return "high-energy bangers and uptempo tracks"
    elif avg_energy > 0.45:
        return "a balanced mix of energy and groove"
    else:
        return "chill, atmospheric vibes"


def _build_greeting(hour: int) -> str:
    if hour < 6:
        return "the early hours"
    elif hour < 12:
        return "the morning"
    elif hour < 17:
        return "the afternoon"
    else:
        return "the evening"


def _build_greeting_caller(hour: int) -> str:
    """Build a greeting for caller segments (more conversational)."""
    if hour < 6:
        return "It's the early hours"
    elif hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    else:
        return "Good evening"


def generate_guest_takeover_intro(
    guest_persona_id: str,
    last_song: dict | None = None,
    upcoming_songs: list[dict] | None = None,
    mood_state: str = "neutral",
) -> TalkSegment | None:
    """Generate a guest DJ takeover intro announcement with musical context."""
    guest = get_persona(guest_persona_id)
    dj = get_persona("dj")
    if not guest:
        return None

    intro_style = random.choice(GUEST_INTRO_STYLES)
    hour = datetime.now().hour
    greeting = _build_greeting(hour)

    last_song_str = f"{last_song.get('artist', 'Unknown')} - {last_song.get('title', 'Unknown')}" if last_song else "silence"
    upcoming_hint = _describe_upcoming_vibe(upcoming_songs or [])

    prompt = GUEST_INTRO_PROMPT_TEMPLATE.format(
        guest_name=guest.name,
        guest_personality=guest.personality,
        greeting=greeting,
        last_song=last_song_str,
        mood_state=mood_state,
        upcoming_songs_hint=upcoming_hint,
        intro_style=intro_style,
    )

    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options={**config.OLLAMA_CHAT_OPTIONS, "temperature": 0.5},
            think=False,
        )
        text = response.content.strip()
        text = text.strip('"\'').strip()
        text = text.encode('ascii', 'ignore').decode('ascii')

        wav = tts_say(text, voice_id=guest.voice_id, speed=guest.speed_base)
        if not wav:
            return None

        # Track last segment mood
        global _last_segment_mood
        with _talk_lock:
            _last_segment_mood = mood_state

        return TalkSegment(
            type=TalkSegmentType.GUEST_TAKEOVER_INTRO,
            wav_paths=[wav],
            duration_secs=max(8.0, len(text) * 0.08),
            text=text,
            persona_id="dj",
            secondary_persona_id=guest.id,
            guest_persona_id=guest.id,
            reason=f"{guest.name} taking over ({intro_style[:30]}...)",
        )
    except Exception as e:
        print(f"talk_segments: guest intro failed: {e}")
        return None


def generate_guest_takeover_outro(
    guest_persona_id: str,
    played_songs: list[dict] | None = None,
    set_mood: str | None = None,
) -> TalkSegment | None:
    """Generate a guest DJ takeover outro (handing back to main DJ)."""
    guest = get_persona(guest_persona_id)
    dj = get_persona("dj")
    if not guest:
        return None

    played_songs_str = "; ".join(
        [f"{s.get('artist', 'Unknown')} - {s.get('title', 'Unknown')}" for s in (played_songs or [])]
    ) or "a great set of tracks"
    dj_name = dj.name if dj else "the main DJ"
    set_mood_str = set_mood or "energetic and fresh"

    prompt = GUEST_OUTRO_PROMPT_TEMPLATE.format(
        guest_name=guest.name,
        guest_personality=guest.personality,
        played_songs=played_songs_str,
        dj_name=dj_name,
        set_mood=set_mood_str,
    )

    try:
        response = llm_chat().chat(
            model=None,
            messages=[{"role": "user", "content": prompt}],
            options={**config.OLLAMA_CHAT_OPTIONS, "temperature": 0.5},
            think=False,
        )
        text = response.content.strip()
        text = text.strip('"\'').strip()
        text = text.encode('ascii', 'ignore').decode('ascii')

        wav = tts_say(text, voice_id=guest.voice_id, speed=guest.speed_base)
        if not wav:
            return None

        # Track last segment mood (outro is neutral handoff)
        global _last_segment_mood
        with _talk_lock:
            _last_segment_mood = "neutral"

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
_current_guest_takeover = None  # {"persona_id": str, "songs_remaining": int, "played_songs": list[dict]}

# Cooldown tracking: avoids repeating the same persona/type back-to-back
_recent_callers: list[str] = []       # last N caller persona IDs used
_recent_guests: list[str] = []        # last N guest persona IDs used
_recent_segment_types: list[str] = [] # last N segment types used
_recent_monologue_openings = deque(maxlen=4)  # first 6 words of generated monologues
_recent_monologue_types = deque(maxlen=3)     # last N monologue type keys
_last_segment_mood = None                    # mood from the most recent segment


def _pick_caller(cooldown: int = DEFAULT_CALLER_COOLDOWN) -> Persona | None:
    """Pick a caller persona, avoiding recent repeats within cooldown window.
    
    Thread-safe: guarded by _talk_lock.
    """
    with _talk_lock:
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
    """Pick a guest persona, avoiding recent repeats within cooldown window.
    
    Thread-safe: guarded by _talk_lock.
    """
    with _talk_lock:
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
    recent_context: list[dict] | None = None,
    last_song: dict | None = None,
    session_state: dict | None = None,
) -> TalkSegment | None:
    """Decide which talk segment to play next, if any.
    
    This is the main entry point called by QueueManager during rebuild.
    Thread-safe: module-level globals (_song_counter, _current_guest_takeover,
    cooldown lists) are guarded by _talk_lock.
    
    Args:
        settings: Talk show settings dict (or None to fetch from DB)
        mood_state: Current mood for context
        caller_mode: "influences_queue" or "narrative_only"
        next_song: Dict with 'artist' and 'title' of the upcoming song,
                   so the talk segment can reference it.
        recent_context: Pre-fetched recent songs from PlaybackContext snapshot.
                        Passed through to generator functions so they use the
                        frozen context rather than a live queue_history query.
        last_song: Dict with 'artist' and 'title' of the song just played,
                   used by guest intros for musical context.
        session_state: Dict with skip_streak, mood_changed_recently, hour,
                       session_duration_minutes for weighting call reasons.
        
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

    # --- Decision phase (under lock, fast — no LLM calls) ---
    with _talk_lock:
        # If a guest takeover is active, check if it's time to end it
        if _current_guest_takeover:
            _current_guest_takeover["songs_remaining"] -= 1
            if _current_guest_takeover["songs_remaining"] <= 0:
                played = _current_guest_takeover.get("played_songs", [])
                outro = generate_guest_takeover_outro(
                    _current_guest_takeover["persona_id"],
                    played_songs=played,
                    set_mood=mood_state,
                )
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

    # --- Generation phase (outside lock, may do LLM calls) ---
    if segment_type == "caller":
        with _talk_lock:
            caller = _pick_caller(cooldown)
        if not caller:
            return None
        return generate_caller_segment(
            mood_state=mood_state, caller_mode=caller_mode,
            next_song=next_song, cooldown=cooldown,
            recent_context=recent_context,
            session_state=session_state,
        )

    elif segment_type == "dj_monologue":
        return generate_dj_monologue(mood_state=mood_state, recent_context=recent_context)

    elif segment_type == "guest_takeover":
        with _talk_lock:
            guest = _pick_guest(cooldown)
        if not guest:
            return generate_caller_segment(
                mood_state=mood_state, caller_mode=caller_mode,
                next_song=next_song, cooldown=cooldown,
                recent_context=recent_context,
                session_state=session_state,
            )
        intro = generate_guest_takeover_intro(
            guest.id,
            last_song=last_song,
            upcoming_songs=[next_song] if next_song else [],
            mood_state=mood_state,
        )
        if not intro:
            return None
        # Set active takeover (under lock, fast)
        with _talk_lock:
            try:
                from db import database
                songs_per_set = int(database.get_setting("talk_guest_songs_per_set") or "4")
            except Exception:
                songs_per_set = 4
            _current_guest_takeover = {
                "persona_id": guest.id,
                "songs_remaining": songs_per_set,
                "played_songs": [],
            }
        return intro

    return None


def _pick_segment_type(settings: dict, cooldown: int = DEFAULT_CALLER_COOLDOWN) -> str | None:
    """Pick which segment type to play based on settings, weights, and cooldown.
    
    Thread-safe: guarded by _talk_lock.
    """
    with _talk_lock:
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
    """Reset talk segment counters and cooldowns (called when talk is disabled).
    
    Thread-safe: guarded by _talk_lock.
    """
    global _song_counter, _current_guest_takeover, _recent_callers, _recent_guests, _recent_segment_types
    global _recent_monologue_openings, _recent_monologue_types, _last_segment_mood
    with _talk_lock:
        _song_counter = 0
        _current_guest_takeover = None
        _recent_callers = []
        _recent_guests = []
        _recent_segment_types = []
        _recent_monologue_openings = deque(maxlen=4)
        _recent_monologue_types = deque(maxlen=3)
        _last_segment_mood = None


def dismiss_guest_takeover() -> str | None:
    """Dismiss the current guest and return the outro segment.
    
    Called when user votes guest off with thumbs-down.
    Thread-safe: guarded by _talk_lock.
    """
    global _current_guest_takeover, _song_counter
    with _talk_lock:
        if not _current_guest_takeover:
            return None
        persona_id = _current_guest_takeover["persona_id"]
        played = _current_guest_takeover.get("played_songs", [])
        _current_guest_takeover = None
        _song_counter = 0
    # Generate outro outside lock (may do LLM call)
    outro = generate_guest_takeover_outro(persona_id, played_songs=played)
    return outro


def get_active_guest_slot_bias() -> dict | None:
    """Get the slot_bias of the currently active guest takeover, if any.
    
    Called by QueueManager during rebuild to apply persona slot preferences.
    Thread-safe: guarded by _talk_lock.
    """
    global _current_guest_takeover
    with _talk_lock:
        if not _current_guest_takeover:
            return None
        guest = get_persona(_current_guest_takeover["persona_id"])
        if not guest:
            return None
        return guest.slot_bias


def get_active_dj_effect_preset() -> str | None:
    """Get the DJ effect preset name from the currently active guest, if any.

    Returns the preset name (e.g. "slowed_reverb") if the active guest
    takeover persona has a "dj_effect" key in their slot_bias.
    Returns None if no active guest or the guest doesn't use DJ effects.
    Thread-safe: guarded by _talk_lock.
    """
    global _current_guest_takeover
    with _talk_lock:
        if not _current_guest_takeover:
            return None
        guest = get_persona(_current_guest_takeover["persona_id"])
        if not guest or not guest.slot_bias:
            return None
        return guest.slot_bias.get("dj_effect")
