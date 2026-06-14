"""Energy trajectory tracking.

Tracks the last 5 song energies and determines
whether energy is rising, falling, or stable.
Also computes target energy based on mood + time of day.
"""

import threading
from db import database


class EnergyTrajectory:
    """Tracks energy trends and computes targets."""

    def __init__(self):
        self._lock = threading.Lock()
        self._recent_energies = []
        self._max_history = 5
        self._hourly_profile = None  # lazy-loaded

    def record_energy(self, song_id, energy):
        """Record a song's energy level."""
        with self._lock:
            self._recent_energies.append({"song_id": song_id, "energy": energy})
            if len(self._recent_energies) > self._max_history:
                self._recent_energies.pop(0)

    def get_direction(self):
        """Get energy direction: 'rising', 'falling', or 'stable'."""
        with self._lock:
            if len(self._recent_energies) < 2:
                return "stable"
            energies = [e["energy"] for e in self._recent_energies]
        # Compare first half vs second half
        mid = len(energies) // 2
        first_half = sum(energies[:mid]) / max(1, mid)
        second_half = sum(energies[mid:]) / max(1, len(energies) - mid)

        delta = second_half - first_half
        if delta > 0.1:
            return "rising"
        elif delta < -0.1:
            return "falling"
        return "stable"

    def get_current_energy(self):
        """Get average energy of recent songs."""
        with self._lock:
            if not self._recent_energies:
                return 0.5
            return sum(e["energy"] for e in self._recent_energies) / len(self._recent_energies)

    def get_target_energy(self, mood_state=None, hour=None):
        """Compute target energy based on mood + time of day.

        Args:
            mood_state: current mood name or None
            hour: current hour (0-23) or None

        Returns target energy (0.0 to 1.0).
        """
        # Base energy from mood
        mood_base = {
            "chill": 0.25,
            "focus": 0.3,
            "melancholy": 0.2,
            "energetic": 0.75,
            "discovery": 0.5,
        }
        base = mood_base.get(mood_state, 0.5) if mood_state else 0.5

        # Personalized time of day bias from play history
        if hour is not None:
            with self._lock:
                if self._hourly_profile is None:
                    self._hourly_profile = database.get_hourly_completion_profile()
                completion = self._hourly_profile[hour] if 0 <= hour < 24 else 0.5
            # Map completion (0-1) to energy bias: high completion at this hour
            # means the user likes what they hear, so maintain energy direction.
            # Low completion means adjust.
            bias = (completion - 0.5) * 0.2  # -0.1 to +0.1 based on historical completion
            base += bias

        # Cap to valid range
        return max(0.0, min(1.0, base))

    def get_energy_delta(self):
        """Get the recommended energy delta for the next song."""
        direction = self.get_direction()
        if direction == "rising":
            return 0.15
        elif direction == "falling":
            return -0.15
        return 0.0


# Module-level singleton
_trajectory_lock = threading.Lock()
_trajectory = None


def get_trajectory():
    """Get or create the global EnergyTrajectory singleton."""
    global _trajectory
    if _trajectory is None:
        with _trajectory_lock:
            if _trajectory is None:
                _trajectory = EnergyTrajectory()
    return _trajectory
