"""Test the energy formula against the hand-rated fixture.

Requires data/test_fixtures/energy_ratings.json (populated by the rating page).
Skipped automatically if the fixture has fewer than 10 ratings.

Test v2 formula only runs once compute_v2_energy() exists AND the
rated songs have their v2 feature columns populated (onset_density, etc.
— added by PR 1, populated by PR 2's reprocess script).
"""

import json
import os

import numpy as np
import pytest

import config
from db import database


FIXTURE_PATH = os.path.join(config.DATA_DIR, "test_fixtures", "energy_ratings.json")


def _load_fixture():
    try:
        with open(FIXTURE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


FIXTURE = _load_fixture()


@pytest.mark.skipif(FIXTURE is None, reason="Fixture not created yet — run /api/admin/rate-songs and save ratings first")
def test_fixture_exists():
    assert FIXTURE is not None


@pytest.mark.skipif(FIXTURE is None, reason="Fixture not created yet")
def test_fixture_has_enough_ratings():
    assert len(FIXTURE.get("ratings", {})) >= 10, (
        f"Need at least 10 rated songs. Got {len(FIXTURE.get('ratings', {}))}."
    )


def _compute_v1_energy(song, bounds):
    if song.get("raw_rms") is None:
        return 0.5
    lo_rms, hi_rms = bounds["raw_rms"]
    lo_crest, hi_crest = bounds["crest_factor"]
    _clip = lambda v, lo, hi: max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.5
    rms_norm = _clip(song["raw_rms"], lo_rms, hi_rms)
    crest_norm = _clip(song["crest_factor"], lo_crest, hi_crest)
    return round(float(0.65 * rms_norm + 0.35 * crest_norm), 4)


def _get_library_v1_bounds():
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT raw_rms, crest_factor FROM songs WHERE raw_rms IS NOT NULL"
        ).fetchall()
        if len(rows) < 10:
            return None
        rms_vals = np.array([r["raw_rms"] for r in rows])
        crest_vals = np.array([r["crest_factor"] for r in rows])
        lo_rms, hi_rms = np.percentile(rms_vals, 5), np.percentile(rms_vals, 95)
        lo_crest, hi_crest = np.percentile(crest_vals, 5), np.percentile(crest_vals, 95)
        return {"raw_rms": (float(lo_rms), float(hi_rms)), "crest_factor": (float(lo_crest), float(hi_crest))}
    except Exception:
        return None
    finally:
        conn.close()


def _has_v2():
    try:
        from ingest.audio import compute_v2_energy
        return True
    except (ImportError, AttributeError):
        return False


def _fixture_songs_have_v2_features():
    if FIXTURE is None:
        return False
    for sid_str in list(FIXTURE["ratings"].keys())[:3]:
        song = database.get_song(int(sid_str))
        if not song or song.get("onset_density") is None:
            return False
    return True


def _get_library_v2_bounds():
    """Compute 5th/95th percentile bounds for v2 features across the library."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT onset_density, spectral_flux, spectral_centroid,
                      lufs, rms_envelope_variance, bpm
               FROM songs WHERE onset_density IS NOT NULL"""
        ).fetchall()
        if len(rows) < 10:
            return None
        arrs = {k: [] for k in ["onset_density", "spectral_flux", "spectral_centroid",
                                 "lufs", "rms_envelope_variance"]}
        bpm_vals = []
        for r in rows:
            for k in arrs:
                v = r[k]
                if v is not None:
                    arrs[k].append(v)
            bpm_vals.append(min(r["bpm"] or 120, 200) / 200.0)

        bounds = {}
        for k, vals in arrs.items():
            a = np.array(vals)
            lo, hi = np.percentile(a, 5), np.percentile(a, 95)
            bounds[k] = (float(lo), float(hi))
        bpm_arr = np.array(bpm_vals)
        lo_bpm, hi_bpm = np.percentile(bpm_arr, 5), np.percentile(bpm_arr, 95)
        bounds["bpm_norm"] = (float(lo_bpm), float(hi_bpm))
        return bounds
    except Exception:
        return None
    finally:
        conn.close()


@pytest.mark.skipif(
    not _has_v2() or not _fixture_songs_have_v2_features(),
    reason="compute_v2_energy not ready or fixture songs lack v2 feature columns — run reprocess first",
)
def test_v2_formula_improves_correlation():
    """v2 must correlate better with human ratings than v1 does."""
    from ingest.audio import compute_v2_energy
    from scipy.stats import pearsonr

    assert FIXTURE is not None
    ratings = FIXTURE["ratings"]

    v2_bounds = _get_library_v2_bounds()
    assert v2_bounds is not None, "Less than 10 songs have v2 features populated"

    v1_bounds = _get_library_v1_bounds()
    assert v1_bounds is not None, "Less than 10 songs have v1 features populated"

    old_scores, new_scores, human_scores = [], [], []
    for sid_str, human in ratings.items():
        song = database.get_song(int(sid_str))
        if not song:
            continue
        old_scores.append(_compute_v1_energy(song, v1_bounds))
        new_scores.append(compute_v2_energy(song, library_range=v2_bounds))
        human_scores.append(float(human))

    assert len(old_scores) >= 10, f"Only {len(old_scores)} songs resolved from fixture"

    old_corr, _ = pearsonr(old_scores, human_scores)
    new_corr, _ = pearsonr(new_scores, human_scores)

    print(f"\nv1 Pearson r: {old_corr:.4f}")
    print(f"v2 Pearson r: {new_corr:.4f}")
    print(f"improvement:  {new_corr - old_corr:+.4f}")

    assert old_corr > 0.5, (
        f"v1 baseline correlation is unexpectedly low: {old_corr:.3f}. "
        "Either the fixture has issues or the current formula is broken."
    )
    assert new_corr > 0.7, (
        f"v2 only reached r={new_corr:.3f}. "
        "Need at least 0.7 to ship. Iterate on the formula."
    )
    assert new_corr > old_corr + 0.1, (
        f"v2 improvement over v1 is only {new_corr - old_corr:.3f}. "
        "Need at least 0.1 gain to justify the change."
    )
