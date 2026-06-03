"""Generate emotional, genre, and instrument tags via LLM."""

import json
import re

from llm import llm_chat

import config

TAG_PROMPT = """Analyze this song and return tags in JSON format.
Do NOT include any preamble or explanation. Return ONLY valid JSON.

Song description:
{caption}

Lyrics:
{lyrics}

Return exactly this JSON structure:
{{
  "emotional_tags": ["tag1", "tag2", "tag3"],
  "genre_tags": ["tag1", "tag2"],
  "instrument_tags": ["tag1", "tag2", "tag3"]
}}

Rules:
- 2-4 tags per category
- Use lowercase, hyphenated multi-word tags
- Be specific (e.g. "ambient-pad" not just "synth")
- Base tags on the actual description AND lyrics, not generic assumptions
- Emotional tags should capture both the musical mood AND lyrical themes
"""


def generate_tags(caption: str, lyrics: str = "", retries: int = 3, options: dict | None = None) -> dict | None:
    """Call Ollama to generate tags from a song caption and optional lyrics."""
    import config
    lyrics_block = ""
    if lyrics:
        words = lyrics.split()[:config.LYRICS_TAG_WORDS]
        lyrics_block = " ".join(words)
    prompt = TAG_PROMPT.format(caption=caption[:500], lyrics=lyrics_block or "(no lyrics available)")
    opts = {**config.OLLAMA_CHAT_OPTIONS, **(options or {})}
    for attempt in range(retries):
        try:
            response = llm_chat().chat(
                model=None,
                messages=[{"role": "user", "content": prompt}],
                options=opts,
                think=False,
            )
            content = response.content.strip()

            # Extract JSON from possible markdown code block
            if content.startswith("```"):
                match = re.match(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
                if match:
                    content = match.group(1).strip()
                else:
                    # Malformed code block — fall through to json.loads on raw content
                    pass

            result = json.loads(content)
            return {
                "emotional_tags": result.get("emotional_tags", []),
                "genre_tags": result.get("genre_tags", []),
                "instrument_tags": result.get("instrument_tags", []),
            }
        except (json.JSONDecodeError, KeyError) as e:
            if attempt < retries - 1:
                continue
    return None


def generate_tags_batch(caption: str) -> list:
    """Generate tags for a single song, returns list of JSON strings with fallback."""
    result = generate_tags(caption)
    if result:
        return [
            json.dumps(result["emotional_tags"]),
            json.dumps(result["genre_tags"]),
            json.dumps(result["instrument_tags"]),
        ]
    return [json.dumps([]), json.dumps([]), json.dumps([])]
