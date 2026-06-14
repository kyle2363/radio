"""Tests for the taste constellations module."""

from engine.constellations import TasteConstellations, TastePocket


class TestTasteConstellations:
    def test_initial_state(self):
        tc = TasteConstellations()
        assert tc.pockets == []
        assert tc.is_active is False
        assert tc.active_pocket is None

    def test_is_active_requires_2_pockets(self):
        tc = TasteConstellations()
        assert tc.is_active is False

        # One pocket should still be inactive
        with tc._lock:
            tc._pockets = [TastePocket(
                constellation_id=0, centroid_text=[0.1, 0.2], centroid_audio=None,
                weight=1.0, signal_count=5, primary_mood="neutral",
                song_ids=[1, 2, 3], energy_avg=0.5, valence_avg=0.5,
            )]
        assert tc.is_active is False

    def test_active_pocket_property(self):
        tc = TasteConstellations()
        pockets = [
            TastePocket(0, [0.1, 0.2], None, 0.5, 5, "chill", [1, 2], 0.3, 0.7),
            TastePocket(1, [0.8, 0.9], None, 0.5, 5, "energetic", [3, 4], 0.8, 0.6),
        ]
        with tc._lock:
            tc._pockets = pockets
            tc._active_pocket_idx = 1
        assert tc.active_pocket is pockets[1]
        assert tc.active_pocket.primary_mood == "energetic"

    def test_invalidate_clears_state(self):
        tc = TasteConstellations()
        with tc._lock:
            tc._pockets = [TastePocket(0, [0.1], None, 1.0, 1, "neutral", [1], 0.5, 0.5)]
            tc._active_pocket_idx = 0
            tc._last_built = 1000.0

        tc.invalidate()

        with tc._lock:
            assert tc._pockets == []
            assert tc._active_pocket_idx is None
            assert tc._last_built == 0
