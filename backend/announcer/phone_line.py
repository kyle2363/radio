"""Caller phone line: an occasional light phone band and occasional background ambience.

Each phone-in rolls its own ``CallScene``, whoever is calling:

- the phone line (rumble and top air gently rolled off, a breath of line hiss)
  on about a third of calls (``PHONE_CHANCE``); the rest sound like the studio;
- ambience from a kitchen, a street or rain on a window on under half of calls
  (``AMBIENCE_WEIGHTS``, where none is the most likely), sitting well under the voice.

Nothing repeats. A scene randomizes its level, tone and how busy it is, and every
line renders fresh noise and fresh events from it, so two calls never share audio
and the lines of one call share a place but not a loop. Everything is synthesized,
so there are no sound files to ship.

The source WAV (often a cached TTS line) is never modified; the result is a new
file beside it, or in ``out_dir``. A call with neither part returns the line as is.
"""

from __future__ import annotations

import logging
import os
import random
import uuid
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

PEAK_LIMIT = 0.89
PHONE_CHANCE = 0.35
AMBIENCE_WEIGHTS: dict[str | None, float] = {None: 0.55, "street": 0.2, "kitchen": 0.125, "rain": 0.125}
SCENE_LEVEL_DB = {"street": -26.0, "kitchen": -30.0, "rain": -31.0}  # under the voice's speaking level
LEVEL_SPREAD_DB = 2.0   # per call
HISS_LEVEL_DB = -58.0
FADE_SECS = 0.04
PHONE_LOW_CUT_HZ = 90
PHONE_HIGH_CUT_HZ = 7500


# ---- building blocks ----

def _filt(data: np.ndarray, kind: str, freq, rate: int, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, sosfilt
    nyq = rate / 2
    if isinstance(freq, (tuple, list)):
        freq = [min(max(f, 10.0), nyq * 0.95) for f in freq]
    else:
        freq = min(max(freq, 10.0), nyq * 0.95)
    return sosfilt(butter(order, freq, btype=kind, fs=rate, output="sos"), data)


def _norm(data: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
    return data / rms if rms > 1e-12 else data


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spec), dtype=float)
    f[0] = 1.0
    return _norm(np.fft.irfft(spec / np.sqrt(f), n))


def _brown(n: int, rate: int, rng: np.random.Generator) -> np.ndarray:
    return _norm(_filt(np.cumsum(rng.standard_normal(n)), "highpass", 20, rate))


def _drift(n: int, rate: int, rng: np.random.Generator, every: float, depth: float) -> np.ndarray:
    """A slow random gain curve around 1 (smooth points every ``every`` seconds)."""
    points = max(2, int(n / rate / every) + 2)
    knots = 1.0 + depth * (rng.random(points) * 2 - 1)
    return np.interp(np.arange(n), np.linspace(0, n, points), knots)


def _event_times(n: int, rate: int, rng: np.random.Generator, per_sec: float) -> list[int]:
    count = rng.poisson(per_sec * n / rate)
    return sorted(int(t) for t in rng.integers(0, max(1, n), count))


def _add_at(bed: np.ndarray, start: int, sound: np.ndarray):
    end = min(len(bed), start + len(sound))
    if end > start:
        bed[start:end] += sound[: end - start]


def _ping(rate: int, freq: float, secs: float, rng: np.random.Generator) -> np.ndarray:
    """A small struck sound (glass, spoon, pot lid) with a few inharmonic partials."""
    t = np.arange(int(rate * secs)) / rate
    out = np.zeros_like(t)
    for ratio, amp in ((1.0, 1.0), (2.76, 0.5), (5.4, 0.25)):
        out += amp * np.sin(2 * np.pi * freq * ratio * (1 + 0.002 * rng.standard_normal()) * t)
    return out * np.exp(-t * rng.uniform(18, 40))


def _babble(n: int, rate: int, rng: np.random.Generator, voices: int) -> np.ndarray:
    """Distant chatter: band-limited noise with syllable-rate wobble, several talkers."""
    out = np.zeros(n)
    t = np.arange(n) / rate
    for _ in range(voices):
        centre = rng.uniform(350, 1100)
        talker = _filt(rng.standard_normal(n), "bandpass", (centre * 0.6, centre * 2.2), rate)
        syllables = np.clip(np.sin(2 * np.pi * rng.uniform(3, 6) * t + rng.uniform(0, 6.3)), 0, None)
        phrases = _drift(n, rate, rng, rng.uniform(0.8, 2.0), 1.0).clip(0, None)
        out += talker * syllables * phrases
    return _norm(out)


# ---- scenes ----

def scene_params(kind: str, rng: random.Random) -> dict:
    """The randomized character of one call's place."""
    return {
        "hum_hz": rng.choice((50.0, 60.0)) * rng.uniform(0.99, 1.01),
        "tone": rng.uniform(0.7, 1.4),        # brightness of the main noise
        "busy": rng.uniform(0.5, 1.5),        # how often things happen
        "level_db": SCENE_LEVEL_DB.get(kind, -28.0) + rng.uniform(-LEVEL_SPREAD_DB, LEVEL_SPREAD_DB),
        "variant": rng.random(),
    }


def _kitchen(n, rate, rng, p):
    t = np.arange(n) / rate
    bed = 0.4 * _filt(_pink(n, rng), "lowpass", 2000, rate)
    bed += 0.3 * (np.sin(2 * np.pi * p["hum_hz"] * t) + 0.5 * np.sin(2 * np.pi * p["hum_hz"] * 3 * t))  # fridge
    if p["variant"] > 0.35:  # something sizzling or simmering
        sizzle = _filt(rng.standard_normal(n), "bandpass", (2500 * p["tone"], 9000), rate)
        crackle = (rng.random(n) < 0.002 * p["busy"]) * rng.standard_normal(n) * 6
        bed += 0.7 * sizzle * _drift(n, rate, rng, 0.7, 0.5) + _filt(crackle, "highpass", 2000, rate)
    for start in _event_times(n, rate, rng, 0.5 * p["busy"]):  # pots, spoons, plates
        _add_at(bed, start, rng.uniform(0.8, 2.0) * _ping(rate, rng.uniform(900, 3200), 0.5, rng))
    return bed


def _street(n, rate, rng, p):
    bed = 0.5 * _filt(_pink(n, rng), "lowpass", 1500 * p["tone"], rate)
    for start in _event_times(n, rate, rng, 0.35 * p["busy"]):  # cars passing
        secs = rng.uniform(2.0, 4.0)
        k = np.arange(int(rate * secs))
        swell = np.exp(-((k / rate - secs / 2) ** 2) / (2 * (secs / 6) ** 2))
        whoosh = _filt(rng.standard_normal(len(k)), "bandpass", (150, 1500 * p["tone"]), rate) * swell
        _add_at(bed, max(0, start - len(k) // 2), rng.uniform(0.8, 1.8) * whoosh)
    for start in _event_times(n, rate, rng, 0.06 * p["busy"]):  # a distant horn
        k = np.arange(int(rate * rng.uniform(0.25, 0.6))) / rate
        f = rng.uniform(330, 450)
        horn = (np.sign(np.sin(2 * np.pi * f * k)) + np.sign(np.sin(2 * np.pi * f * 1.26 * k))) * np.hanning(len(k))
        _add_at(bed, start, 0.35 * _filt(horn, "lowpass", 2000, rate))
    if p["variant"] > 0.6:  # people nearby
        bed += 0.25 * _babble(n, rate, rng, 3)
    return bed


def _rain(n, rate, rng, p):
    bed = 0.8 * _filt(rng.standard_normal(n), "bandpass", (600 * p["tone"], 7000), rate) * _drift(n, rate, rng, 4.0, 0.2)
    bed += 0.3 * _filt(_brown(n, rate, rng), "lowpass", 200, rate)
    drops = (rng.random(n) < 0.004 * p["busy"]) * rng.standard_normal(n) * 8
    return bed + _filt(drops, "bandpass", (1500, 6000), rate)


SCENES = {"kitchen": _kitchen, "street": _street, "rain": _rain}


def render_scene(kind: str, rate: int, n: int, rng: np.random.Generator, params: dict) -> np.ndarray:
    """``n`` samples of a place, peak-normalized (level is set by the caller)."""
    bed = SCENES[kind](n, rate, rng, params)
    peak = float(np.max(np.abs(bed))) if bed.size else 0.0
    return bed / peak if peak > 1e-12 else bed


@dataclass
class CallScene:
    """How one call sounds, fixed for its length: phone line or not, and where (or nowhere)."""
    kind: str | None = None
    phone: bool = False
    rng: random.Random = field(default_factory=lambda: random.Random(int.from_bytes(os.urandom(8), "little")))
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind and not self.params:
            self.params = scene_params(self.kind, self.rng)

    @classmethod
    def pick(cls, rng: random.Random | None = None, phone: bool = True, ambience: bool = True) -> "CallScene":
        """Roll a call: each part can be switched off by its setting."""
        rng = rng or random.Random(int.from_bytes(os.urandom(8), "little"))
        on_phone = phone and rng.random() < PHONE_CHANCE
        kind = rng.choices(list(AMBIENCE_WEIGHTS), weights=list(AMBIENCE_WEIGHTS.values()))[0] if ambience else None
        return cls(kind=kind, phone=on_phone, rng=random.Random(rng.getrandbits(63)))

    @property
    def plain(self) -> bool:
        return not self.phone and not self.kind

    def line_rng(self) -> np.random.Generator:
        """Fresh noise for each line, so lines of a call never share audio."""
        return np.random.default_rng(self.rng.getrandbits(63))


# ---- processing ----

def _speech_rms(data: np.ndarray, rate: int) -> float:
    """RMS of the voiced part (frames within 35 dB of the loudest)."""
    frame = max(1, int(rate * 0.03))
    usable = data[: len(data) // frame * frame]
    if not usable.size:
        return 0.0
    rms = np.sqrt(np.mean(usable.reshape(-1, frame) ** 2, axis=1))
    voiced = rms[rms > rms.max() * 10 ** (-35 / 20)]
    return float(np.sqrt(np.mean(voiced ** 2))) if voiced.size else 0.0


def _phone_band(data: np.ndarray, rate: int) -> np.ndarray:
    """Light: roll off rumble below ~90 Hz and a little air above ~7.5 kHz."""
    data = _filt(data, "highpass", PHONE_LOW_CUT_HZ, rate, order=2)
    return _filt(data, "lowpass", PHONE_HIGH_CUT_HZ, rate, order=1)


def _fade(data: np.ndarray, rate: int) -> np.ndarray:
    k = min(len(data) // 2, int(rate * FADE_SECS))
    if k:
        ramp = np.linspace(0.0, 1.0, k)
        data[:k] *= ramp
        data[-k:] *= ramp[::-1]
    return data


def process(wav_path: str, scene: CallScene, out_dir: str | None = None) -> str:
    """Return ``wav_path`` as heard in ``scene``: a new WAV, or the same path for a plain call.

    Falls back to ``wav_path`` itself if anything goes wrong.
    """
    if scene.plain:
        return wav_path
    try:
        import soundfile as sf
        info = sf.info(wav_path)
        data, rate = sf.read(wav_path, always_2d=True)
        voice = np.stack([_phone_band(data[:, c], rate) for c in range(data.shape[1])], axis=1) if scene.phone else data
        level = _speech_rms(voice.mean(axis=1), rate)
        rng = scene.line_rng()
        extra = np.zeros(len(voice))
        if level > 0 and scene.kind:
            bed = render_scene(scene.kind, rate, len(voice), rng, scene.params)
            if scene.phone:
                bed = _phone_band(bed, rate)
            bed_rms = float(np.sqrt(np.mean(bed ** 2)))
            if bed_rms > 1e-12:
                extra += _fade(bed / bed_rms, rate) * level * 10 ** (scene.params["level_db"] / 20)
        if level > 0 and scene.phone:
            hiss = _norm(_filt(rng.standard_normal(len(voice)), "bandpass", (1500, 5000), rate))
            extra += hiss * level * 10 ** (HISS_LEVEL_DB / 20)
        out = voice + extra[:, None]
        peak = float(np.max(np.abs(out))) if out.size else 0.0
        if peak > PEAK_LIMIT:
            out *= PEAK_LIMIT / peak
        stem = os.path.splitext(os.path.basename(wav_path))[0]
        tag = "_".join(part for part in ("phone" if scene.phone else "", scene.kind or "") if part)
        target_dir = out_dir or os.path.dirname(os.path.abspath(wav_path))
        out_path = os.path.join(target_dir, f"{stem}_call_{tag}_{uuid.uuid4().hex[:8]}.wav")
        sf.write(out_path, out if out.shape[1] > 1 else out[:, 0], rate, subtype=info.subtype)
        return out_path
    except Exception as e:
        logger.warning(f"Call sound skipped for {wav_path}: {e}")
        return wav_path
