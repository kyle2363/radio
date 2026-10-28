"""Shared EQ profile data for native and browser audio outputs."""


EQ_PRESETS = {
    "off": None,
    "bass_boost": [0.0, 0.0, 0.6, 0.8, 0.6, 0.4, 0.2, 0.0, 0.0, 0.0],
    "vocal_boost": [0.0, 0.0, 0.2, 0.4, 0.6, 0.8, 0.6, 0.4, 0.2, 0.0],
    "night_mode": [-0.2, -0.1, 0.0, 0.0, 0.0, -0.1, -0.2, -0.4, -0.6, -0.8],
    "flat": [0.0] * 10,
    "loudness": [0.4, 0.3, 0.2, 0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
    "clarity": [0.2, 0.3, 0.0, 0.2, 0.5, 0.7, 0.5, 0.3, 0.2, 0.1],
}


def allowed_presets():
    """Return supported preset names in stable display order."""
    return list(EQ_PRESETS)


def bands_for(preset):
    """Return a defensive 10-band profile, or None for disabled/unknown EQ."""
    bands = EQ_PRESETS.get(preset)
    return list(bands) if bands is not None else None
