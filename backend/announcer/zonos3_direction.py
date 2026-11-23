"""ZONOS3-Mini adapter for engine-neutral speech direction.

Turns a ``SpeechDirection`` and inline marks into zonos3-server request fields:

- ``controls``: steering doses for emotion, energy/valence and persona hesitation
- ``control_spans``: word-level steering for whispered, shouted, soft and
  emphasised words, and for the OmniVoice non-verbal tags (ZONOS3 has no
  laugh or sigh sounds, so ``[laughter]`` becomes an amused delivery of the
  sentence)
- ``condition_values.loudness``: the target loudness
- ``silence_prefill_frames: 0``: speech starts with no lead-in silence
- ``steer_hold`` / ``steer_onset_seconds`` / ``steer_ramp_seconds``: ease the
  controls off early in each line so delivery does not climb (``STEER_EASING``)

Every id is a real control of the released steering bundle (``GET /v2/controls``,
November 21, 2026) and every dose is clamped to the studio's "safe" slider range
(``SAFE``). The engine drops ids and fields the running server does not offer.
Speaker-embedding controls (accents, texture, age, pitch) are not used: the radio
speaks in reference mode, where the server refuses them, so a persona's accent and
timbre come from its reference clip. Tune the tables below by ear; a different
model gets its own adapter instead.
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
}

HESITANT_DOSE = 4.0

# Full doses (intensity 1.0); the direction's intensity scales them.
EMOTION_CONTROLS: dict[str, dict[str, float]] = {
    "neutral": {},
    # Upbeat emotions keep arousal and expressiveness low: on a cloned voice they climb
    # in pitch over a line (the model copies its own style), heard as shouting.
    "happy": {"emotions.happy": 2.5},
    "amused": {"emotions.happy": 1.5, "expressive.expressive": 2},
    "excited": {"emotions.happy": 2, "mood.arousal": 1.5, "expressive.expressive": 2.5},
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

# Steer then hold: the request-level controls run at full dose for the onset, ramp to
# hold x dose, then stay there. The server default (4 s onset, 0.35 hold) starts too late
# for 5-12 s talk lines, which climb within their first seconds. Spans stay constant.
STEER_EASING = {"steer_hold": 0.25, "steer_onset_seconds": 1.5, "steer_ramp_seconds": 1.0}

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


def calm_exclamations(text: str) -> str:
    """ZONOS3 reads "!" as a cue to shout, so it becomes "." (or a space after "?" or ".").

    The emotion controls carry the excitement instead. One character for one, so word
    spans over the text keep their positions.
    """
    out = []
    for ch in text:
        if ch == "!":
            ch = " " if out and out[-1] in ".?" else "."
        out.append(ch)
    return "".join(out)


class Zonos3Direction(DirectionAdapter):
    """ZONOS3-Mini has no pause tokens in the released checkpoint, so pauses are split by the voice manager."""

    native_pauses = False

    def render(self, text: str, direction: SpeechDirection) -> Rendered:
        marked = parse_marks(remove_pauses(text))
        marked.text = calm_exclamations(marked.text)
        request: dict = {}

        doses: dict[str, float] = {}
        if direction.emotion:
            _add(doses, EMOTION_CONTROLS.get(direction.emotion, {}), max(0.0, min(1.0, direction.intensity)))
        _add(doses, ENERGY_CONTROLS, _unit(direction.energy))
        _add(doses, VALENCE_CONTROLS, _unit(direction.valence))
        traits = direction.traits
        if traits is not None:
            if traits.hesitant:
                _add(doses, {"disfluency.disfluent": HESITANT_DOSE}, max(0.0, min(1.0, traits.hesitant)))
        controls = _clamped(doses)
        if controls:
            request["controls"] = controls
            request.update(STEER_EASING)

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
