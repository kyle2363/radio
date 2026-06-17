"""Librosa-based audio analysis — extracts raw features.

Two-pass ingest:
  Pass 1 (analyze_audio): extracts raw RMS, crest factor, spectral features, BPM,
                          onset density, spectral flux, spectral centroid,
                          LUFS, RMS envelope variance.
  Pass 2 (normalize_all): normalizes across the library for true 0-1 spread.
                         normalize_all_songs_v2 uses the improved formula.
"""

import json
import logging
import librosa
import numpy as np
import pyloudnorm as pyln
import config

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
    while len(envelope) < n_bins:
        envelope.append(0.5)
    return envelope[:n_bins]


def p_scale(arr):
    """Percentile-based scaling to [0, 1]."""
    lo, hi = np.percentile(arr, 5), np.percentile(arr, 95)
    if hi - lo < 1e-10:
        return np.full_like(arr, 0.5)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def analyze_audio(filepath: str) -> dict:
    """Extract raw audio features from an MP3.

    Returns dict with both v1 features (raw_rms, crest_factor, ...) and
    v2 features (onset_density, spectral_flux, spectral_centroid,
    rms_envelope_variance, lufs).  No normalization — that happens
    in the second pass after all songs are analyzed.
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
            "onset_density": None, "spectral_flux": None,
            "spectral_centroid": None, "rms_envelope_variance": None,
            "lufs": None,
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
        rolloff_mean = float(np.mean(rolloff)) / sr
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

    # --- v2 features: onset density and spectral flux ---
    try:
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
        onset_density = len(onset_frames) / 90.0
    except Exception as e:
        logger.debug("Onset detection failed: %s", e)
        onset_density = 0.0

    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        spectral_flux = float(np.mean(onset_env))
    except Exception as e:
        logger.debug("Spectral flux failed: %s", e)
        spectral_flux = 0.0

    # --- v2 features: spectral centroid ---
    try:
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        centroid_mean = float(np.mean(centroid)) / sr
    except Exception as e:
        logger.debug("Spectral centroid failed: %s", e)
        centroid_mean = 0.0

    # --- v2 features: RMS envelope variance (dynamic energy) ---
    rms_envelope_variance = float(np.var(rms))

    # --- v2 features: LUFS (loudness standard) ---
    try:
        meter = pyln.Meter(sr, block_size=0.400)
        loudness = meter.integrated_loudness(y)
        lufs = round(float(loudness), 2) if loudness is not None and loudness > -80 else -25.0
    except Exception as e:
        logger.debug("LUFS measurement failed: %s", e)
        lufs = -25.0

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
        "onset_density": round(onset_density, 6),
        "spectral_flux": round(spectral_flux, 6),
        "spectral_centroid": round(centroid_mean, 6),
        "rms_envelope_variance": round(rms_envelope_variance, 6),
        "lufs": lufs,
        "energy": 0.5,
        "valence": 0.5,
    }


def normalize_all_songs(songs: list) -> list:
    """Normalize raw features across the full library — v1 formula.

    Retained for backward compatibility.  v2 formula is in
    normalize_all_songs_v2.

    Returns:
        list of {id, energy, valence} dicts ready for DB update.
    """
    valid = [s for s in songs if s.get("raw_rms") is not None]
    if not valid:
        return []

    rms_vals = np.array([s["raw_rms"] for s in valid])
    crest_vals = np.array([s["crest_factor"] for s in valid])
    contrast_vals = np.array([s["spectral_contrast"] for s in valid])
    rolloff_vals = np.array([s["spectral_rolloff"] for s in valid])
    flatness_vals = np.array([s["spectral_flatness"] for s in valid])

    rms_norm = p_scale(rms_vals)
    crest_norm = p_scale(crest_vals)

    energy = 0.65 * rms_norm + 0.35 * crest_norm

    contrast_norm = p_scale(contrast_vals)
    rolloff_norm = p_scale(rolloff_vals)
    flatness_inv = 1.0 - p_scale(flatness_vals)

    valence = 0.4 * contrast_norm + 0.3 * rolloff_norm + 0.3 * flatness_inv

    results = []
    for i, s in enumerate(valid):
        results.append({
            "id": s["id"],
            "energy": round(float(energy[i]), 4),
            "valence": round(float(valence[i]), 4),
        })
    return results


def normalize_all_songs_v2(songs: list) -> list:
    """Normalize raw features — v2 energy formula.

    Args:
        songs: list of dicts, each with:
               onset_density, spectral_flux, spectral_centroid,
               lufs, rms_envelope_variance, bpm, id

    Returns:
        list of {id, energy} dicts ready for DB update (v2 energy only).
        Valence is unchanged from v1.
    """
    valid = [s for s in songs if s.get("onset_density") is not None]
    if not valid:
        return []

    onset_density = np.array([s["onset_density"] for s in valid])
    centroid_vals = np.array([s["spectral_centroid"] for s in valid])
    lufs_vals = np.array([s["lufs"] for s in valid], dtype=float)
    rms_var_vals = np.array([s["rms_envelope_variance"] for s in valid])

    onset_norm = p_scale(onset_density)
    centroid_norm = p_scale(centroid_vals)
    lufs_norm = p_scale(lufs_vals)
    rms_var_norm = p_scale(rms_var_vals)

    energy = 0.40 * centroid_norm + 0.30 * lufs_norm + 0.15 * rms_var_norm + 0.15 * onset_norm

    results = []
    for i, s in enumerate(valid):
        results.append({
            "id": s["id"],
            "energy": round(float(energy[i]), 4),
        })
    return results


def compute_v2_energy(song: dict, library_range=None) -> float:
    """Compute v2 energy for a single song given library-wide percentile bounds.

    Formula: 0.40 * centroid + 0.30 * LUFS + 0.15 * rms_envelope_variance + 0.15 * onset_density

    Args:
        song: dict with onset_density, spectral_centroid, lufs, rms_envelope_variance
        library_range: optional pre-computed (lo, hi) tuple for each feature.
                       If None, uses default bounds.

    Returns:
        float 0-1 representing energy.
    """
    needed = ["onset_density", "spectral_centroid", "lufs", "rms_envelope_variance"]
    if any(song.get(k) is None for k in needed):
        return song.get("energy", 0.5)

    def _bounds(key, default):
        if library_range is not None:
            return library_range.get(key, default)
        return default

    lo_onset, hi_onset = _bounds("onset_density", (0, 1))
    lo_centroid, hi_centroid = _bounds("spectral_centroid", (0, 1))
    lo_lufs, hi_lufs = _bounds("lufs", (-25, 0))
    lo_rms_var, hi_rms_var = _bounds("rms_envelope_variance", (0, 1))

    def _clip(v, lo, hi):
        return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.5

    onset_norm = _clip(song["onset_density"], lo_onset, hi_onset)
    centroid_norm = _clip(song["spectral_centroid"], lo_centroid, hi_centroid)
    lufs_norm = _clip(song["lufs"], lo_lufs, hi_lufs)
    rms_var_norm = _clip(song["rms_envelope_variance"], lo_rms_var, hi_rms_var)

    energy = 0.40 * centroid_norm + 0.30 * lufs_norm + 0.15 * rms_var_norm + 0.15 * onset_norm
    return round(float(energy), 4)


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
            data = data.mean(axis=1)

        meter = pyln.Meter(rate, block_size=0.400)
        loudness = meter.integrated_loudness(data)

        if loudness is None or loudness <= -80:
            return 0.0

        gain_db = target_loudness - loudness
        return round(gain_db, 2)
    except Exception as e:
        logger.debug("measure_loudness failed for %s: %s", filepath, e)
        return 0.0
