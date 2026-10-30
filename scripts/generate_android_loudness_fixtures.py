"""Synthetic MP3s and independent FFmpeg EBU-R128 references, only in .test-runtime.
Run using backend\\venv\\Scripts\\python. No user media is opened.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    args = parser.parse_args()
    if not args.ffmpeg:
        parser.error("Pass --ffmpeg PATH")
    root = Path(__file__).resolve().parents[1]
    output = root / ".test-runtime" / "android-fixtures"
    output.mkdir(parents=True, exist_ok=True)
    fixtures = [
        ("level-loud", 48000, 1, 3, 1000, .8),
        ("level-quiet", 44100, 1, 3, 200, .02),
        ("level-stereo", 48000, 2, 3, 6000, .3),
        ("level-gated", 44100, 1, 9, 1000, .5),
        ("level-32k", 32000, 1, 3, 1000, .4),
    ]
    references = []
    for name, rate, channels, seconds, frequency, amplitude in fixtures:
        pcm = output / f"{name}.wav"
        mp3 = output / f"{name}.mp3"
        with wave.open(str(pcm), "wb") as audio:
            audio.setnchannels(channels); audio.setsampwidth(2); audio.setframerate(rate)
            for start in range(0, rate * seconds, 4096):
                data = bytearray()
                for frame in range(start, min(start + 4096, rate * seconds)):
                    scale = amplitude
                    if name == "level-gated":
                        scale = .001 if frame < rate * 3 else amplitude if frame < rate * 6 else 0
                    for channel in range(channels):
                        value = scale * math.sin(2 * math.pi * frequency * frame / rate + channel * math.pi / 2)
                        data.extend(struct.pack("<h", round(value * 32767)))
                audio.writeframes(data)
        subprocess.run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(pcm),
                        "-c:a", "libmp3lame", "-b:a", "128k", "-y", str(mp3)], check=True)
        result = subprocess.run([args.ffmpeg, "-hide_banner", "-nostats", "-i", str(mp3),
                                 "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True, check=True)
        integrated = float(re.findall(r"I:\s*(-?[\d.]+) LUFS", result.stderr)[-1])
        references.append({"id": name, "lufs": integrated, "rate": rate, "channels": channels})
        print(f"{name}: {integrated:.1f} LUFS")
    (output / "loudness-reference.json").write_text(json.dumps(references, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
