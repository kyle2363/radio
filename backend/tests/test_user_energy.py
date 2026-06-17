"""Tests for the user-correction energy layer (PR 3)."""

import pytest
from db import database


def test_set_user_energy_no_base_change():
    """set_user_energy should not touch the energy column."""
    song = database.get_song(1)
    if not song:
        pytest.skip("No song 1 in DB")
    original = song["energy"]
    database.set_user_energy(1, 0.875)
    after = database.get_song(1)
    assert after["energy"] == original, "energy column was modified"
    assert after["user_energy"] is not None
    assert (after["user_energy_count"] or 0) > 0
    database.clear_user_energy(1)


def test_effective_energy_returns_base_for_unrated():
    """With no user rating, effective_energy should equal base energy."""
    song = database.get_song(1)
    if not song:
        pytest.skip("No song 1 in DB")
    database.clear_user_energy(1)
    eff = database.get_effective_energy(1)
    assert eff == (song["energy"] or 0.5)


def test_effective_energy_blend():
    """With 1 user rating, effective = 0.7 * base + 0.3 * user."""
    song = database.get_song(1)
    if not song:
        pytest.skip("No song 1 in DB")
    base = song["energy"] or 0.5
    user_val = 0.875
    expected = round(0.7 * base + 0.3 * user_val, 4)
    database.set_user_energy(1, user_val)
    eff = database.get_effective_energy(1)
    database.clear_user_energy(1)
    assert eff == expected, f"Expected {expected}, got {eff}"


def test_clear_user_energy_resets():
    """After clear_user_energy, effective should return to base."""
    song = database.get_song(1)
    if not song:
        pytest.skip("No song 1 in DB")
    base = song["energy"] or 0.5
    database.set_user_energy(1, 0.5)
    database.clear_user_energy(1)
    song_after = database.get_song(1)
    assert song_after["user_energy"] is None
    assert (song_after["user_energy_count"] or 0) == 0
    assert database.get_effective_energy(1) == base


def test_get_effective_energy_batch():
    """Batch lookup returns same results as individual calls."""
    song = database.get_song(1)
    if not song:
        pytest.skip("No song 1 in DB")
    database.set_user_energy(1, 0.75)
    batch = database.get_effective_energy_batch([1, 2])
    assert 1 in batch
    assert batch[1] == database.get_effective_energy(1)
    assert 2 in batch
    database.clear_user_energy(1)


def test_get_calibration_sample_size():
    """get_calibration_sample should return the requested number."""
    sample = database.get_calibration_sample(20)
    assert len(sample) == 20, f"Expected 20, got {len(sample)}"
    ids = [s["id"] for s in sample]
    assert len(set(ids)) == 20, "Duplicate songs in sample"


def test_get_calibration_sample_clusters_diverse():
    """get_calibration_sample should pick songs from different clusters."""
    sample = database.get_calibration_sample(20)
    clusters = [s["cluster_id"] for s in sample]
    assert len(set(clusters)) == 20, "Expected 20 unique clusters"


def test_get_top_anchors_falls_back_to_library():
    """When no user ratings exist, anchors use library songs by energy."""
    anchors = database.get_top_anchors_for_energy(0.75, 1.0, 3)
    assert len(anchors) == 3
    for a in anchors:
        assert "id" in a
        assert "artist" in a
        assert "title" in a
