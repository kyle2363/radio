"""Generate silent MP3s for Android instrumentation, entirely in .test-runtime.

Run with backend\\venv\\Scripts\\python -B scripts/generate_android_test_audio.py
and optionally --ffmpeg PATH. No library audio or metadata is read or changed.
"""
import argparse
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    args = parser.parse_args()
    if not args.ffmpeg:
        parser.error("ffmpeg is required; pass --ffmpeg PATH")
    root = Path(__file__).resolve().parents[1]
    destination = root / ".test-runtime" / "android-fixtures"
    destination.mkdir(parents=True, exist_ok=True)
    for name, seconds in (("silence-long.mp3", "8"), ("silence-short.mp3", "0.6"), ("silence-background.mp3", "60")):
        target = destination / name
        subprocess.run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "anullsrc=channel_layout=mono:sample_rate=44100", "-t", seconds,
                        "-c:a", "libmp3lame", "-b:a", "64k", "-y", str(target)], check=True)
        print(target.relative_to(root))


if __name__ == "__main__":
    main()
