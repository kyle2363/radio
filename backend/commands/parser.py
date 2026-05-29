"""NL command parser using LLM to convert natural language to structured actions."""

import json
import re

from llm import llm_chat

import config

# Direct keyword matching for common commands (fast, no Ollama needed)
_COMMAND_PATTERNS = [
    # Mood commands
    (r'\b(more|get) (energetic|electric|pumped|hyped|lit)\b', {"action": "mood_shift", "target_mood": "energetic"}),
    (r'\b(chill|relax|calm|laid.?back|cool down|slow down)\b', {"action": "mood_shift", "target_mood": "chill"}),
    (r'\b(focus|concentrate|study|work|deep work)\b', {"action": "mood_shift", "target_mood": "focus"}),
    (r'\b(melancholy|sad|down|blue|emo)\b', {"action": "mood_shift", "target_mood": "melancholy"}),
    (r'\b(discover|explore|new stuff|something new|variety)\b', {"action": "mood_shift", "target_mood": "discovery"}),
    (r'\bplay it (safe|cool|chill|smooth)\b', {"action": "mood_shift", "target_mood": "chill"}),
    (r'\bget (me )?(more|some) (experimental|weird|out there|different)\b', {"action": "mood_shift", "target_mood": "discovery"}),

    # Vibe lock
    (r'\b(vibe\s*lock|lock this|stay in this|keep this vibe|lock the vibe)\b', {"action": "vibe_lock"}),
    (r'\b(unlock|remove vibe lock|vibe lock off|vibe unlock)\b', {"action": "vibe_unlock"}),

    # Queue commands
    (r'\b(what|who|which) (is|was|coming up|next|playing)\b', {"action": "queue_info"}),
    (r'\b(replay|again|play again|one more time)\b', {"action": "replay"}),
    (r'\b(skip|next|pass|next song|skip this)\b', {"action": "skip"}),
    (r'\b(clear|empty|flush) (the |queue|playlist)\b', {"action": "clear_queue"}),

    # Exploration
    (r'\b(more|get more|way more|really) (experimental|adventurous|wild|risky|out there)\b', {"action": "exploration", "level": "high"}),
    (r'\b(experimental|adventurous|wild|risky|out there)\b', {"action": "exploration", "level": "medium"}),
    (r'\b(safe|conservative|familiar|comfortable|cozy|predictable)\b', {"action": "exploration", "level": "low"}),
    (r'\b(less|decrease|reduce) exploration\b', {"action": "exploration", "level": "low"}),

    # Genre/artist
    (r'\b(more|play more) (\w+(?:[\s-]+\w+)?)\b.*\b(jazz|rock|pop|hip.?hop|rap|classical|electronic|ambient|lo.?fi|soul|funk|disco|country|folk|metal|punk|indie|r\b)\b', {"action": "genre_filter", "genre": None}),
    (r'\b(more|play more) (\w+(?:[\s-]+\w+)*)\b', {"action": "artist_filter", "artist": None}),
    (r'\b(clear|remove|reset) (filters?|genre|artist)\b', {"action": "clear_filters"}),

    # Song info
    (r'\b(why|because) (this|this song|this track|this one)\b', {"action": "why_this_song"}),
    (r'\b(tell me about|what is|info about|about this)\b', {"action": "song_info"}),

    # Play specific song
    (r'\b(play|start) (.+?)(?:\.|!|\?|$)', {"action": "play_song", "query": None}),

    # Stop
    (r'\b(stop|pause|skip|quit|exit|halt)\b', {"action": "stop"}),

    # Help
    (r'\b(help|commands|what can you do|options)\b', {"action": "help"}),
]

# Mood keywords for fuzzy matching
_MOOD_KEYWORDS = {
    "energetic": ["energetic", "electric", "pumped", "hyped", "lit", "intense", "powerful", "driving", "upbeat", "fast"],
    "chill": ["chill", "relax", "calm", "laid-back", "cool", "smooth", "mellow", "soft", "gentle", "peaceful"],
    "focus": ["focus", "concentrate", "study", "work", "deep", "ambient", "minimal", "repetitive", "instrumental"],
    "melancholy": ["melancholy", "sad", "down", "blue", "emo", "somber", "dark", "heavy", "gloomy", "wistful"],
    "discovery": ["discover", "explore", "new", "variety", "experimental", "weird", "different", "unique", "novel"],
}


def _match_direct(text: str) -> dict | None:
    """Try to match the command against direct keyword patterns."""
    lower = text.lower().strip()

    # Check mood keywords (highest priority)
    for mood, keywords in _MOOD_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lower:
                return {"action": "mood_shift", "target_mood": mood}

    # Check other direct patterns
    for pattern, action in _COMMAND_PATTERNS:
        if re.search(pattern, lower):
            result = dict(action)
            # Fill in captured genre/artist groups
            if "genre" in result:
                genre_match = re.search(r'\b(jazz|rock|pop|hip.?hop|rap|classical|electronic|ambient|lo.?fi|soul|funk|disco|country|folk|metal|punk|indie)\b', lower)
                if genre_match:
                    result["genre"] = genre_match.group(1)
            if "artist" in result:
                artist_match = re.search(r'\bplay more (\w+(?:[\s-]+\w+)*)\b', lower)
                if artist_match:
                    result["artist"] = artist_match.group(1)
            return result

    # Last-resort genre detection (vague, no specific action matched)
    genre_match = re.search(r'\b(jazz|rock|pop|hip.?hop|rap|classical|electronic|ambient|lo.?fi|soul|funk|disco|country|folk|metal|punk|indie)\b', lower)
    if genre_match:
        return {"action": "genre_filter", "genre": genre_match.group(1)}

    return None


def _build_context(current_song: dict, mood_state: str, queue: list, archetype: str) -> str:
    """Build context string for Ollama prompt."""
    ctx = f"Mood: {mood_state}\n"
    if current_song:
        ctx += f"Current: {current_song.get('artist', '?')} - {current_song.get('title', '?')}\n"
    if queue:
        next_song = queue[0] if isinstance(queue, list) else None
        if next_song:
            ctx += f"Next: {next_song.get('artist', '?')} - {next_song.get('title', '?')}\n"
    if archetype:
        ctx += f"Archetype: {archetype}\n"
    return ctx


def _call_ollama(text: str, context: str) -> dict:
    """Use Ollama to parse the command."""
    prompt = f"""You are a radio station command parser. Convert the user's natural language into a structured JSON action.

Context:
{context}

User command: "{text}"

Available actions:
- mood_shift: {{action: "mood_shift", target_mood: "energetic"|"chill"|"focus"|"melancholy"|"discovery"}}
- vibe_lock: {{action: "vibe_lock"}}
- vibe_unlock: {{action: "vibe_unlock"}}
- queue_info: {{action: "queue_info"}}
- replay: {{action: "replay"}}
- skip: {{action: "skip"}}
- clear_queue: {{action: "clear_queue"}}
- exploration: {{action: "exploration", level: "low"|"medium"|"high"}}
- genre_filter: {{action: "genre_filter", genre: "jazz"|"rock"|"pop"|"hip-hop"|...}}
- play_song: {{action: "play_song", query: "artist or song name"}}
- why_this_song: {{action: "why_this_song"}}
- song_info: {{action: "song_info"}}
- stop: {{action: "stop"}}
- help: {{action: "help"}}

Rules:
- Return ONLY valid JSON, no preamble, no markdown, no code blocks
- If the command is unclear, return {{action: "help"}}
- Match the intent as closely as possible

Return JSON:"""

    response = llm_chat().chat(
        model=None,
        messages=[{"role": "user", "content": prompt}],
        options=config.OLLAMA_CHAT_OPTIONS,
        think=False,
    )
    raw = response["message"]["content"].strip()
    # Strip markdown code blocks if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    # Strip quotes
    raw = raw.strip('"\'')
    return json.loads(raw)


def parse_command(text: str, context: dict) -> dict:
    """Parse a natural language command into a structured action.

    Args:
        text: The user's command string
        context: Dict with keys: current_song, mood_state, queue, archetype

    Returns:
        Dict with keys: action, explanation (and action-specific fields)
    """
    # Try direct matching first (fast, no Ollama)
    direct = _match_direct(text)
    if direct:
        action = direct["action"]
        explanation_map = {
            "mood_shift": f"Shifting mood to {direct.get('target_mood', 'unknown')} vibe",
            "vibe_lock": "Locking current vibe",
            "vibe_unlock": "Unlocking vibe lock",
            "queue_info": "Showing queue info",
            "replay": "Replaying current song",
            "skip": "Skipping to next song",
            "clear_queue": "Clearing the queue",
            "exploration": f"Exploration set to {direct.get('level', 'medium')}",
            "genre_filter": f"Filtering for {direct.get('genre', 'unknown')} genre",
            "stop": "Stopping playback",
            "help": "Showing available commands",
        }
        result = dict(direct)
        result["explanation"] = explanation_map.get(action, f"Command: {text}")
        return result

    # Fall back to Ollama for complex/unclear commands
    try:
        ollama_result = _call_ollama(text, _build_context(
            context.get("current_song"),
            context.get("mood_state", "neutral"),
            context.get("queue", []),
            context.get("archetype", ""),
        ))
        explanation = ollama_result.pop("explanation", f"Interpreted: {text}")
        ollama_result["explanation"] = explanation
        return ollama_result
    except (json.JSONDecodeError, Exception) as e:
        print(f"Command parsing failed: {e}")
        return {
            "action": "help",
            "explanation": f"Could not understand: '{text}'. Try: 'more energetic', 'chill vibes', 'vibe lock', 'skip', 'replay', 'play more jazz'",
        }
