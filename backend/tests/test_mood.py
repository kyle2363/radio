"""Tests for the mood state machine."""

import pytest
from engine.mood import MoodStateMachine


class TestMoodStateMachine:
    def test_default_state(self):
        m = MoodStateMachine()
        assert m.get_state() == "neutral"

    def test_set_valid_state(self):
        m = MoodStateMachine()
        assert m.set_state("chill") is True
        assert m.get_state() == "chill"

    def test_set_valid_state_energetic(self):
        m = MoodStateMachine()
        assert m.set_state("energetic") is True
        assert m.get_state() == "energetic"

    def test_set_invalid_state(self):
        m = MoodStateMachine()
        assert m.set_state("invalid_state") is False
        assert m.get_state() == "neutral"

    def test_vibe_lock_starts_inactive(self):
        m = MoodStateMachine()
        assert m.is_vibe_lock_active() is False

    def test_activate_deactivate_vibe_lock(self):
        m = MoodStateMachine()
        m.activate_vibe_lock(song_id=1, embedding=[0.1, 0.2, 0.3], audio_embedding=[0.4, 0.5, 0.6])
        assert m.is_vibe_lock_active() is True
        info = m.get_vibe_lock_info()
        assert info["song_id"] == 1
        assert info["active"] is True

        m.deactivate_vibe_lock()
        assert m.is_vibe_lock_active() is False

    def test_get_vibe_lock_info_copy(self):
        m = MoodStateMachine()
        m.activate_vibe_lock(song_id=5, embedding=[0.1, 0.2, 0.3])
        info = m.get_vibe_lock_info()
        assert info["song_id"] == 5
        info["song_id"] = 999  # Should not modify internal state
        assert m.get_vibe_lock_info()["song_id"] == 5

    def test_get_energy_range(self):
        m = MoodStateMachine()
        low, high = m.get_energy_range("chill")
        assert low <= high
        assert 0.0 <= low <= 1.0
        assert 0.0 <= high <= 1.0

    def test_get_valence_range(self):
        m = MoodStateMachine()
        low, high = m.get_valence_range("chill")
        assert low <= high
        assert 0.0 <= low <= 1.0
        assert 0.0 <= high <= 1.0

    def test_check_auto_mood_shift_during_vibe_lock(self):
        m = MoodStateMachine()
        m.activate_vibe_lock(song_id=1, embedding=[0.1, 0.2, 0.3])
        result = m.check_auto_mood_shift(recent_signals=[], skip_streak=5)
        assert result is None  # Vibe lock prevents shift

    def test_set_state_clears_vibe_lock_state(self):
        m = MoodStateMachine()
        m.set_state("energetic")
        m.activate_vibe_lock(song_id=2, embedding=[0.1, 0.2, 0.3])
        assert m.is_vibe_lock_active() is True
        m.deactivate_vibe_lock()
        assert m.is_vibe_lock_active() is False
