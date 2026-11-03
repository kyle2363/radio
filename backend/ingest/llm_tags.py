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


def generate_tags(caption: str, lyrics: str = "", retries: int = 3, options: dict | None = None) -> dict:
    """Call the configured chat provider and validate its tag response.

    Provider, parsing, and validation failures are retried. Exhaustion raises
    ValueError so callers cannot mistake a failed tagging attempt for success.
    """
    import config
    lyrics_block = ""
    if lyrics:
        words = lyrics.split()[:config.LYRICS_TAG_WORDS]
        lyrics_block = " ".join(words)
    prompt = TAG_PROMPT.format(caption=caption[:500], lyrics=lyrics_block or "(no lyrics available)")
    opts = {**config.OLLAMA_CHAT_OPTIONS, **(options or {})}
    if retries < 1:
        raise ValueError("retries must be at least 1")
    last_error = None
    for attempt in range(retries):
        try:
            response = llm_chat().chat(
                model=None,
                messages=[{"role": "user", "content": prompt}],
                options=opts,
                think=False,
            )
            content = response.content
            if not isinstance(content, str):
                raise ValueError("LLM response content must be text")
            content = content.strip()

            # Extract JSON from possible markdown code block
            if content.startswith("```"):
                match = re.match(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
                if match:
                    content = match.group(1).strip()
                else:
                    # Malformed code block — fall through to json.loads on raw content
                    pass

            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("LLM response must be a JSON object")
            tags = {}
            for key in ("emotional_tags", "genre_tags", "instrument_tags"):
                values = result.get(key)
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    raise ValueError(f"LLM response field {key} must be a list of strings")
                tags[key] = values
            return tags
        except Exception as e:
            last_error = e
    raise ValueError(f"Could not generate valid song tags after {retries} attempts: {last_error}") from last_error


def generate_tags_batch(caption: str) -> list:
    """Generate tags for a single song, returns list of JSON strings with fallback."""
    try:
        result = generate_tags(caption)
    except ValueError:
        result = None
    if result:
        return [
            json.dumps(result["emotional_tags"]),
            json.dumps(result["genre_tags"]),
            json.dumps(result["instrument_tags"]),
        ]
    return [json.dumps([]), json.dumps([]), json.dumps([])]
