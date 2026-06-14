"""Tests for the energy trajectory module."""

from engine.energy import EnergyTrajectory, get_trajectory


class TestEnergyTrajectory:
    def test_empty_returns_default(self):
        e = EnergyTrajectory()
        assert e.get_current_energy() == 0.5
        assert e.get_direction() == "stable"
        assert e.get_energy_delta() == 0.0

    def test_single_song_stable(self):
        e = EnergyTrajectory()
        e.record_energy(1, 0.5)
        assert e.get_direction() == "stable"

    def test_rising_detected(self):
        e = EnergyTrajectory()
        e.record_energy(1, 0.2)
        e.record_energy(2, 0.4)
        e.record_energy(3, 0.6)
        e.record_energy(4, 0.8)
        e.record_energy(5, 1.0)
        assert e.get_direction() == "rising"
        assert e.get_energy_delta() == 0.15

    def test_falling_detected(self):
        e = EnergyTrajectory()
        e.record_energy(1, 0.9)
        e.record_energy(2, 0.7)
        e.record_energy(3, 0.5)
        e.record_energy(4, 0.3)
        e.record_energy(5, 0.1)
        assert e.get_direction() == "falling"
        assert e.get_energy_delta() == -0.15

    def test_stable_mid_range(self):
        e = EnergyTrajectory()
        e.record_energy(1, 0.5)
        e.record_energy(2, 0.52)
        e.record_energy(3, 0.48)
        e.record_energy(4, 0.51)
        e.record_energy(5, 0.5)
        assert e.get_direction() == "stable"

    def test_max_history_enforced(self):
        e = EnergyTrajectory()
        for i in range(10):
            e.record_energy(i, float(i) / 10.0)
        assert len(e._recent_energies) == 5

    def test_get_target_energy_mood_default(self):
        e = EnergyTrajectory()
        energy = e.get_target_energy()
        assert 0.0 <= energy <= 1.0

    def test_get_target_energy_mood_mapping(self):
        e = EnergyTrajectory()
        assert e.get_target_energy("chill") < e.get_target_energy("energetic")
        assert e.get_target_energy("energetic") >= 0.5

    def test_get_current_energy_average(self):
        e = EnergyTrajectory()
        e.record_energy(1, 0.2)
        e.record_energy(2, 0.8)
        assert e.get_current_energy() == 0.5  # (0.2 + 0.8) / 2

    def test_singleton(self):
        t1 = get_trajectory()
        t2 = get_trajectory()
        assert t1 is t2
