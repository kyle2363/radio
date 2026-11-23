"""Engine-neutral speech direction for the DJ, talk show and announcements.

The radio describes *how* a line should sound in engine-neutral terms, and each
TTS engine has a small adapter that turns that into its own request. Swapping or
upgrading the speech model means writing one adapter and adding it to
``ADAPTERS``; an engine without an adapter gets plain text and still works.

Parts:

- ``DirectionFlags``: one setting row per feature plus a master switch
  (``SETTING_DEFAULTS``). Everything here is skipped when a feature is off.
- ``SpeechDirection``: emotion, energy/valence/pace, persona delivery traits, a
  loudness target, a tight start, and whether to honour inline marks and pauses.
- Inline marks inside the text: ``<whisper>…</whisper>``, ``<shout>…</shout>``,
  ``<soft>…</soft>``, ``<emph>…</emph>`` for a few words, ``<pause>`` /
  ``<pause short>`` / ``<pause long>`` between words, and the OmniVoice
  non-verbal tags (``[laughter]``, ``[sigh]``…) which other engines may treat as
  a style over the rest of the sentence.
- Builders that turn a trigger, mood, next song, hour, LLM emotion word and
  persona into a direction.

Engine-neutral work that does not need an adapter (pauses for engines without
native pauses, loudness matching) is done by ``VoiceManager``.
"""

from __future__ import annotations

import hashlib
import importlib
import random
import re
import threading
from dataclasses import dataclass, field

# ---- settings ----

SETTING_DEFAULTS: dict[str, str] = {
    "speech_direction_enabled": "true",        # master switch
    "speech_emotion_enabled": "true",          # trigger and per-line emotions
    "speech_mood_delivery_enabled": "true",    # mood, next song and hour shape energy and pace
    "speech_loudness_match_enabled": "true",   # every voice at one loudness
    "speech_loudness_target_lufs": "-13",  # about the library median
    "speech_tight_start_enabled": "true",      # no lead-in silence before speech
    "speech_word_marks_enabled": "true",       # whispered asides, shouted words, emphasis
    "speech_pauses_enabled": "true",           # real pauses at "..." and spaced dashes
    "speech_persona_traits_enabled": "true",   # delivery habits per persona (hesitation)
    "speech_caller_phone_line_enabled": "true",  # some calls get a light phone sound (phone_line.PHONE_CHANCE)
    "speech_caller_ambience_enabled": "true",    # some calls get a kitchen, street or rain background
}
LOUDNESS_RANGE = (-30.0, -8.0)


def _flag(get_setting, key: str) -> bool:
    try:
        value = get_setting(key)
    except Exception:
        value = None
    if value is None:
        value = SETTING_DEFAULTS[key]
    return str(value).strip().lower() == "true"


@dataclass(frozen=True)
class DirectionFlags:
    enabled: bool = False
    emotion: bool = False
    mood_delivery: bool = False
    loudness_match: bool = False
    loudness_target: float = -13.0
    tight_start: bool = False
    word_marks: bool = False
    pauses: bool = False
    persona_traits: bool = False
    phone_line: bool = False
    caller_ambience: bool = False

    @classmethod
    def from_settings(cls, get_setting) -> "DirectionFlags":
        if not _flag(get_setting, "speech_direction_enabled"):
            return cls()
        try:
            target = float(get_setting("speech_loudness_target_lufs"))
        except (TypeError, ValueError):
            target = float(SETTING_DEFAULTS["speech_loudness_target_lufs"])
        if not LOUDNESS_RANGE[0] <= target <= LOUDNESS_RANGE[1]:
            target = float(SETTING_DEFAULTS["speech_loudness_target_lufs"])
        return cls(
            enabled=True,
            emotion=_flag(get_setting, "speech_emotion_enabled"),
            mood_delivery=_flag(get_setting, "speech_mood_delivery_enabled"),
            loudness_match=_flag(get_setting, "speech_loudness_match_enabled"),
            loudness_target=target,
            tight_start=_flag(get_setting, "speech_tight_start_enabled"),
            word_marks=_flag(get_setting, "speech_word_marks_enabled"),
            pauses=_flag(get_setting, "speech_pauses_enabled"),
            persona_traits=_flag(get_setting, "speech_persona_traits_enabled"),
            phone_line=_flag(get_setting, "speech_caller_phone_line_enabled"),
            caller_ambience=_flag(get_setting, "speech_caller_ambience_enabled"),
        )


def current_flags() -> DirectionFlags:
    """Flags from the settings table (cached by the database layer)."""
    try:
        from db import database
        return DirectionFlags.from_settings(database.get_setting)
    except Exception:
        return DirectionFlags()


# ---- direction ----

# Emotion vocabulary: name -> (energy, valence) it implies, each -1..1.
EMOTIONS: dict[str, tuple[float, float]] = {
    "neutral": (0.0, 0.0),
    "happy": (0.4, 0.7),
    "amused": (0.3, 0.6),
    "excited": (0.8, 0.6),
    "warm": (0.0, 0.5),
    "calm": (-0.5, 0.2),
    "sad": (-0.4, -0.6),
    "wistful": (-0.3, -0.2),
    "nervous": (0.3, -0.3),
    "annoyed": (0.3, -0.5),
    "angry": (0.7, -0.7),
    "surprised": (0.6, 0.2),
    "proud": (0.3, 0.5),
    "confused": (0.0, -0.2),
    "interested": (0.2, 0.3),
}


@dataclass(frozen=True)
class VoiceTraits:
    """A persona's lasting delivery habits, in engine-neutral words.

    Accent and voice texture are not traits: they come from the persona's reference
    clip (steering the speaker itself needs embedding mode and costs likeness).
    """
    hesitant: float = 0.0                 # 0..1


@dataclass(frozen=True)
class SpeechDirection:
    emotion: str | None = None
    intensity: float = 0.6     # 0..1 of the adapter's full emotion dose
    energy: float = 0.0        # -1 calm .. 1 energetic
    valence: float = 0.0       # -1 dark .. 1 bright
    pace: float = 0.0          # -1 slower .. 1 faster
    traits: VoiceTraits | None = None
    loudness_lufs: float | None = None
    tight_start: bool = False
    word_marks: bool = True    # honour inline marks already in the text
    pauses: bool = False       # honour <pause> marks

    @property
    def is_neutral(self) -> bool:
        return (self.emotion is None and not self.energy and not self.valence and not self.pace
                and self.traits is None and self.loudness_lufs is None and not self.tight_start)

    def key(self) -> str:
        return hashlib.md5(repr(self).encode()).hexdigest()[:8]

    @classmethod
    def off(cls) -> "SpeechDirection":
        return cls(word_marks=False, pauses=False)


TRIGGER_EMOTIONS: dict[str, str] = {
    "mood_change": "excited",
    "vibe_lock": "happy",
    "wildcard": "amused",
    "skip_streak": "calm",
    "recovery": "warm",
    "discovery": "interested",
}

# Mood -> (energy, valence, pace).
MOOD_DELIVERY: dict[str, tuple[float, float, float]] = {
    "neutral": (0.0, 0.0, 0.0),
    "chill": (-0.5, 0.2, -0.3),
    "energetic": (0.6, 0.4, 0.25),
    "focus": (-0.3, 0.0, -0.1),
    "melancholy": (-0.4, -0.5, -0.25),
    "discovery": (0.2, 0.3, 0.05),
}

MONOLOGUE_EMOTIONS: dict[str, str] = {
    "musical_detail": "interested",
    "everyday_observation": "amused",
    "listener_question": "interested",
    "unexpected_connection": "interested",
    "mini_opinion": "proud",
    "set_direction": "warm",
    "memory": "wistful",
    "artist_dive": "interested",
    "late_night_confession": "wistful",
    "listener_address": "warm",
    "lyric_meditation": "calm",
    "arc_trace": "warm",
}

# Persona delivery traits (persona id -> traits). Voice character (accent, texture)
# lives in each persona's reference clip.
PERSONA_TRAITS: dict[str, VoiceTraits] = {
    "caller_bookworm": VoiceTraits(hesitant=0.4),
}


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_emotion(word) -> str | None:
    if not isinstance(word, str):
        return None
    cleaned = re.sub(r"[^a-z]", "", word.lower())
    return cleaned if cleaned in EMOTIONS else None


def mood_delivery(mood_state: str | None, next_song: dict | None = None,
                  hour: int | None = None) -> tuple[float, float, float]:
    """Energy, valence and pace from the mood, the next song and the hour."""
    energy, valence, pace = MOOD_DELIVERY.get((mood_state or "neutral").lower(), (0.0, 0.0, 0.0))
    song_energy = (next_song or {}).get("energy")
    song_valence = (next_song or {}).get("valence")
    if isinstance(song_energy, (int, float)):
        energy = 0.6 * energy + 0.4 * (2 * float(song_energy) - 1)
    if isinstance(song_valence, (int, float)):
        valence = 0.6 * valence + 0.4 * (2 * float(song_valence) - 1)
    if hour is not None and (hour >= 23 or hour < 5):
        energy -= 0.3
        pace -= 0.1
    return round(_clamp(energy), 3), round(_clamp(valence), 3), round(_clamp(pace), 3)


# The main host is the station's steady voice: milder emotion than callers and guests,
# a gentler pull from the mood, and never the strongest emotions.
HOST_PERSONAS = frozenset({"dj"})
HOST_EMOTION_SCALE = 0.5     # of the emotion dose any other voice gets
HOST_DELIVERY_SCALE = 0.6    # of the mood's energy, valence and pace
HOST_SOFTER: dict[str, str] = {
    "excited": "happy",
    "surprised": "interested",
    "angry": "annoyed",
    "sad": "wistful",
    "nervous": "neutral",
}

# Within one segment: an emotion a speaker repeats fades line by line instead of
# re-hitting the full dose (heard as building), and a jump between far-apart emotions
# lands at reduced strength (heard as swinging back and forth).
REPEAT_DECAY = 0.7
SWING_DISTANCE = 0.8         # in (energy, valence) space, see EMOTIONS
SWING_SCALE = 0.5


class EmotionArc:
    """One segment's emotional history per speaker, so a back-and-forth stays steady."""

    def __init__(self):
        self._last: dict[str | None, tuple[str, int]] = {}  # speaker -> (emotion, repeats)

    def shape(self, speaker: str | None, emotion: str | None, intensity: float) -> float:
        if not emotion or emotion == "neutral":
            self._last.pop(speaker, None)
            return intensity
        previous = self._last.get(speaker)
        repeats = 0
        if previous and previous[0] == emotion:
            repeats = previous[1] + 1
            intensity *= REPEAT_DECAY ** repeats
        elif previous:
            (e1, v1), (e2, v2) = EMOTIONS[previous[0]], EMOTIONS[emotion]
            if ((e1 - e2) ** 2 + (v1 - v2) ** 2) ** 0.5 > SWING_DISTANCE:
                intensity *= SWING_SCALE
        self._last[speaker] = (emotion, repeats)
        return intensity


def _build(emotion, *, mood_state=None, next_song=None, hour=None, persona_id=None,
           flags: DirectionFlags | None = None, intensity: float = 0.6,
           arc: EmotionArc | None = None) -> SpeechDirection:
    flags = flags if flags is not None else current_flags()
    if not flags.enabled:
        return SpeechDirection.off()
    energy = valence = pace = 0.0
    if flags.mood_delivery:
        energy, valence, pace = mood_delivery(mood_state, next_song, hour)
    emotion = normalize_emotion(emotion) if flags.emotion else None
    if persona_id in HOST_PERSONAS:
        if emotion:
            emotion = HOST_SOFTER.get(emotion, emotion)
            intensity *= HOST_EMOTION_SCALE
        energy, valence, pace = (round(x * HOST_DELIVERY_SCALE, 3) for x in (energy, valence, pace))
    if arc is not None:
        intensity = arc.shape(persona_id, emotion, intensity)
    return SpeechDirection(
        emotion=emotion,
        intensity=round(intensity, 3),
        energy=energy,
        valence=valence,
        pace=pace,
        traits=PERSONA_TRAITS.get(persona_id) if flags.persona_traits and persona_id else None,
        loudness_lufs=flags.loudness_target if flags.loudness_match else None,
        tight_start=flags.tight_start,
        word_marks=flags.word_marks,
        pauses=flags.pauses,
    )


def direction_for_announcement(trigger: str | None, context: dict | None = None, *,
                               persona_id: str | None = "dj",
                               flags: DirectionFlags | None = None) -> SpeechDirection:
    """Direction for a DJ announcement: the trigger sets the emotion, the context the delivery."""
    context = context or {}
    return _build(TRIGGER_EMOTIONS.get(trigger or ""), mood_state=context.get("mood_state"),
                  next_song=context.get("next_song"), hour=context.get("hour"),
                  persona_id=persona_id, flags=flags)


def direction_for_line(emotion=None, *, mood_state: str | None = None, persona_id: str | None = None,
                       context: dict | None = None, flags: DirectionFlags | None = None,
                       intensity: float = 0.6, arc: EmotionArc | None = None) -> SpeechDirection:
    """Direction for one talk-show line (caller, DJ, monologue or guest).

    Pass one ``EmotionArc`` for every line of a segment so repeated emotions fade
    and big swings are softened.
    """
    context = context or {}
    return _build(emotion, mood_state=mood_state, next_song=context.get("next_song"),
                  hour=context.get("hour"), persona_id=persona_id, flags=flags, intensity=intensity,
                  arc=arc)


# ---- inline marks ----

SPAN_STYLES = ("whisper", "shout", "soft", "emph")
TAG_STYLES = {
    "laughter": "laughter",
    "laughs": "laughter",
    "sigh": "sigh",
    "confirmation-en": "confirmation",
    "surprise-ah": "surprise",
    "dissatisfaction-hnn": "dissatisfaction",
}
PAUSE_MS = {"short": 250, None: 450, "long": 800}
MAX_PAUSES_PER_LINE = 2
MAX_MARKS_PER_LINE = 2

_MARK_RE = re.compile(r"<(/?)([a-z]+)(?:\s+([a-z]+))?>")
_TAG_RE = re.compile(r"\[([a-z-]+)\]")
_PAUSE_RE = re.compile(r"\s*<pause(?:\s+(short|long))?>\s*")
_TOKEN_RE = re.compile(r"<(/?)([a-z]+)(?:\s+([a-z]+))?>|\[([a-z-]+)\]")


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    style: str


@dataclass
class MarkedText:
    text: str
    spans: list[Span] = field(default_factory=list)


def parse_marks(text: str) -> MarkedText:
    """Plain speech text plus character spans for marks and non-verbal tags.

    Unknown or stray marks are dropped; an unclosed mark runs to the end. A
    non-verbal tag styles the rest of its sentence.
    """
    out: list[str] = []
    length = 0
    open_spans: dict[str, int] = {}
    spans: list[Span] = []
    tag_starts: list[tuple[int, str]] = []

    def append(piece: str):
        nonlocal length
        if not piece:
            return
        piece = re.sub(r"\s+", " ", piece)
        if (not out or out[-1].endswith(" ")) and piece.startswith(" "):
            piece = piece[1:]
        if not piece:
            return
        out.append(piece)
        length += len(piece)

    pos = 0
    for m in _TOKEN_RE.finditer(text or ""):
        append(text[pos:m.start()])
        pos = m.end()
        closing, name, _arg, tag = m.groups()
        if tag is not None:
            style = TAG_STYLES.get(tag)
            if style:
                tag_starts.append((length, style))
            continue
        if name not in SPAN_STYLES:
            continue
        if not closing:
            open_spans.setdefault(name, length)
        elif name in open_spans:
            start = open_spans.pop(name)
            if length > start:
                spans.append(Span(start, length, name))
    append(text[pos:] if text else "")

    plain = "".join(out)
    stripped = plain.rstrip()
    lead = len(plain) - len(plain.lstrip())
    plain = stripped.lstrip()
    end = len(plain)

    def fix(i):
        return max(0, min(end, i - lead))

    for name, start in open_spans.items():
        spans.append(Span(start, end + lead, name))
    for start, style in tag_starts:
        start = fix(start)
        while start < end and plain[start] == " ":
            start += 1
        m = re.search(r"[.!?]+", plain[start:])
        stop = start + m.end() if m else end
        if stop > start:
            spans.append(Span(start, stop, style))
    result = []
    for s in spans:
        a, b = fix(s.start), fix(s.end)
        while b > a and plain[b - 1] == " ":
            b -= 1
        if b > a:
            result.append(Span(a, b, s.style))
    result.sort(key=lambda s: (s.start, s.end))
    return MarkedText(plain, result)


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text.strip()


def strip_marks(text: str, keep_tags: bool = False) -> str:
    """Text with every inline mark removed (and non-verbal tags, unless kept)."""
    if not text:
        return text
    text = _MARK_RE.sub(" ", text)
    if not keep_tags:
        text = _TAG_RE.sub(" ", text)
    return _tidy(text)


def remove_pauses(text: str) -> str:
    return _PAUSE_RE.sub(" ", text or "")


def split_pauses(text: str) -> list[tuple[str, int]]:
    """Split at pause marks: [(chunk, silence_ms_after), ...]; the last pause is 0."""
    chunks: list[tuple[str, int]] = []
    pos = 0
    for m in _PAUSE_RE.finditer(text or ""):
        piece = text[pos:m.start()].strip()
        ms = PAUSE_MS[m.group(1)]
        if strip_marks(piece):
            chunks.append((piece, ms))
        elif chunks:
            chunks[-1] = (chunks[-1][0], chunks[-1][1] + ms)
        pos = m.end()
    tail = (text or "")[pos:].strip()
    if strip_marks(tail) or not chunks:
        chunks.append((tail, 0))
    else:
        chunks[-1] = (chunks[-1][0], 0)
    return chunks


_PAUSE_SITES = re.compile(r"(\.\.\.|…)(?=\s+\w)|\s+(?:--|-|—|–)\s+")


def mark_pauses(text: str, limit: int = MAX_PAUSES_PER_LINE) -> str:
    """Turn mid-sentence "..." and spaced dashes into pause marks (at most ``limit``)."""
    count = 0

    def replace(m):
        nonlocal count
        if count >= limit:
            return m.group(0)
        count += 1
        return " <pause> " if m.group(1) else " <pause short> "

    return _PAUSE_SITES.sub(replace, text or "")


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[<]*[A-Z0-9])")
_OPEN_MARK = re.compile(r"<(whisper|shout|soft|emph)>")
_CLOSE_MARK = re.compile(r"</(whisper|shout|soft|emph)>")
# Mostly normal and long: a 250 ms "short" pause is no longer than the gaps between
# phrases inside a sentence, so on its own it is not heard as a breath.
SENTENCE_PAUSE_WEIGHTS = (("short", 0.10), (None, 0.60), ("long", 0.30))


def pace_sentences(text: str, rng=random) -> str:
    """A pause between sentences, so long talk is spoken in breaths, not read as one block.

    Mostly short beats, sometimes a normal or long one. Boundaries inside a word mark
    (an open <whisper>...) are skipped so a mark is never split across two chunks.
    """
    if not text:
        return text
    out, pos = [], 0
    for m in _SENTENCE_END.finditer(text):
        before = text[:m.start()]
        if len(_OPEN_MARK.findall(before)) > len(_CLOSE_MARK.findall(before)):
            continue
        styles, weights = zip(*SENTENCE_PAUSE_WEIGHTS)
        style = rng.choices(styles, weights=weights, k=1)[0]
        out.append(text[pos:m.start()])
        out.append(f" <pause {style}> " if style else " <pause> ")
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


_WORD_MARK_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:between you and me|just between us|don't tell anyone|truth be told|if i'm honest|secretly)\b[^,.!?;]*", re.I), "whisper"),
    (re.compile(r"\b(?:no way|oh my god|come on|let's go|are you kidding(?: me)?|unbelievable)\b", re.I), "shout"),
    (re.compile(r"\b(?:those were the days|i miss (?:it|that|them|those days|him|her)|rest in peace|goodnight|sweet dreams)\b", re.I), "soft"),
    (re.compile(r"\b[A-Z]{3,}\b"), "emph"),
]


def add_word_marks(text: str, rng=random, probability: float = 0.35,
                   limit: int = MAX_MARKS_PER_LINE) -> str:
    """Wrap a few phrases in marks (asides whispered, exclamations shouted...)."""
    if not text:
        return text
    candidates = []
    for pattern, style in _WORD_MARK_RULES:
        for m in pattern.finditer(text):
            if m.end() > m.start():
                candidates.append((m.start(), m.end(), style))
    candidates.sort()
    chosen, last_end = [], -1
    for start, end, style in candidates:
        if len(chosen) >= limit:
            break
        if start < last_end or rng.random() > probability:
            continue
        chosen.append((start, end, style))
        last_end = end
    for start, end, style in reversed(chosen):
        text = f"{text[:start]}<{style}>{text[start:end]}</{style}>{text[end:]}"
    return text


def prepare_line(text: str, flags: DirectionFlags | None = None, rng=random,
                 mark_probability: float = 0.35) -> str:
    """Add word marks and pauses to a generated line, as the settings allow."""
    flags = flags if flags is not None else current_flags()
    if not flags.enabled or not text:
        return text
    if flags.word_marks:
        text = add_word_marks(text, rng=rng, probability=mark_probability)
    if flags.pauses:
        text = mark_pauses(text)
    return text


# ---- adapters ----

@dataclass
class Rendered:
    text: str
    request: dict = field(default_factory=dict)


class DirectionAdapter:
    """Plain rendering: speech text only. Base for engine adapters."""

    native_pauses = False

    def render(self, text: str, direction: SpeechDirection) -> Rendered:
        return Rendered(strip_marks(remove_pauses(text)), {})


PlainAdapter = DirectionAdapter


class OmniVoiceAdapter(DirectionAdapter):
    """OmniVoice reads non-verbal tags and ``.,.`` pauses itself; no request fields."""

    native_pauses = True

    def render(self, text: str, direction: SpeechDirection) -> Rendered:
        text = _PAUSE_RE.sub(" \x00 ", text or "")
        return Rendered(strip_marks(text, keep_tags=True).replace("\x00", ".,."), {})


# Engine id -> "module:Class". Add one line here for a new speech model.
ADAPTERS: dict[str, str] = {
    "omnivoice": "announcer.speech_direction:OmniVoiceAdapter",
    "zonos3": "announcer.zonos3_direction:Zonos3Direction",
}
_adapter_cache: dict[str, DirectionAdapter] = {}
_adapter_lock = threading.Lock()


def get_adapter(engine_id: str | None) -> DirectionAdapter:
    with _adapter_lock:
        if engine_id in _adapter_cache:
            return _adapter_cache[engine_id]
        adapter: DirectionAdapter = PlainAdapter()
        target = ADAPTERS.get(engine_id or "")
        if target:
            module, _, name = target.partition(":")
            try:
                adapter = getattr(importlib.import_module(module), name)()
            except Exception:
                adapter = PlainAdapter()
        _adapter_cache[engine_id] = adapter
        return adapter
