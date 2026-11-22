"""Encode the persona reference clips to ZONOS3 reference codes (run once, and after changing a clip).

Usage (from the repo root, with the project venv):
    backend\\venv\\Scripts\\python scripts/zonos3_encode_voices.py [--zonos-dir DIR] [--cpu] [--force]

--zonos-dir is the ZONOS3-Mini servers/ggml folder (default: ZONOS3_WORK_DIR from .env). It must hold
build-cuda/Release/zac-encoder-cli.exe (or build/...) and gguf/zac-encoder.gguf + gguf/ssl-teacher.gguf.
Codes are written to ZONOS3_CODES_DIR (default data/zonos3_voices). Only that folder is written;
the clips in backend/announcer/voices are read, never changed.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config  # noqa: E402
from announcer.zonos3_engine import _list_reference_clips  # noqa: E402
from announcer.zonos3_voices import Encoder, encode_missing  # noqa: E402


def find_cli(zonos_dir: str) -> str | None:
    for rel in ("build-cuda/Release/zac-encoder-cli.exe", "build/Release/zac-encoder-cli.exe",
                "build-cuda/zac-encoder-cli", "build/zac-encoder-cli"):
        path = os.path.join(zonos_dir, rel)
        if os.path.isfile(path):
            return path
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zonos-dir", default=config.ZONOS3_WORK_DIR, help="ZONOS3-Mini servers/ggml folder")
    ap.add_argument("--cpu", action="store_true", help="encode on the CPU instead of the GPU")
    ap.add_argument("--force", action="store_true", help="re-encode clips that already have codes")
    ap.add_argument("--voices-dir", help="folder of persona clips (default backend/announcer/voices)")
    args = ap.parse_args()

    if not args.zonos_dir:
        print("Set ZONOS3_WORK_DIR in .env or pass --zonos-dir.")
        return 2
    cli = find_cli(args.zonos_dir)
    gguf = os.path.join(args.zonos_dir, "gguf")
    enc_gguf, teacher = os.path.join(gguf, "zac-encoder.gguf"), os.path.join(gguf, "ssl-teacher.gguf")
    missing = [p for p in (cli, enc_gguf, teacher) if not p or not os.path.isfile(p)]
    if missing:
        print("Missing:", ", ".join(str(m) for m in missing) or "zac-encoder-cli")
        return 2

    if args.voices_dir:
        from announcer import omnivoice_engine
        omnivoice_engine.VOICES_DIR = args.voices_dir
    clips = _list_reference_clips()
    if not clips:
        print("No reference clips found in backend/announcer/voices.")
        return 1
    encoder = Encoder(exe=cli, encoder_gguf=enc_gguf, teacher_gguf=teacher, gpu=not args.cpu)
    results = encode_missing(clips, config.ZONOS3_CODES_DIR, encoder, force=args.force)
    for voice, status in results.items():
        print(f"{status:8s} {voice}")
    print(f"Codes in {config.ZONOS3_CODES_DIR}")
    return 1 if "failed" in results.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
