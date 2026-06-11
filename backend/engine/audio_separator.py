"""Vocal separation via audio-separator (UVR-MDX). Caches separated stems by file hash."""

import hashlib
import os
import shutil

import config

_SEPARATOR_INSTANCE = None


def _file_hash(filepath: str) -> str:
    """SHA256 of filepath string (consistent with scanner dedup)."""
    return hashlib.sha256(filepath.encode("utf-8")).hexdigest()[:16]


def _separator():
    """Lazy-loaded singleton separator instance."""
    global _SEPARATOR_INSTANCE
    if _SEPARATOR_INSTANCE is None:
        from audio_separator.separator import Separator
        os.makedirs(config.SEPARATOR_MODEL_DIR, exist_ok=True)
        os.makedirs(config.SEPARATOR_OUTPUT_DIR, exist_ok=True)
        _SEPARATOR_INSTANCE = Separator(
            log_level=30,
            model_file_dir=config.SEPARATOR_MODEL_DIR,
            output_dir=config.SEPARATOR_OUTPUT_DIR,
            output_format="WAV",
            normalization_threshold=0.9,
            output_single_stem="vocals",
            sample_rate=24000,
            mdx_params={
                "hop_length": 1024,
                "segment_size": 256,
                "overlap": 0.25,
                "batch_size": 1,
                "enable_denoise": False,
            },
        )
        # Load the dedicated vocal extraction model
        model = config.WHISPERX_SEPARATOR_MODEL
        print(f"  Loading separator model: {model}")
        _SEPARATOR_INSTANCE.load_model(model)
    return _SEPARATOR_INSTANCE


def get_vocals_path(audio_path: str) -> str:
    """Separate vocals from audio_path, return path to vocals WAV.

    Caches by filepath hash so the same song is only separated once.
    The output is a 24kHz WAV in SEPARATOR_OUTPUT_DIR.
    """
    fhash = _file_hash(audio_path)
    expected = os.path.join(config.SEPARATOR_OUTPUT_DIR, f"{fhash}_vocals.wav")
    if os.path.exists(expected):
        return expected

    sep = _separator()
    stem = sep.separate(audio_path)
    if stem:
        src = os.path.join(config.SEPARATOR_OUTPUT_DIR, stem[0])
        os.rename(src, expected)
        return expected

    raise RuntimeError(f"audio-separator returned no stems for {audio_path}")


def cleanup():
    """Remove separated vocal files."""
    if os.path.isdir(config.SEPARATOR_OUTPUT_DIR):
        shutil.rmtree(config.SEPARATOR_OUTPUT_DIR)
        os.makedirs(config.SEPARATOR_OUTPUT_DIR, exist_ok=True)
