"""ZONOS3-Mini adapter for engine-neutral speech direction.

Turns a ``SpeechDirection`` and inline marks into zonos3-server request fields:

- ``controls``: steering doses for emotion, energy/valence and persona traits
- ``control_spans``: word-level steering for whispered, shouted, soft and
  emphasised words, and for the OmniVoice non-verbal tags (ZONOS3 has no
  laugh or sigh sounds, so ``[laughter]`` becomes an amused delivery of the
  sentence)
- ``condition_values.loudness``: the target loudness
- ``silence_prefill_frames: 0``: speech starts with no lead-in silence

Every id is a real control of the released steering bundle (``GET /v2/controls``,
November 21, 2026) and every dose is clamped to the studio's "safe" slider range
(``SAFE``). The engine drops ids and fields the running server does not offer.
Tune the tables below by ear; a different model gets its own adapter instead.
"""

from __future__ import annotations

from announcer.speech_direction import DirectionAdapter, Rendered, SpeechDirection, parse_marks, remove_pauses

# Raw control id -> (low, high) dose, from the studio's safe slider ranges.
SAFE: dict[str, tuple[float, float]] = {
    "mood.arousal": (-4, 4),
    "mood.valence": (-4, 4),
    "expressive.expressive": (-8, 8),
    "delivery.tempo": (-2, 2),
    "delivery.conversational": (-4, 4),
    "delivery.whisper": (0, 1.5),
    "delivery.loud": (0, 2),
    "disfluency.disfluent": (-8, 8),
    "emphasis_nv_nodur.emphasis": (0, 12),
    "emotions.happy": (0, 4),
    "emotions.angry": (0, 4),
    "emotions.sad": (0, 6),
    "emotions.serene": (0, 6),
    "emotions.scared": (0, 6),
    "emotions.confused": (0, 3),
    # Named emotions without a studio slider: kept low.
    "emotions.content": (0, 3),
    "emotions.surprised": (0, 3),
    "emotions.proud": (0, 3),
    "emotions.interested": (0, 3),
    "texture.deep": (0, 4),
    "texture.shrill": (0, 4),
    "texture.husky": (0, 4),
    "texture.raspy": (0, 4),
    "texture.soft": (0, 4),
    "texture.authoritative": (0, 4),
}

ACCENTS: dict[str, str] = {
    "american": "accents_en.american",
    "australian": "accents_en.australian",
    "british": "accents_en.british",
    "indian": "accents_en.indian",
    "irish": "accents_en.irish",
    "scottish": "accents_en.scottish",
    "welsh": "accents_en.welsh",
    "german": "accents_l1.deu",
    "french": "accents_l1.fra",
    "hindi": "accents_l1.hin",
    "polish": "accents_l1.pol",
    "portuguese": "accents_l1.por",
    "spanish": "accents_l1.spa",
    "vietnamese": "accents_l1.vie",
}
for _cid in ACCENTS.values():
    SAFE[_cid] = (0, 1.5)
ACCENT_DOSE = 1.2

TEXTURES: dict[str, str] = {
    "deep": "texture.deep",
    "bright": "texture.shrill",
    "husky": "texture.husky",
    "raspy": "texture.raspy",
    "soft": "texture.soft",
    "authoritative": "texture.authoritative",
}
TEXTURE_DOSE = 1.5
HESITANT_DOSE = 4.0

# Full doses (intensity 1.0); the direction's intensity scales them.
EMOTION_CONTROLS: dict[str, dict[str, float]] = {
    "neutral": {},
    "happy": {"emotions.happy": 3},
    "amused": {"emotions.happy": 2, "expressive.expressive": 3},
    "excited": {"emotions.happy": 2, "mood.arousal": 2.5, "expressive.expressive": 5},
    "warm": {"emotions.content": 2, "mood.valence": 1.5},
    "calm": {"emotions.serene": 3},
    "sad": {"emotions.sad": 3},
    "wistful": {"emotions.sad": 1.5, "emotions.serene": 1.5},
    "nervous": {"emotions.scared": 3, "disfluency.disfluent": 3},
    "annoyed": {"emotions.angry": 1.5, "mood.valence": -1.5},
    "angry": {"emotions.angry": 3},
    "surprised": {"emotions.surprised": 2.5, "mood.arousal": 1.5},
    "proud": {"emotions.proud": 2.5},
    "confused": {"emotions.confused": 2},
    "interested": {"emotions.interested": 2.5, "delivery.conversational": 1.5},
}

# Energy/valence of -1..1 map onto these doses.
ENERGY_CONTROLS = {"mood.arousal": 2.0, "expressive.expressive": 3.0}
VALENCE_CONTROLS = {"mood.valence": 1.5}

# Word-level styles (all spannable in the studio).
SPAN_CONTROLS: dict[str, dict[str, float]] = {
    "whisper": {"delivery.whisper": 1.2},
    "shout": {"delivery.loud": 1.5, "mood.arousal": 1.5},
    "soft": {"delivery.whisper": 0.5, "emotions.serene": 2},
    "emph": {"emphasis_nv_nodur.emphasis": 8, "mood.arousal": 1},
    "laughter": {"emotions.happy": 3, "expressive.expressive": 4},
    "sigh": {"emotions.sad": 2, "delivery.whisper": 0.5},
    "surprise": {"mood.arousal": 2, "expressive.expressive": 4},
    "confirmation": {"emphasis_nv_nodur.emphasis": 6},
    "dissatisfaction": {"disfluency.disfluent": 4, "mood.valence": -1.5},
}


def _add(target: dict[str, float], doses: dict[str, float], scale: float = 1.0):
    for cid, dose in doses.items():
        target[cid] = target.get(cid, 0.0) + dose * scale


def _clamped(doses: dict[str, float]) -> dict[str, float]:
    out = {}
    for cid, dose in doses.items():
        low, high = SAFE.get(cid, (0.0, 0.0))
        value = round(max(low, min(high, dose)), 2)
        if value:
            out[cid] = value
    return out


def _unit(value: float) -> float:
    return max(-1.0, min(1.0, float(value or 0.0)))


class Zonos3Direction(DirectionAdapter):
    """ZONOS3-Mini has no pause tokens in the released checkpoint, so pauses are split by the voice manager."""

    native_pauses = False

    def render(self, text: str, direction: SpeechDirection) -> Rendered:
        marked = parse_marks(remove_pauses(text))
        request: dict = {}

        doses: dict[str, float] = {}
        if direction.emotion:
            _add(doses, EMOTION_CONTROLS.get(direction.emotion, {}), max(0.0, min(1.0, direction.intensity)))
        _add(doses, ENERGY_CONTROLS, _unit(direction.energy))
        _add(doses, VALENCE_CONTROLS, _unit(direction.valence))
        traits = direction.traits
        if traits is not None:
            if traits.accent in ACCENTS:
                _add(doses, {ACCENTS[traits.accent]: ACCENT_DOSE}, max(0.0, min(1.0, traits.accent_strength)))
            for name, weight in traits.textures:
                if name in TEXTURES:
                    _add(doses, {TEXTURES[name]: TEXTURE_DOSE}, max(0.0, min(1.0, weight)))
            if traits.hesitant:
                _add(doses, {"disfluency.disfluent": HESITANT_DOSE}, max(0.0, min(1.0, traits.hesitant)))
        controls = _clamped(doses)
        if controls:
            request["controls"] = controls

        if direction.word_marks:
            spans = []
            for span in marked.spans:
                span_controls = _clamped(SPAN_CONTROLS.get(span.style, {}))
                if span_controls:
                    spans.append({"start": span.start, "end": span.end, "controls": span_controls})
            if spans:
                request["control_spans"] = spans

        if direction.loudness_lufs is not None:
            request["condition_values"] = {"loudness": float(direction.loudness_lufs)}
        if direction.tight_start:
            request["silence_prefill_frames"] = 0
        return Rendered(marked.text, request)
