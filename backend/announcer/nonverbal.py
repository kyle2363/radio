"""Non-verbal tag injection for OmniVoice TTS.

Programmatically adds expressive tags like [laughter], [sigh], etc.
to text based on pattern matching. Applied only to caller lines —
DJ lines, monologues, and announcements stay clean.

Tags are OmniVoice-specific. When using Piper, tags are stripped
before synthesis to prevent them from being read as literal text.
"""

import re
import random
import logging

logger = logging.getLogger(__name__)

# ---- Tag rules ----
# Each rule: (compiled_regex, tag, probability)
# Patterns are case-insensitive and match within sentences.

_TAG_RULES = [
    # Laughter — most common
    (re.compile(r'\b(haha|ha ha|lol|that.{0,20}funny|crack.{0,5}up|cracking.{0,3}up|can.{0,10}stop.{0,5}laughing|dying)\b', re.I),
     '[laughter]', 0.30),

    # Sigh — reflective, nostalgic
    (re.compile(r'\b(sigh|miss.{0,10}those.{0,5}days|those.{0,10}were.{0,5}the.{0,5}days|remember.{0,5}when|i.{0,5}wish)\b', re.I),
     '[sigh]', 0.15),

    # Confirmation — agreement
    (re.compile(r'\b(exactly|absolutely|totally|for.{0,3}sure|you.{0,3}know.{0,3}it|right\?|tell.{0,3}me.{0,3}about.{0,3}it|preach)\b', re.I),
     '[confirmation-en]', 0.15),

    # Surprise — unexpected
    (re.compile(r'\b(wait.{0,3}what|no.{0,3}way|are.{0,3}you.{0,3}serious|get.{0,3}out|shut.{0,3}up|you.{0,10}kidding|hold.{0,3}up)\b', re.I),
     '[surprise-ah]', 0.12),

    # Dissatisfaction — mild skepticism
    (re.compile(r"\b(i.{0,5}don.{0,5}t.{0,3}know|hmm|not.{0,5}sure.{0,5}about.{0,5}that|eh|meh|could.{0,5}do.{0,5}better|is.{0,5}it.{0,5}though)\b", re.I),
     '[dissatisfaction-hnn]', 0.10),
]

# Maximum tags per segment
MAX_TAGS_PER_SEGMENT = 2
# Maximum tags per sentence (always 1 — at most one tag per sentence)
MAX_TAGS_PER_SENTENCE = 1
# Minimum probability floor — even if pattern matches, don't always tag
GLOBAL_TAG_PROBABILITY = 0.25


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences. Handles basic punctuation."""
    # Split on sentence-ending punctuation, keeping the delimiter
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p for p in parts if p.strip()]


def _join_sentences(sentences: list[str]) -> str:
    """Rejoin sentences with spaces."""
    return ' '.join(sentences)


def inject_nonverbal_tags(text: str) -> str:
    """Inject non-verbal tags into caller dialogue text.

    Uses pattern matching to find natural insertion points.
    Applies probabilistic tagging to avoid overuse.

    Args:
        text: Raw caller dialogue text from LLM

    Returns:
        Text with non-verbal tags injected (e.g. "[laughter] That's hilarious.")
    """
    if not text or not text.strip():
        return text

    sentences = _split_sentences(text)
    if not sentences:
        return text

    tags_added = 0
    result_sentences = []

    for sentence in sentences:
        if tags_added >= MAX_TAGS_PER_SEGMENT:
            result_sentences.append(sentence)
            continue

        # Global probability gate — ~25% of sentences get any tag at all
        if random.random() > GLOBAL_TAG_PROBABILITY:
            result_sentences.append(sentence)
            continue

        # Check each tag rule
        tagged = False
        for pattern, tag, probability in _TAG_RULES:
            if pattern.search(sentence):
                # Per-rule probability check
                if random.random() < probability:
                    tagged_text = f"{tag} {sentence}"
                    result_sentences.append(tagged_text)
                    tags_added += 1
                    tagged = True
                    break  # One tag per sentence max

        if not tagged:
            result_sentences.append(sentence)

    result = _join_sentences(result_sentences)
    if tags_added > 0:
        logger.debug(f"NonVerbal: injected {tags_added} tag(s) into caller line")
    return result


def strip_nonverbal_tags(text: str) -> str:
    """Remove all non-verbal tags from text.

    Used when synthesizing with Piper, which would read tags as literal text.

    Args:
        text: Text that may contain [tag] markers

    Returns:
        Text with all [tag] markers removed
    """
    if not text:
        return text
    # Match [word] patterns — covers all known tags
    return re.sub(r'\[[a-z-]+\]\s*', '', text).strip()
