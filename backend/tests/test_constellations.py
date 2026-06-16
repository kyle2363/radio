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
            tc._last_silhouette = 0.5
            tc._last_k = 3

        tc.invalidate()

        with tc._lock:
            assert tc._pockets == []
            assert tc._active_pocket_idx is None
            assert tc._last_built == 0
            assert tc._last_silhouette is None
            assert tc._last_k is None

    def test_get_diagnostics_empty(self):
        tc = TasteConstellations()
        diag = tc.get_diagnostics()
        assert diag["active"] is False
        assert diag["pockets"] == []
        assert "total_positive_signals" in diag

    def test_get_diagnostics_with_pockets(self):
        tc = TasteConstellations()
        p1 = TastePocket(0, [0.1, 0.2], None, 0.6, 10, "neutral", [1, 2], 0.4, 0.5)
        p2 = TastePocket(1, [0.8, 0.9], None, 0.4, 5, "energetic", [3, 4], 0.7, 0.6)
        with tc._lock:
            tc._pockets = [p1, p2]
            tc._total_positive_signals = 15
            tc._last_silhouette = 0.35
            tc._last_k = 2
            tc._n_pca_components = 10
            tc._global_energy_mean = 0.55
            tc._global_valence_mean = 0.55
            tc._compute_pocket_similarity()

        diag = tc.get_diagnostics()
        assert diag["active"] is True
        assert diag["pocket_count"] == 2
        assert diag["silhouette"] == 0.35
        assert diag["k"] == 2
        assert diag["pca_components"] == 10
        assert len(diag["pockets"]) == 2
        assert diag["pockets"][0]["completion_rate"] is not None
        assert diag["pockets"][0]["id"] == 0

    def test_reweight_energy_gate(self):
        """Candidates far from the active pocket's energy should not get boosted."""
        tc = TasteConstellations()
        # active pocket has energy_avg=0.5, so energy window is [0.35, 0.65]
        active_pocket = TastePocket(0, [0.5, 0.5], None, 1.0, 10, "neutral", [101], 0.5, 0.5)
        pocket2 = TastePocket(1, [0.6, 0.6], None, 0.5, 5, "energetic", [102], 0.8, 0.6)
        with tc._lock:
            tc._pockets = [active_pocket, pocket2]
            tc._compute_pocket_similarity()

        candidates = [
            {"song_id": 101, "score": 0.5, "energy": 0.5},   # in pocket 0 AND in energy window
            {"song_id": 103, "score": 0.5, "energy": 0.9},   # energy way too high
            {"song_id": 104, "score": 0.5, "energy": 0.1},   # energy way too low
        ]
        tc.reweight_candidates(candidates, active_pocket)

        assert candidates[0]["score"] > 0.5, "In-pocket candidate should be boosted"
        assert candidates[1]["score"] == 0.5, "High-energy candidate should NOT be boosted"
        assert candidates[2]["score"] == 0.5, "Low-energy candidate should NOT be boosted"
