"""
Whisper lyrics transcriber for remaining Pinn HateBerry tracks.
Uses local openai-whisper.

Usage:
  python whisper_lyrics.py
"""

import json
import os
import sys
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MUSIC_DIR = BASE_DIR / "audio_files"
UNDERSTAND_DIR = BASE_DIR / "docs" / "understand_new"

TRACKS = [
    ("Pinn HateBerry - You Missed Me Anyway", "new13mp3"),
    ("Pinn HateBerry - Slow Day", "new13mp3"),
    ("Pinn HateBerry - Plastic Sunset", "new13mp3"),
    ("Pinn HateBerry - Midnight To Moonlight", "new13mp3"),
    ("Pinn HateBerry - Bubble Tea", "new13mp3"),
    ("Pinn HateBerry & Cullen HateBerry - Neon Street", "new13mp3"),
    ("Pinn HateBerry & Kirby HateBerry - On My Mind", "new13mp3"),
    ("Kirby HateBerry & Cullen HateBerry & Pinn HateBerry - U & I (HateBerry Festival Mix)", "new13mp3"),
    ("Pinn HateBerry - Colors of You", "new12 mp3"),
    ("Pinn HateBerry & Cullen HateBerry - Will Survive", "new12 mp3"),
]


def load_model():
    """Load whisper model with progress feedback."""
    print("Loading whisper base model (first time - downloading ~1GB, may take a few minutes)...")
    print("  Progress will appear below...")
    import whisper

    model = whisper.load_model("base", device="cpu")
    print("  Model loaded successfully!")
    return model


def transcribe(model, mp3_path):
    """Transcribe an MP3 file."""
    print(f"  Transcribing {mp3_path.name}...", flush=True)
    result = model.transcribe(
        str(mp3_path),
        language="en",
        verbose=False,
        task="transcribe",
    )
    return result["text"].strip()


def update_json(json_path, lyrics):
    """Update the lyrics field in the JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list) and len(data) > 0:
        for entry in data:
            if entry.get("lyrics") == "TODO":
                entry["lyrics"] = lyrics
    elif isinstance(data, dict):
        if data.get("lyrics") == "TODO":
            data["lyrics"] = lyrics

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 60)
    print("Whisper Lyrics Transcriber")
    print("=" * 60)
    print(f"Found {len(TRACKS)} tracks to process")
    print(f"Music dir: {MUSIC_DIR}")
    print(f"Output dir: {UNDERSTAND_DIR}")
    print()

    # Load model once
    model = load_model()

    results = []
    for i, (json_name, subfolder) in enumerate(TRACKS, 1):
        json_path = UNDERSTAND_DIR / f"{json_name}.json"
        print(f"\n[{i}/{len(TRACKS)}] {json_name}")

        # Find MP3
        if subfolder:
            mp3_path = MUSIC_DIR / subfolder / f"{json_name}.mp3"
        else:
            mp3_path = None

        if not mp3_path or not mp3_path.exists():
            # Fallback: scan all subdirs
            for root, _, files in os.walk(MUSIC_DIR):
                for f in files:
                    normalized = json_name.replace(" ", "").lower()
                    fnormalized = f.replace(" ", "").lower()
                    if normalized in fnormalized and f.endswith(".mp3"):
                        mp3_path = Path(root) / f
                        break
                if mp3_path:
                    break

        if not mp3_path or not mp3_path.exists():
            print(f"  ERROR: MP3 not found")
            results.append((json_name, "ERROR: MP3 not found"))
            continue

        # Transcribe
        try:
            lyrics = transcribe(model, mp3_path)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append((json_name, f"ERROR: {e}"))
            continue

        # Clean up
        lyrics = re.sub(r'\[\d+m\d+s\]', '', lyrics)
        lyrics = re.sub(r'\[\d+:\d+\.\d+\]', '', lyrics)
        lyrics = re.sub(r'\s+', ' ', lyrics).strip()

        if not lyrics:
            print(f"  WARNING: Empty transcription (likely instrumental)")
            lyrics = "Instrumental"

        # Write back
        try:
            update_json(json_path, lyrics)
            print(f"  OK: {len(lyrics)} chars -> {json_path.name}")
            results.append((json_name, "OK"))
        except Exception as e:
            print(f"  ERROR writing: {e}")
            results.append((json_name, f"ERROR writing: {e}"))

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(TRACKS)} files processed")
    ok = sum(1 for _, r in results if r == "OK")
    err = sum(1 for _, r in results if r != "OK")
    print(f"  OK: {ok}")
    print(f"  Errors: {err}")
    if err > 0:
        print("\nFailed files:")
        for name, r in results:
            if r != "OK":
                print(f"  - {name}: {r}")

    print(f"\nDone! Check {UNDERSTAND_DIR}")


if __name__ == "__main__":
    main()
