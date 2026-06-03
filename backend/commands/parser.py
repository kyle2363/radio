"""NL command parser using LLM to convert natural language to structured actions.

Supports two modes:
1. Regex fast path: common commands matched in <1ms, no LLM needed
2. Tool-calling LLM path: complex/multi-step commands via structured tool calls
"""

import json
import re

from llm import llm_chat

import config
from db import database

# ── Module-level counter for synthetic tool_call_ids (Ollama doesn't always emit them)
_tool_call_counter = 0


def _next_tool_id() -> str:
    global _tool_call_counter
    _tool_call_counter += 1
    return f"call_{_tool_call_counter}"


# ═══════════════════════════════════════════════════════════
#  TOOL DEFINITIONS (OpenAI tool schema)
# ═══════════════════════════════════════════════════════════

_COMMAND_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_songs",
            "description": "Search for songs in the library by artist, title, genre, energy/valence range. Use this when the user asks for specific songs, artists, genres, or moods.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free text search (matches title and artist)"},
                    "artist": {"type": "string", "description": "Artist name to filter by"},
                    "genre": {"type": "string", "description": "Genre tag to filter by (e.g. jazz, rock, electronic, ambient, lo-fi, soul, funk, indie)"},
                    "energy_min": {"type": "number", "description": "Minimum energy level 0.0-1.0"},
                    "energy_max": {"type": "number", "description": "Maximum energy level 0.0-1.0"},
                    "valence_min": {"type": "number", "description": "Minimum valence/mood 0.0-1.0"},
                    "valence_max": {"type": "number", "description": "Maximum valence/mood 0.0-1.0"},
                    "limit": {"type": "integer", "description": "Max results (default 10, max 50)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_queue",
            "description": "Add one or more songs to the playback queue by their song IDs. Use search_songs first to find the IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "song_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Array of song IDs to queue",
                    },
                    "position": {
                        "type": "string",
                        "enum": ["front", "back"],
                        "description": "'front' = play next, 'back' = add to end (default)",
                    },
                },
                "required": ["song_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_song",
            "description": "Play a specific song immediately by its ID. Use search_songs first to find the ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "song_id": {"type": "integer", "description": "The song ID to play"},
                },
                "required": ["song_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mood",
            "description": "Change the radio's mood state, which affects the type of songs selected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "enum": ["chill", "neutral", "energetic", "focus", "melancholy", "discovery"],
                        "description": "Target mood state",
                    },
                },
                "required": ["mood"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_vibe_lock",
            "description": "Lock or unlock the current song's vibe. When locked, the radio plays songs similar to the current one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True to lock vibe to current song, false to unlock",
                    },
                },
                "required": ["enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_filter",
            "description": "Set a genre or artist filter that biases the recommendation engine toward that type of music.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_type": {
                        "type": "string",
                        "enum": ["genre", "artist"],
                        "description": "What kind of filter to apply",
                    },
                    "value": {
                        "type": "string",
                        "description": "The genre name or artist name to filter by",
                    },
                },
                "required": ["filter_type", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_filters",
            "description": "Remove all active genre and artist filters so recommendations go back to normal.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_exploration",
            "description": "Control how adventurous the song selection is. Low = familiar songs, High = experimental picks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Exploration level",
                    },
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_queue",
            "description": "Remove all songs from the playback queue.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip",
            "description": "Skip the current song and play the next one.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause",
            "description": "Pause playback of the current song.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume",
            "description": "Resume playback after pausing.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "heart",
            "description": "Like or favorite the currently playing song.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "thumbs_down",
            "description": "Dislike the currently playing song so it plays less often.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_info",
            "description": "Get info about what's currently playing, mood state, and upcoming queue.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_sleep_timer",
            "description": "Set a timer to fade out and stop playback after a number of minutes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer", "description": "Number of minutes before sleep (5-120)"},
                },
                "required": ["minutes"],
            },
        },
    },
]


def _search_songs_tool(args: dict) -> str:
    """Execute search_songs tool call. Returns JSON string for LLM consumption."""
    limit = min(args.get("limit", 10), 50)
    results = database.search_songs_advanced(
        query=args.get("query"),
        artist=args.get("artist"),
        genre_tag=args.get("genre"),
        energy_min=args.get("energy_min"),
        energy_max=args.get("energy_max"),
        valence_min=args.get("valence_min"),
        valence_max=args.get("valence_max"),
        limit=limit,
        fuzzy=True,
    )
    summary = []
    for s in results:
        summary.append({
            "id": s["id"],
            "artist": s["artist"],
            "title": s["title"],
            "energy": s["energy"],
            "valence": s["valence"],
            "cluster_id": s["cluster_id"],
        })
    return json.dumps(summary, default=str)


# Tools that can be fully executed inside the parser (only need DB access).
_EXECUTABLE_TOOLS = {"search_songs"}

# Tools that are accumulated as pending actions for main.py to execute.
_ACTION_TOOLS = {
    "add_to_queue": "add_to_queue",
    "play_song": "play_song",
    "set_mood": "mood_shift",
    "set_vibe_lock": "vibe_lock",
    "set_filter": "set_filter",
    "clear_filters": "clear_filters",
    "set_exploration": "exploration",
    "clear_queue": "clear_queue",
    "skip": "skip",
    "pause": "pause",
    "resume": "resume",
    "heart": "heart",
    "thumbs_down": "thumbs_down",
    "set_sleep_timer": "sleep_timer",
}


def _execute_tool(tool_call) -> str | None:
    """Execute a tool call in the parser. Returns JSON result string or None if it's an action tool."""
    name = tool_call.function_name
    args = tool_call.arguments

    if name == "search_songs":
        return _search_songs_tool(args)
    if name == "get_current_info":
        return json.dumps(tool_call._context if hasattr(tool_call, '_context') else {})

    if name in _ACTION_TOOLS:
        return None  # Will be collected as pending action

    return json.dumps({"error": f"Unknown tool: {name}"})


def _tool_to_action(tool_call, context: dict) -> dict:
    """Convert a tool call that needs main.py execution into the expected action dict format."""
    name = tool_call.function_name
    args = tool_call.arguments
    action_name = _ACTION_TOOLS.get(name, name)

    result = {"action": action_name}

    if name == "add_to_queue":
        result["song_ids"] = args.get("song_ids", [])
        result["position"] = args.get("position", "back")
    elif name == "play_song":
        result["song_id"] = args.get("song_id")
    elif name == "set_mood":
        result["target_mood"] = args.get("mood", "neutral")
    elif name == "set_vibe_lock":
        result["enabled"] = args.get("enabled", True)
    elif name == "set_filter":
        ftype = args.get("filter_type", "")
        if ftype == "genre":
            result["genre"] = args.get("value", "")
        elif ftype == "artist":
            result["artist"] = args.get("value", "")
    elif name == "set_exploration":
        result["level"] = args.get("level", "medium")
    elif name == "set_sleep_timer":
        result["minutes"] = args.get("minutes", 30)

    return result


def _get_tool_system_prompt(context: dict) -> str:
    """Build system prompt for the tool-calling LLM."""
    ctx = context or {}
    song = ctx.get("current_song") or {}
    mood = ctx.get("mood_state", "neutral")
    arch = ctx.get("archetype", "")

    lines = [
        "You are a helpful radio station assistant. You help users control their music experience.",
        "",
        f"Current state: Mood={mood}",
        f"Now playing: {song.get('artist', '?')} - {song.get('title', '?')}" if song.get("id") else "Now playing: (nothing)",
    ]
    if arch:
        lines.append(f"Listener type: {arch}")

    lines.extend([
        "",
        "You have tools available. Use them to help the user:",
        "- If they want to play specific songs/artists: search_songs first, then use the IDs with add_to_queue or play_song",
        "- For complex requests (e.g. 'play 5 chill songs from Radiohead'): search with artist + valence/energy filters, then add_to_queue",
        "- For simple mood changes or playback controls: call the appropriate tool directly",
        "- If a search returns no results, try a broader search and tell the user what you found",
        "",
        "After calling the necessary tools, summarize what you did for the user in a friendly way.",
    ])
    return "\n".join(lines)


def _call_llm_with_tools(text: str, context: dict) -> dict:
    """Agentic loop: send message + tools to LLM, process tool calls, return actions + explanation."""
    messages = [
        {"role": "system", "content": _get_tool_system_prompt(context)},
        {"role": "user", "content": text},
    ]

    pending_actions = []
    max_iterations = 5

    for iteration in range(max_iterations):
        try:
            response = llm_chat().chat(
                model=None,
                messages=messages,
                options=config.OLLAMA_CHAT_OPTIONS,
                tools=_COMMAND_TOOLS,
                timeout=30,
            )
        except Exception as e:
            print(f"[parser] Tool-calling LLM error on iteration {iteration}: {e}")
            if pending_actions:
                break
            raise  # Let outer handler fall back to old method

        if response.has_tool_calls():
            any_fed_back = False
            for tc in response.tool_calls:
                # Ensure synthetic ID for providers that don't emit them
                if not tc.id:
                    tc.id = _next_tool_id()

                tc._context = context  # For get_current_info
                result = _execute_tool(tc)

                if result is not None:
                    messages.append({
                        "role": "tool",
                        "content": result,
                        "tool_call_id": tc.id,
                    })
                    any_fed_back = True
                else:
                    pending_actions.append(_tool_to_action(tc, context))

            # If all tools in this turn were action tools (none fed back),
            # ask LLM to summarize so we can exit
            if not any_fed_back:
                messages.append({
                    "role": "user",
                    "content": "All tools have been submitted for execution. Summarize what you've done for the user in one or two sentences.",
                })
        else:
            # LLM returned text — this is the final explanation
            return {
                "actions": pending_actions if pending_actions else None,
                "explanation": response.content.strip(),
            }

    # Max iterations reached
    return {
        "actions": pending_actions if pending_actions else None,
        "explanation": "I've processed your request and set things up.",
    }


# ═══════════════════════════════════════════════════════════
#  DIRECT PATTERN MATCHING (fast path, unchanged)
# ═══════════════════════════════════════════════════════════

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
    (r'\b(rebuild|refresh|reload) (queue|playlist)\b', {"action": "rebuild"}),
    (r'\b(go back|undo|previous|never mind)\b', {"action": "undo_skip"}),

    # Song feedback
    (r'\b(heart|love|like|favorite|❤️|♥)\s*(this|song|track|it)\b', {"action": "heart"}),
    (r'\b(thumbs.?down|dislike|ban|hate|terrible|awful)\s*(this|song|track|it)\b', {"action": "thumbs_down"}),

    # Playback control
    (r'\b(pause|hold on|wait|freeze)\b', {"action": "pause"}),
    (r'\b(resume|unpause|continue|keep going)\b', {"action": "resume"}),
    (r'^\s*play\s*$', {"action": "resume"}),
    (r'\b(louder|turn it up|volume up|crank it)\b', {"action": "volume_up"}),
    (r'\b(quieter|turn it down|volume down|softer)\b', {"action": "volume_down"}),
    (r'\bvolume (to|set|at|is) (\d{1,3})\b', {"action": "set_volume", "level": None}),
    (r'\bset volume (\d{1,3})\b', {"action": "set_volume", "level": None}),

    # Mode
    (r'\b(radio mode|ai mode|full mode)\b', {"action": "mode_switch", "mode": "radio"}),
    (r'\b(normal mode|basic mode|manual mode)\b', {"action": "mode_switch", "mode": "normal"}),
    (r'\b(toggle mode|switch mode)\b', {"action": "mode_switch", "mode": None}),

    # Sleep timer
    (r'\b(sleep|stop|shut.?down)\s+in\s+(\d+)\s*(min|minutes|minute)\b', {"action": "sleep_timer", "minutes": None}),
    (r'\b(cancel sleep|remove timer|no sleep)\b', {"action": "cancel_sleep"}),

    # Talk show
    (r'\b(talk show|dj banter|radio show)\s+(on|enable|start)\b', {"action": "talk_show", "enabled": "true"}),
    (r'\b(talk show|dj banter|radio show)\s+(off|disable|stop)\b', {"action": "talk_show", "enabled": "false"}),

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

    # Play specific song (after genre/artist so "play more jazz" → genre_filter, not play_song)
    (r'\b(play|start) (.+?)(?:\.|!|\?|$)', {"action": "play_song", "query": None}),

    # Stop
    (r'\b(stop|quit|exit|halt)\b', {"action": "stop"}),

    # Help
    (r'\b(help|commands|what can you do|options)\b', {"action": "help"}),
]

_MOOD_KEYWORDS = {
    "energetic": ["energetic", "electric", "pumped", "hyped", "lit", "intense", "powerful", "driving", "upbeat", "fast"],
    "chill": ["chill", "relax", "calm", "laid-back", "cool", "smooth", "mellow", "soft", "gentle", "peaceful"],
    "focus": ["focus", "concentrate", "study", "work", "deep", "ambient", "minimal", "repetitive", "instrumental"],
    "melancholy": ["melancholy", "sad", "down", "blue", "emo", "somber", "dark", "heavy", "gloomy", "wistful"],
    "discovery": ["discover", "explore", "new", "variety", "experimental", "weird", "different", "unique", "novel"],
}

# Pre-compiled regex for mood keyword matching (replaces 50 substring checks)
_MOOD_PATTERN = re.compile(
    r'\b(' + '|'.join(kw for keywords in _MOOD_KEYWORDS.values() for kw in keywords) + r')\b', re.IGNORECASE
)


def _match_direct(text: str) -> dict | None:
    """Try to match the command against direct keyword patterns."""
    lower = text.lower().strip()

    # Use pre-compiled regex instead of 50 substring checks
    if _MOOD_PATTERN.search(lower):
        for mood, keywords in _MOOD_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                return {"action": "mood_shift", "target_mood": mood}

    for pattern, action in _COMMAND_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            result = dict(action)
            if "genre" in result:
                genre_match = re.search(r'\b(jazz|rock|pop|hip.?hop|rap|classical|electronic|ambient|lo.?fi|soul|funk|disco|country|folk|metal|punk|indie)\b', lower)
                if genre_match:
                    result["genre"] = genre_match.group(1)
            if "artist" in result:
                artist_match = re.search(r'\bplay more (\w+(?:[\s-]+\w+)*)\b', lower)
                if artist_match:
                    result["artist"] = artist_match.group(1)
            if "level" in result and result["level"] is None:
                for g in m.groups():
                    if g and g.isdigit():
                        result["level"] = int(g)
                        break
            if "minutes" in result and result["minutes"] is None:
                for g in m.groups():
                    if g and g.isdigit():
                        result["minutes"] = int(g)
                        break
            if "query" in result:
                captured = m.groups()
                if len(captured) >= 2:
                    result["query"] = captured[1].strip()
                query = result.get("query", "")
                if query and re.search(r'\b(\d+\s+song|songs?\s+from|something\s+like|like\s+this)\b', query):
                    return None  # Complex → fall through to tool-calling
                if query and len(query.split()) > 1:
                    return None  # Multi-word → fall through to LLM for better parsing
            return result

    genre_match = re.search(r'\b(jazz|rock|pop|hip.?hop|rap|classical|electronic|ambient|lo.?fi|soul|funk|disco|country|folk|metal|punk|indie)\b', lower)
    if genre_match:
        return {"action": "genre_filter", "genre": genre_match.group(1)}

    return None


_EXPLANATION_MAP = {
    "mood_shift": "Shifting mood to {target_mood} vibe",
    "vibe_lock": "Locking current vibe",
    "vibe_unlock": "Unlocking vibe lock",
    "queue_info": "Showing queue info",
    "replay": "Replaying current song",
    "skip": "Skipping to next song",
    "clear_queue": "Clearing the queue",
    "rebuild": "Rebuilding the queue",
    "undo_skip": "Going back to previous song",
    "heart": "Hearted! Adding to favorites",
    "thumbs_down": "Thumbs down. I'll play less of this",
    "pause": "Pausing playback",
    "resume": "Resuming playback",
    "volume_up": "Turning it up",
    "volume_down": "Turning it down",
    "set_volume": "Volume set to {level}",
    "exploration": "Exploration set to {level}",
    "genre_filter": "Filtering for {genre} genre",
    "artist_filter": "Filtering for {artist}",
    "clear_filters": "Cleared all filters",
    "mode_switch": "Switching to {mode} mode",
    "sleep_timer": "Sleep timer set for {minutes} minutes",
    "cancel_sleep": "Sleep timer cancelled",
    "talk_show": "Talk show {enabled}",
    "stop": "Stopping playback",
    "help": "Showing available commands",
}


def _make_explanation(action: str, direct: dict) -> str:
    fmt = _EXPLANATION_MAP.get(action)
    if fmt:
        try:
            return fmt.format(**direct)
        except KeyError:
            pass
    return f"Command: {action}"


# Help text (unchanged)
_HELP_CATEGORIES = {
    "Playback": [
        ("play <song>", "Find and play a specific song"),
        ("skip / next", "Skip to next song"),
        ("replay / again", "Play current song again"),
        ("pause", "Pause playback"),
        ("resume / play", "Resume playback"),
        ("stop / quit", "Stop playback entirely"),
        ("louder / volume up", "Increase volume"),
        ("quieter / volume down", "Decrease volume"),
        ("volume to 50", "Set exact volume level"),
    ],
    "Mood & Vibe": [
        ("more energetic", "Switch to energetic mood"),
        ("chill / relax / calm", "Switch to chill mood"),
        ("focus / concentrate", "Switch to focus mood"),
        ("melancholy / sad", "Switch to melancholy mood"),
        ("discover / explore", "Switch to discovery mood"),
        ("vibe lock", "Lock to current song's vibe"),
        ("vibe unlock", "Release vibe lock"),
    ],
    "Queue": [
        ("what's next", "Show upcoming songs"),
        ("clear queue", "Empty the queue"),
        ("rebuild queue", "Force refresh the queue"),
        ("go back / undo", "Undo last skip"),
    ],
    "Feedback": [
        ("heart this / love this", "Favorite the current song"),
        ("dislike this / ban", "Thumbs down the current song"),
        ("why this song", "Why this was recommended"),
        ("tell me about this", "Info about current song"),
    ],
    "Filters": [
        ("play more jazz", "Filter by genre"),
        ("play more Radiohead", "Filter by artist"),
        ("clear filters", "Remove all genre/artist filters"),
        ("get more experimental", "High exploration (wild picks)"),
        ("play it safe", "Low exploration (familiar picks)"),
    ],
    "Multi-Step": [
        ("play 5 songs from Radiohead", "Queue multiple songs then return to recommendations"),
        ("play something chill for studying", "Search by mood constraints"),
        ("queue Radiohead then Pink Floyd", "Search-and-queue multiple artists in order"),
        ("skip to something upbeat", "Search + skip in one step"),
        ("play something like this", "Find and play songs similar to current"),
        ("add 3 chill songs to queue", "Search and queue without interrupting playback"),
        ("find fast jazz and queue a few", "Narrow by genre + energy in one command"),
        ("sleep in 30", "Set sleep timer for 30 minutes"),
        ("cancel sleep timer", "Cancel the sleep timer"),
    ],
    "Features": [
        ("radio mode", "Switch to AI radio mode"),
        ("normal mode", "Switch to basic mode"),
        ("talk show on/off", "Enable or disable DJ banter"),
        ("help / commands", "Show this help menu"),
    ],
}


def get_help_categories():
    return _HELP_CATEGORIES


def parse_command(text: str, context: dict) -> dict:
    """Parse a natural language command into structured action(s).

    Returns dict with either:
      - action + explanation (simple commands, backward compatible)
      - actions[] + explanation (multi-step tool-calling commands)
    """
    # Fast path: regex matching
    direct = _match_direct(text)
    if direct:
        result = dict(direct)
        result["explanation"] = _make_explanation(direct["action"], direct)
        return result

    # Tool-calling LLM path
    try:
        tool_result = _call_llm_with_tools(text, context)
        if tool_result.get("actions"):
            return tool_result
        # If no actions but we got an explanation, wrap as a single action
        if tool_result.get("explanation"):
            return {
                "action": "custom",
                "explanation": tool_result["explanation"],
            }
    except Exception as e:
        print(f"[parser] Tool-calling failed, falling back: {e}")

    # Legacy fallback: single-action LLM parsing
    try:
        ctx_str = _build_context_str(context)
        ollama_result = _call_ollama(text, ctx_str)
        explanation = ollama_result.pop("explanation", f"Interpreted: {text}")
        ollama_result["explanation"] = explanation
        return ollama_result
    except (json.JSONDecodeError, Exception) as e:
        print(f"[parser] All parsing methods failed: {e}")
        return {
            "action": "help",
            "explanation": f"Could not understand: '{text}'. Try 'help' to see available commands.",
        }


def _build_context_str(context: dict) -> str:
    """Build a short context string for the legacy LLM prompt."""
    ctx = f"Mood: {context.get('mood_state', 'neutral')}\n"
    song = context.get("current_song") or {}
    if song.get("id"):
        ctx += f"Current: {song.get('artist', '?')} - {song.get('title', '?')}\n"
    queue = context.get("queue", [])
    if queue:
        n = queue[0] if isinstance(queue, list) else None
        if n:
            ctx += f"Next: {n.get('artist', '?')} - {n.get('title', '?')}\n"
    if context.get("archetype"):
        ctx += f"Archetype: {context['archetype']}\n"
    return ctx


def _call_ollama(text: str, context: str) -> dict:
    """Legacy single-action LLM parsing."""
    prompt = f"""You are a radio station command parser. Convert the user's natural language into a structured JSON action.

Context:
{context}

User command: "{text}"

Available actions:
- mood_shift: {{"action": "mood_shift", "target_mood": "energetic"|"chill"|"focus"|"melancholy"|"discovery"}}
- vibe_lock: {{"action": "vibe_lock"}}
- vibe_unlock: {{"action": "vibe_unlock"}}
- queue_info: {{"action": "queue_info"}}
- replay: {{"action": "replay"}}
- skip: {{"action": "skip"}}
- clear_queue: {{"action": "clear_queue"}}
- undo_skip: {{"action": "undo_skip"}}
- rebuild: {{"action": "rebuild"}}
- heart: {{"action": "heart"}}
- thumbs_down: {{"action": "thumbs_down"}}
- pause: {{"action": "pause"}}
- resume: {{"action": "resume"}}
- volume_up: {{"action": "volume_up"}}
- volume_down: {{"action": "volume_down"}}
- set_volume: {{"action": "set_volume", "level": <0-100>}}
- exploration: {{"action": "exploration", "level": "low"|"medium"|"high"}}
- genre_filter: {{"action": "genre_filter", "genre": "jazz"|"rock"|"pop"|...}}
- artist_filter: {{"action": "artist_filter", "artist": "artist name"}}
- clear_filters: {{"action": "clear_filters"}}
- play_song: {{"action": "play_song", "query": "artist or song name"}}
- why_this_song: {{"action": "why_this_song"}}
- song_info: {{"action": "song_info"}}
- mode_switch: {{"action": "mode_switch", "mode": "radio"|"normal"}}
- sleep_timer: {{"action": "sleep_timer", "minutes": 15|30|60}}
- cancel_sleep: {{"action": "cancel_sleep"}}
- talk_show: {{"action": "talk_show", "enabled": true|false}}
- stop: {{"action": "stop"}}
- help: {{"action": "help"}}

Rules:
- Return ONLY valid JSON, no preamble, no markdown, no code blocks
- If the command is unclear, return {{"action": "help"}}
- Match the intent as closely as possible

Return JSON:"""

    response = llm_chat().chat(
        model=None,
        messages=[{"role": "user", "content": prompt}],
        options=config.OLLAMA_CHAT_OPTIONS,
        think=False,
        timeout=config.LLM_CHAT_TIMEOUT,
    )
    raw = response.content.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    raw = raw.strip('"\'')
    return json.loads(raw)
