"""Encode persona reference clips to ZONOS3 reference codes once.

The codes let the engine use reference-mode cloning without the zonos3-server
ever loading its codec encoders. Encoding runs Zyphra's ``zac-encoder-cli``
(needs ``zac-encoder.gguf`` + ``ssl-teacher.gguf`` and ffmpeg for MP3 clips).
Codes are named ``<voice>.<clip fingerprint>.codes.npy``; a re-recorded clip
gets new codes and the old ones are removed.
"""

import glob
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from announcer.zonos3_engine import codes_path

logger = logging.getLogger(__name__)


@dataclass
class Encoder:
    exe: str
    encoder_gguf: str
    teacher_gguf: str
    gpu: bool = True
    run: Callable = field(default=subprocess.run)

    def encode(self, clip: str, out_path: str) -> tuple[bool, str]:
        cmd = [self.exe, self.encoder_gguf, self.teacher_gguf, "--audio", clip, out_path,
               "--gpu" if self.gpu else "--cpu"]
        done = self.run(cmd, capture_output=True, text=True)
        message = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()
        return done.returncode == 0 and os.path.exists(out_path), (message[-1] if message else "")


def _stale_codes(codes_dir: str, voice_id: str, keep: str) -> list[str]:
    pattern = os.path.join(glob.escape(codes_dir), f"{glob.escape(voice_id)}.*.codes.npy")
    return [p for p in glob.glob(pattern) if os.path.abspath(p) != os.path.abspath(keep)]


def encode_missing(clips: dict[str, str], codes_dir: str, encoder: Encoder, force: bool = False) -> dict[str, str]:
    """Encode every clip without current codes. Returns {voice: "encoded" | "current" | "failed"}."""
    os.makedirs(codes_dir, exist_ok=True)
    results = {}
    for voice_id, clip in sorted(clips.items()):
        target = codes_path(codes_dir, voice_id, clip)
        if os.path.exists(target) and not force:
            results[voice_id] = "current"
            continue
        ok, message = encoder.encode(clip, target)
        if not ok:
            logger.error(f"Zonos3: encoding '{voice_id}' failed: {message}")
            if os.path.exists(target):
                os.remove(target)
            results[voice_id] = "failed"
            continue
        for old in _stale_codes(codes_dir, voice_id, target):
            os.remove(old)
        logger.info(f"Zonos3: encoded '{voice_id}' ({message})")
        results[voice_id] = "encoded"
    return results
