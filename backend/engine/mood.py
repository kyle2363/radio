"""Mood state machine + vibe lock.

States: chill, neutral, energetic, focus, melancholy, discovery
Each state has energy/valence ranges, emotional tags, and a centroid for blending.
"""

import logging
import time
import config
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
            "audio_embedding": None,
            "expires_at": None,
            "songs_remaining": 0,
        }
        self._load_state()

    def _get_vibe_lock_duration_seconds(self):
        """Get vibe lock duration in seconds from DB settings."""
        try:
            val = database.get_setting("vibe_lock_duration_minutes")
            minutes = int(val) if val is not None else 30
            return max(5, min(120, minutes)) * 60  # clamp 5-120 min
        except (ValueError, TypeError):
            return 30 * 60

    def _get_vibe_lock_max_songs(self):
        """Get vibe lock max songs from DB settings."""
        try:
            val = database.get_setting("vibe_lock_max_songs")
            songs = int(val) if val is not None else 10
            return max(1, min(50, songs))  # clamp 1-50
        except (ValueError, TypeError):
            return 10

    def _load_state(self):
        """Load mood state from settings on startup."""
        active = database.get_setting("vibe_lock_active")
        if active and active.lower() == "true":
            song_id = int(database.get_setting("vibe_lock_song_id") or 0)
            expires = database.get_setting("vibe_lock_expires")
            expires_at = float(expires) if expires else None

            # Vibe lock requires an embedding to function.  Embeddings are
            # in-memory only and never persisted to the settings table, so
            # after a restart they are always None.  We must deactivate.
            import time
            if expires_at and time.time() > expires_at:
                logger.info("Vibe lock expired during downtime, deactivating")
                self._save_state()
                return

            if not song_id:
                logger.info("Vibe lock has no song_id, deactivating")
                self._save_state()
                return

            # Try to re-fetch the embedding from ChromaDB
            embedding = None
            audio_embedding = None
            try:
                from engine import retriever
                collection = retriever.get_chroma_collection("songs")
                result = collection.get(ids=[str(song_id)], include=["embeddings"])
                if result and result.get("embeddings") is not None and len(result["embeddings"]) > 0:
                    emb = result["embeddings"][0]
                    if emb is not None:
                        embedding = emb
            except Exception as e:
                logger.debug("Could not re-fetch vibe lock embedding for song %s: %s", song_id, e)

            if config.MUQ_ENABLED:
                try:
                    audio_collection = retriever.get_chroma_collection(config.MUQ_AUDIO_COLLECTION)
                    result = audio_collection.get(ids=[str(song_id)], include=["embeddings"])
                    if result and result.get("embeddings") is not None and len(result["embeddings"]) > 0:
                        emb = result["embeddings"][0]
                        if emb is not None:
                            audio_embedding = emb
                except Exception:
                    pass

            if embedding is None:
                logger.info("Vibe lock embedding unavailable for song %s, deactivating", song_id)
                self._save_state()
                return

            self._vibe_lock["active"] = True
            self._vibe_lock["song_id"] = song_id
            self._vibe_lock["embedding"] = embedding
            self._vibe_lock["audio_embedding"] = audio_embedding
            self._vibe_lock["expires_at"] = expires_at
            self._vibe_lock["songs_remaining"] = self._get_vibe_lock_max_songs()
            logger.info("Vibe lock restored for song %s (expires_at=%s)", song_id, expires_at)

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

    def activate_vibe_lock(self, song_id, embedding, audio_embedding=None):
        """Activate vibe lock for a specific song.

        Args:
            song_id: ID of the song to lock to
            embedding: text embedding vector
            audio_embedding: optional MuQ-MuLan audio embedding vector
        """
        self._vibe_lock["active"] = True
        self._vibe_lock["song_id"] = song_id
        self._vibe_lock["embedding"] = embedding
        self._vibe_lock["audio_embedding"] = audio_embedding
        self._vibe_lock["expires_at"] = time.time() + self._get_vibe_lock_duration_seconds()
        self._vibe_lock["songs_remaining"] = self._get_vibe_lock_max_songs()
        self._current_state = None  # Clear auto mood during vibe lock
        self._save_state()

    def deactivate_vibe_lock(self):
        """Deactivate vibe lock."""
        self._vibe_lock["active"] = False
        self._vibe_lock["song_id"] = None
        self._vibe_lock["embedding"] = None
        self._vibe_lock["audio_embedding"] = None
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

    def get_mood_centroid(self, mood_state=None):
        """Compute the audio embedding centroid for a mood from completed plays.

        Returns a 512-dim vector (list of floats) or None if insufficient data.
        Requires at least 3 completed plays in the given mood.
        """
        import config as app_config
        if not app_config.MUQ_ENABLED:
            return None

        try:
            from llm.muq_engine import is_available
            if not is_available():
                return None
        except ImportError:
            return None

        state = mood_state or self.get_state()
        if state == "neutral":
            return None

        songs = database.get_completed_songs_by_mood(state, limit=50)
        if len(songs) < 3:
            return None

        try:
            import chromadb
            client = chromadb.PersistentClient(path=app_config.CHROMA_PATH)
            audio_collection = client.get_collection(app_config.MUQ_AUDIO_COLLECTION)
            song_ids = [str(s["song_id"]) for s in songs]
            data = audio_collection.get(ids=song_ids, include=["embeddings"])

            if not data or data.get("embeddings") is None:
                return None

            embeddings = data["embeddings"]
            dim = len(embeddings[0])
            centroid = [sum(e[i] for e in embeddings) / len(embeddings) for i in range(dim)]
            return centroid
        except Exception as e:
            logger.debug("Failed to compute mood centroid for %s: %s", state, e)
            return None
