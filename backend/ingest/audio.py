"""Librosa-based audio analysis — extracts raw features.

Two-pass ingest:
  Pass 1 (analyze_audio): extracts raw RMS, crest factor, spectral features, BPM.
  Pass 2 (normalize_all): normalizes across the library for true 0-1 spread.
"""

import json
import logging
import librosa
import numpy as np
import pyloudnorm as pyln

logger = logging.getLogger(__name__)


def compute_rms_envelope(y, n_bins=100):
    """Compute RMS energy envelope across the audio signal.

    Returns list of n_bins floats (0-1 range).
    """
    frame_length = max(1, len(y) // n_bins)
    envelope = []
    for i in range(0, len(y), frame_length):
        frame = y[i:i + frame_length]
        if len(frame) == 0:
            break
        rms_val = float(np.sqrt(np.mean(frame ** 2)))
        envelope.append(rms_val)
        if len(envelope) >= n_bins:
            break
    if not envelope:
        return [0.5] * n_bins
    max_val = max(envelope) or 1.0
    envelope = [min(1.0, v / max_val) for v in envelope]
    # Pad or trim to exactly n_bins
    while len(envelope) < n_bins:
        envelope.append(0.5)
    return envelope[:n_bins]


def analyze_audio(filepath: str) -> dict:
    """Extract raw audio features from an MP3.

    Returns dict with raw_rms, crest_factor, spectral_contrast,
    spectral_rolloff, spectral_flatness, bpm.  No normalization —
    that happens in the second pass after all songs are analyzed.
    """
    try:
        y, sr = librosa.load(filepath, duration=90, sr=22050)
    except Exception as e:
        logger.warning("Could not load audio %s: %s", filepath, e)
        return {
            "energy": 0.5, "valence": 0.5, "bpm": None,
            "raw_rms": None, "crest_factor": None,
            "spectral_contrast": None, "spectral_rolloff": None,
            "spectral_flatness": None,
        }

    # --- RMS (loudness) ---
    rms = librosa.feature.rms(y=y)[0]
    raw_rms = float(np.mean(rms))
    peak = float(np.max(np.abs(y)))
    crest_factor = peak / max(raw_rms, 1e-10)

    # --- Spectral features (valence ingredients) ---
    try:
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
        contrast_mean = float(np.mean(contrast))
    except Exception as e:
        logger.debug("Spectral contrast failed: %s", e)
        contrast_mean = 0.0

    try:
        rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
        rolloff_mean = float(np.mean(rolloff)) / sr  # normalized 0-1
    except Exception as e:
        logger.debug("Spectral rolloff failed: %s", e)
        rolloff_mean = 0.0

    try:
        flatness = librosa.feature.spectral_flatness(y=y)[0]
        flatness_mean = float(np.mean(flatness))
    except Exception as e:
        logger.debug("Spectral flatness failed: %s", e)
        flatness_mean = 0.0

    # --- BPM ---
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, '__len__'):
        tempo_val = float(tempo[0]) if len(tempo) > 0 else 120.0
    else:
        tempo_val = float(tempo) if tempo else 120.0

    # RMS envelope (100 bins) for audio-reactive waveform
    rms_envelope = compute_rms_envelope(y, n_bins=100)

    return {
        "raw_rms": round(raw_rms, 6),
        "crest_factor": round(crest_factor, 4),
        "spectral_contrast": round(contrast_mean, 4),
        "spectral_rolloff": round(rolloff_mean, 6),
        "spectral_flatness": round(flatness_mean, 6),
        "bpm": int(round(tempo_val)) if tempo_val else None,
        "rms_envelope": json.dumps(rms_envelope),
        # Placeholder — overwritten in normalization pass
        "energy": 0.5,
        "valence": 0.5,
    }


def normalize_all_songs(songs: list) -> list:
    """Normalize raw features across the full library using percentile scaling.

    Args:
        songs: list of dicts, each with at least:
               raw_rms, crest_factor, spectral_contrast,
               spectral_rolloff, spectral_flatness, id

    Returns:
        list of {id, energy, valence} dicts ready for DB update.
    """
    # Filter songs with valid raw features
    valid = [s for s in songs if s.get("raw_rms") is not None]
    if not valid:
        return []

    # Extract raw arrays
    rms_vals = np.array([s["raw_rms"] for s in valid])
    crest_vals = np.array([s["crest_factor"] for s in valid])
    contrast_vals = np.array([s["spectral_contrast"] for s in valid])
    rolloff_vals = np.array([s["spectral_rolloff"] for s in valid])
    flatness_vals = np.array([s["spectral_flatness"] for s in valid])

    # --- Energy: loudness + dynamics ---
    # raw_rms measures average loudness; crest_factor measures dynamics
    # (high crest = lots of quiet/loud variation = more dynamic energy)
    # Percentile-scale each, then combine.
    def p_scale(arr):
        """Percentile-based scaling to [0, 1]."""
        lo, hi = np.percentile(arr, 5), np.percentile(arr, 95)
        if hi - lo < 1e-10:
            return np.full_like(arr, 0.5)
        return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)

    rms_norm = p_scale(rms_vals)
    crest_norm = p_scale(crest_vals)

    # Energy = 0.65 * loudness + 0.35 * dynamics
    energy = 0.65 * rms_norm + 0.35 * crest_norm

    # --- Valence: brightness + tonality ---
    # High spectral contrast + high rolloff + low flatness = "bright/happy"
    contrast_norm = p_scale(contrast_vals)
    rolloff_norm = p_scale(rolloff_vals)
    flatness_inv = 1.0 - p_scale(flatness_vals)  # invert: low flatness = high valence

    valence = 0.4 * contrast_norm + 0.3 * rolloff_norm + 0.3 * flatness_inv

    # Build update list
    results = []
    for i, s in enumerate(valid):
        results.append({
            "id": s["id"],
            "energy": round(float(energy[i]), 4),
            "valence": round(float(valence[i]), 4),
        })

    return results


def measure_loudness(filepath: str, target_loudness: float = -14.0) -> float:
    """Measure integrated loudness (LUFS) and compute gain to reach target.

    Uses EBU R128 via pyloudnorm.  Target default -14 LUFS (Spotify level).

    Returns gain_db (float): how much to boost/cut to reach target.
    Positive = boost needed, Negative = cut needed.
    Returns 0.0 if measurement fails.
    """
    try:
        import soundfile as sf
        data, rate = sf.read(filepath)
        if data.ndim > 1:
            data = data.mean(axis=1)  # mix to mono

        meter = pyln.Meter(rate, block_size=0.400)
        loudness = meter.integrated_loudness(data)

        if loudness is None or loudness <= -80:
            return 0.0

        gain_db = target_loudness - loudness
        return round(gain_db, 2)
    except Exception as e:
        logger.debug("measure_loudness failed for %s: %s", filepath, e)
        return 0.0
