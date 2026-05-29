"""Mood state machine + vibe lock.

States: chill, neutral, energetic, focus, melancholy, discovery
Each state has energy/valence ranges, emotional tags, and a centroid for blending.
"""

import logging
import time
from db import database

logger = logging.getLogger(__name__)


def _get_song_energy(song_id):
    """Look up a song's energy from the database."""
    try:
        song = database.get_song(song_id)
        if song:
            return song.get("energy", 0.5)
    except Exception as e:
        logger.debug("Could not get energy for song %s: %s", song_id, e)
    return 0.5

MOOD_STATES = {
    "chill": {
        "energy_range": (0.0, 0.5),
        "valence_range": (0.3, 0.8),
        "emotional_tags": ["calm", "relaxed", "mellow", "gentle"],
        "tts_line": "Let's slow things down a bit.",
    },
    "neutral": {
        "energy_range": (0.3, 0.75),
        "valence_range": (0.4, 0.8),
        "emotional_tags": ["balanced", "steady", "comfortable"],
        "tts_line": None,
    },
    "energetic": {
        "energy_range": (0.55, 1.0),
        "valence_range": (0.5, 1.0),
        "emotional_tags": ["upbeat", "driving", "punchy", "loud"],
        "tts_line": "Let's turn it up.",
    },
    "focus": {
        "energy_range": (0.2, 0.6),
        "valence_range": (0.3, 0.7),
        "emotional_tags": ["minimal", "repetitive", "atmospheric"],
        "tts_line": "Time to focus.",
    },
    "melancholy": {
        "energy_range": (0.0, 0.5),
        "valence_range": (0.1, 0.5),
        "emotional_tags": ["sad", "wistful", "haunting", "emotional"],
        "tts_line": None,
    },
    "discovery": {
        "energy_range": (0.3, 0.85),
        "valence_range": (0.3, 0.85),
        "emotional_tags": [],
        "tts_line": "Something new.",
    },
}


class MoodStateMachine:
    """Manages mood state and vibe lock."""

    def __init__(self):
        self._current_state = None
        self._vibe_lock = {
            "active": False,
            "song_id": None,
            "embedding": None,
            "expires_at": None,
            "songs_remaining": 0,
        }
        self._load_state()

    def _load_state(self):
        """Load mood state from settings on startup."""
        active = database.get_setting("vibe_lock_active")
        if active and active.lower() == "true":
            self._vibe_lock["active"] = True
            self._vibe_lock["song_id"] = int(database.get_setting("vibe_lock_song_id") or 0)
            expires = database.get_setting("vibe_lock_expires")
            self._vibe_lock["expires_at"] = float(expires) if expires else None

    def _save_state(self):
        """Persist mood state to settings."""
        database.set_setting("vibe_lock_active", str(self._vibe_lock["active"]).lower())
        if self._vibe_lock["song_id"]:
            database.set_setting("vibe_lock_song_id", str(self._vibe_lock["song_id"]))
        if self._vibe_lock["expires_at"]:
            database.set_setting("vibe_lock_expires", str(self._vibe_lock["expires_at"]))

    def get_state(self):
        """Get current mood state dict or None."""
        if self._current_state and self._current_state in MOOD_STATES:
            return self._current_state
        return "neutral"

    def set_state(self, state_name):
        """Set mood state explicitly (from mood button)."""
        if state_name in MOOD_STATES:
            self._current_state = state_name
            return True
        return False

    def check_auto_mood_shift(self, recent_signals, skip_streak=0):
        """Auto-detect mood shift based on recent behavior.

        Returns new state name if shift detected, else None.
        """
        # Vibe lock prevents auto-shift
        if self._vibe_lock["active"]:
            return None

        # 3+ consecutive skips -> suggest energy shift
        if skip_streak >= 3:
            # Check if recent skips are from high-energy songs
            high_energy_skips = 0
            for s in recent_signals[-5:]:
                if s.get("signal") in ("skip_early", "skip_late"):
                    song_energy = _get_song_energy(s.get("song_id"))
                    if song_energy > 0.6:
                        high_energy_skips += 1
            if high_energy_skips >= 2:
                return "chill"

        # 3+ consecutive completions of energetic songs -> energetic mood
        if len(recent_signals) >= 3:
            recent_energy = []
            for s in recent_signals[-3:]:
                song_energy = _get_song_energy(s.get("song_id"))
                recent_energy.append(song_energy)
            if all(e > 0.7 for e in recent_energy):
                return "energetic"

        return None

    def activate_vibe_lock(self, song_id, embedding):
        """Activate vibe lock for a specific song."""
        self._vibe_lock["active"] = True
        self._vibe_lock["song_id"] = song_id
        self._vibe_lock["embedding"] = embedding
        self._vibe_lock["expires_at"] = time.time() + (30 * 60)  # 30 minutes
        self._vibe_lock["songs_remaining"] = 10
        self._current_state = None  # Clear auto mood during vibe lock
        self._save_state()

    def deactivate_vibe_lock(self):
        """Deactivate vibe lock."""
        self._vibe_lock["active"] = False
        self._vibe_lock["song_id"] = None
        self._vibe_lock["embedding"] = None
        self._vibe_lock["expires_at"] = None
        self._vibe_lock["songs_remaining"] = 0
        self._save_state()

    def tick_vibe_lock(self):
        """Call on each song boundary to decrement vibe lock counter."""
        if not self._vibe_lock["active"]:
            return

        # Check expiry
        if self._vibe_lock["expires_at"] and time.time() > self._vibe_lock["expires_at"]:
            self.deactivate_vibe_lock()
            return

        # Decrement remaining songs
        self._vibe_lock["songs_remaining"] -= 1
        if self._vibe_lock["songs_remaining"] <= 0:
            self.deactivate_vibe_lock()

    def is_vibe_lock_active(self):
        return self._vibe_lock["active"]

    def get_vibe_lock_info(self):
        return dict(self._vibe_lock)

    def get_mood_config(self, state_name):
        """Get config for a mood state."""
        return MOOD_STATES.get(state_name, MOOD_STATES["neutral"])

    def get_energy_range(self, state_name=None):
        """Get energy range for a mood state."""
        state = state_name or self.get_state()
        config = self.get_mood_config(state)
        return config["energy_range"]

    def get_valence_range(self, state_name=None):
        """Get valence range for a mood state."""
        state = state_name or self.get_state()
        config = self.get_mood_config(state)
        return config["valence_range"]
