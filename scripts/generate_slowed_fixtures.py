"""Generate Slowed Radio comparison fixtures (checkpoint 14A, plan section 4.4).

Writes a short synthetic source and the desktop's pedalboard render of it for each preset into
.test-runtime/android-fixtures/slowed/. The Android JVM test renders the same source through the phone chain
(Freeverb + limiter, then Sonic tape resample) and compares duration, pitch and reverb decay.

The desktop chain mirrors backend/engine/dj_effects.py: time stretch -> pitch shift -> reverb -> limiter, with the
plan's tape-style presets (pitch shift = 12 * log2(rate) semitones).

Run with the backend venv:  backend\\venv\\Scripts\\python -B scripts/generate_slowed_fixtures.py
"""

import json
import math
import os
import shutil
import subprocess

import numpy as np
from pedalboard import Limiter, Pedalboard, PitchShift, Reverb, time_stretch
from pedalboard.io import AudioFile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".test-runtime", "android-fixtures", "slowed")
RATE = 44_100

# Keep in step with SlowedPreset.kt and the desktop seed rows.
PRESETS = {
    "really_slow": dict(rate=0.75, room=0.60, damping=0.45, wet=0.22),
    "slowed": dict(rate=0.85, room=0.40, damping=0.50, wet=0.12),
    "lofi": dict(rate=0.90, room=0.55, damping=0.60, wet=0.20),
}


def source() -> np.ndarray:
    """2 s of a 440 Hz tone with harmonics, 2 s of silence for the reverb tail, a 4 ms click, then silence: 6 s stereo."""
    t = np.arange(int(RATE * 6)) / RATE
    tone = np.zeros_like(t)
    body = t < 2.0
    for n, amp in ((1, 0.30), (2, 0.10), (3, 0.05)):
        tone[body] += amp * np.sin(2 * math.pi * 440 * n * t[body])
    fade = int(RATE * 0.01)
    edge = np.where(body)[0][-1]
    tone[edge - fade:edge] *= np.linspace(1, 0, fade)
    click = (t >= 4.0) & (t < 4.004)
    tone[click] = 0.6
    return np.stack([tone, tone]).astype(np.float32)


def desktop(audio: np.ndarray, preset: dict) -> np.ndarray:
    stretched = time_stretch(audio, RATE, stretch_factor=preset["rate"], high_quality=True, transient_mode="crisp")
    board = Pedalboard([
        PitchShift(semitones=12 * math.log2(preset["rate"])),
        Reverb(room_size=preset["room"], damping=preset["damping"], wet_level=preset["wet"], dry_level=1.0, width=1.0, freeze_mode=0.0),
        Limiter(threshold_db=-1.0),
    ])
    return board(stretched, RATE)


def write(path: str, audio: np.ndarray) -> None:
    with AudioFile(path, "w", RATE, num_channels=audio.shape[0], bit_depth=16) as f:
        f.write(audio)


def line(path: str) -> None:
    """A 2 s spoken-line stand-in encoded exactly like a desktop talk clip (AAC-LC mono 64 kbps in MP4)."""
    binary = os.environ.get("RADIO_FFMPEG") or shutil.which("ffmpeg")
    if not binary:
        print("ffmpeg not found: skipped line.m4a (Slowed instrumented tests need it)")
        return
    t = np.arange(int(RATE * 2)) / RATE
    voice = (0.2 * np.sin(2 * math.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * math.pi * 3 * t))).astype(np.float32)
    wav = path + ".wav"
    write(wav, np.stack([voice]))
    subprocess.run([binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", wav, "-ac", "1", "-ar", "44100",
                    "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", "-f", "mp4", path], check=True)
    os.remove(wav)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    audio = source()
    write(os.path.join(OUT, "source.wav"), audio)
    for name, preset in PRESETS.items():
        write(os.path.join(OUT, f"desktop_{name}.wav"), desktop(audio, preset))
    with open(os.path.join(OUT, "presets.json"), "w", encoding="utf-8") as f:
        json.dump(PRESETS, f, indent=2)
    line(os.path.join(OUT, "line.m4a"))
    print(f"Wrote Slowed fixtures to {OUT}")


if __name__ == "__main__":
    main()
