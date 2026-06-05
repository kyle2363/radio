"""Multi-timescale memory system for taste tracking.

Supports dual embedding paths:
  - Text taste vector (from LLM text embeddings)
  - Audio taste vector (from MuQ-MuLan audio embeddings)

Both vectors are computed in parallel and returned together.
The retriever blends them using MUQ_AUDIO_BLEND.
"""

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from time import time
import config
from db import database
from engine.utils import cosine_similarity
from engine import retriever

logger = logging.getLogger(__name__)

# ChromaDB singleton — avoids creating a new PersistentClient per call
_chroma_client = None


def _get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        os.environ["CHROMA_NO_ANALYTICS"] = "1"
        os.environ["CHROMA_TELEMETRY_OFF"] = "1"
        import chromadb
        os.makedirs(config.CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return _chroma_client


@dataclass
class MemoryLayer:
    name: str
    window: int  # number of recent signals to consider
    weight: float  # base weight in blend
    decay: float  # exponential decay factor per position


@dataclass
class _TasteCacheEntry:
    text_vector: list | None
    audio_vector: list | None
    drift: float
    expires_at: float


LAYERS = [
    MemoryLayer('immediate', window=10, weight=0.40, decay=0.85),
    MemoryLayer('session', window=50, weight=0.25, decay=0.92),
    MemoryLayer('daily', window=200, weight=0.20, decay=0.96),
    MemoryLayer('longterm', window=2000, weight=0.15, decay=0.99),
]


class MemorySystem:
    """Multi-timescale taste memory.

    Tracks liked/completed/replayed songs across 4 timescales,
    blends them into taste vectors via weighted exponential decay.

    Supports dual embedding paths (text + audio) for the hybrid
    recommendation pipeline.
    """

    def __init__(self):
        self._cache: dict[str, _TasteCacheEntry] = {}
        self._cache_ttl = 15  # seconds

    def _get_chroma_embedding(self, song_id: int, collection_name: str = "songs") -> list | None:
        """Fetch a song's embedding from ChromaDB."""
        try:
            client = _get_chroma_client()
            collection = client.get_collection(collection_name)
            result = collection.get(ids=[str(song_id)], include=["embeddings"])
            if result and result["embeddings"]:
                return result["embeddings"][0]
        except Exception:
            pass
        return None

    def _batch_get_chroma_embeddings(self, song_ids: list, collection_name: str = "songs") -> dict:
        """Batch-fetch embeddings for multiple songs in one ChromaDB call.

        Returns dict: {str(song_id): embedding_list}
        """
        if not song_ids:
            return {}
        # Deduplicate IDs — ChromaDB requires unique IDs, but callers may
        # pass lists with repeats (e.g., multiple play_events for same song).
        seen = set()
        unique_ids = []
        for sid in song_ids:
            key = str(sid)
            if key not in seen:
                seen.add(key)
                unique_ids.append(key)
        try:
            client = _get_chroma_client()
            collection = client.get_collection(collection_name)
            result = collection.get(ids=unique_ids, include=["embeddings"])
            return {
                eid: emb
                for eid, emb in zip(result["ids"], result["embeddings"])
                if emb
            }
        except Exception as e:
            logger.debug("Batch ChromaDB fetch failed for %d songs from %s: %s",
                         len(unique_ids), collection_name, e)
            return {}

    def get_recent_signals(self, session_id: int = None, skip_streak: int = 0,
                           collection_name: str = "songs") -> list:
        """Get recent behavioral signals (likes, completions, replays).

        Returns list of dicts: {song_id, embedding, timestamp, signal_type}
        """
        liked = database.get_recent_signals(
            signal_types=['hearted', 'replayed', 'completed'],
            limit=2000,
            session_id=session_id,
        )
        if not liked:
            return []

        song_ids = [ev['song_id'] for ev in liked]
        embedding_map = self._batch_get_chroma_embeddings(song_ids, collection_name)

        signals = []
        for ev in liked:
            embedding = embedding_map.get(str(ev['song_id']))
            if embedding:
                signals.append({
                    'song_id': ev['song_id'],
                    'embedding': embedding,
                    'timestamp': ev['played_at'],
                    'signal_type': ev['signal'],
                })
        return signals

    def compute_layer_weights(self, window_size: int) -> list:
        """Compute exponential decay weights for a layer's window."""
        weights = []
        for i in range(window_size):
            w = (1.0 - 0.5) ** (i / max(1, window_size / 5))
            weights.append(w)
        total = sum(weights)
        return [w / total for w in weights]

    def get_layer_vectors(self, signals, layers):
        """Compute a centroid vector for each layer.

        Returns dict: {layer_name: centroid_vector}
        """
        layer_vectors = {}
        for layer in layers:
            layer_signals = signals[:layer.window]
            if not layer_signals:
                continue
            layer_weights = self.compute_layer_weights(len(layer_signals))
            weighted_sum = None
            weight_total = 0
            for i, sig in enumerate(layer_signals):
                emb = sig['embedding']
                w = layer_weights[i]
                if weighted_sum is None:
                    weighted_sum = [v * w for v in emb]
                else:
                    weighted_sum = [a + b * w for a, b in zip(weighted_sum, emb)]
                weight_total += w
            if weighted_sum and weight_total > 0:
                layer_vectors[layer.name] = [v / weight_total for v in weighted_sum]
        return layer_vectors

    def detect_drift(self, layer_vectors):
        """Detect taste drift between immediate and longterm layers.

        Returns drift score (0.0 = aligned, 1.0 = fully diverged),
        or 0.0 if either layer is missing.
        """
        immediate = layer_vectors.get('immediate')
        longterm = layer_vectors.get('longterm')
        if immediate is None or longterm is None:
            return 0.0
        sim = cosine_similarity(immediate, longterm)
        return max(0.0, 1.0 - sim)

    def _compute_taste_vector(self, session_id, skip_streak, collection_name):
        """Core taste vector computation for any collection.

        Returns (vector, drift) tuple.
        """
        cache_key = f"s:{session_id}:sk:{skip_streak}:{collection_name}"
        entry = self._cache.get(cache_key)
        if entry and entry.expires_at > time():
            return entry.text_vector, entry.drift

        signals = self.get_recent_signals(
            session_id=session_id,
            skip_streak=skip_streak,
            collection_name=collection_name,
        )
        if not signals:
            return None, 0.0

        layers = [MemoryLayer(l.name, l.window, l.weight, l.decay) for l in LAYERS]
        if skip_streak >= 3:
            for layer in layers:
                if layer.name == 'immediate':
                    layer.weight += 0.20
                elif layer.name in ('longterm', 'daily'):
                    layer.weight -= 0.05

        total_weight = sum(l.weight for l in layers)
        for layer in layers:
            layer.weight /= total_weight

        layer_vectors = self.get_layer_vectors(signals, layers)
        drift = self.detect_drift(layer_vectors)

        blended = None
        for layer in layers:
            lv = layer_vectors.get(layer.name)
            if lv is None:
                continue
            if blended is None:
                blended = [v * layer.weight for v in lv]
            else:
                blended = [a + b * layer.weight for a, b in zip(blended, lv)]

        # Cache the result
        self._cache[cache_key] = _TasteCacheEntry(
            text_vector=blended,
            audio_vector=None,
            drift=drift,
            expires_at=time() + self._cache_ttl,
        )

        return blended, drift

    def get_blended_taste_vector(self, session_id: int = None, skip_streak: int = 0) -> tuple:
        """Compute blended text taste vector from all memory layers.

        Returns (blended_vector, drift_score) where blended_vector
        is a list of floats or None if no taste signals available.
        """
        return self._compute_taste_vector(session_id, skip_streak, "songs")

    def get_audio_taste_vector(self, session_id: int = None, skip_streak: int = 0) -> tuple:
        """Compute blended audio taste vector from MuQ-MuLan embeddings.

        Returns (blended_vector, drift_score) where blended_vector
        is a list of floats or None if no audio embeddings available.
        """
        if not config.MUQ_ENABLED:
            return None, 0.0
        return self._compute_taste_vector(session_id, skip_streak, config.MUQ_AUDIO_COLLECTION)

    def get_taste_vectors(self, session_id: int = None, skip_streak: int = 0) -> dict:
        """Compute both text and audio taste vectors in one call.

        Returns dict:
            text: text taste vector or None
            audio: audio taste vector or None
            drift: drift score from text vector computation
        """
        text_vec, text_drift = self.get_blended_taste_vector(session_id, skip_streak)
        audio_vec, audio_drift = self.get_audio_taste_vector(session_id, skip_streak)

        # Use the higher drift score (either embedding space may show drift)
        drift = max(text_drift, audio_drift)

        return {
            "text": text_vec,
            "audio": audio_vec,
            "drift": drift,
        }

    def get_neutral_centroid(self) -> list:
        """Return the average embedding of all clustered songs as a neutral centroid.

        Used for cold start when no taste signals exist.
        """
        try:
            conn = database.get_connection()
            try:
                row = conn.execute(
                    "SELECT AVG(energy) as ae, AVG(valence) as av "
                    "FROM songs WHERE ingest_phase = 'clustered'"
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            pass
        return [0.0] * retriever.get_embedding_dimension()

    def _compute_repulsion_vector(self, collection_name: str) -> list | None:
        """Compute time-weighted average embedding of disliked songs.

        Recent skips have full weight; older ones decay exponentially.
        Returns a vector to be subtracted from the taste vector.
        """
        signals = database.get_recent_signals(
            signal_types=['skip_early', 'skip_late', 'thumbed_down'],
            limit=500,
        )
        if not signals:
            return None

        song_ids = [ev['song_id'] for ev in signals]
        embedding_map = self._batch_get_chroma_embeddings(song_ids, collection_name)

        now = datetime.now()
        weighted_sum = None
        weight_total = 0.0
        # Read decay rate once before loop for performance
        try:
            decay_val = database.get_setting("dislike_decay_rate")
            decay_rate = float(decay_val) if decay_val is not None else 0.029
        except (ValueError, TypeError):
            decay_rate = 0.029
        for ev in signals:
            emb = embedding_map.get(str(ev['song_id']))
            if not emb:
                continue
            # Compute time-based weight
            played_at = ev.get('played_at')
            if played_at:
                try:
                    ts = datetime.fromisoformat(played_at)
                    age_hours = max(0.0, (now - ts).total_seconds() / 3600.0)
                except (ValueError, TypeError):
                    age_hours = 0.0
            else:
                age_hours = 0.0
            # Exponential decay: full weight at 0h, ~0.5 at 24h, ~0.1 at 72h
            # Rate is configurable via dislike_decay_rate setting
            w = math.exp(-decay_rate * age_hours)
            if weighted_sum is None:
                weighted_sum = [v * w for v in emb]
            else:
                weighted_sum = [a + b * w for a, b in zip(weighted_sum, emb)]
            weight_total += w

        if weight_total <= 0:
            return None
        return [v / weight_total for v in weighted_sum]

    def get_repulsion_vector(self) -> list | None:
        """Compute average embedding of disliked songs (text embeddings).

        Returns a vector to be subtracted from the taste vector,
        or None if no negative signals exist.
        """
        return self._compute_repulsion_vector("songs")

    def get_audio_repulsion_vector(self) -> list | None:
        """Compute average embedding of disliked songs (audio embeddings).

        Returns a vector to be subtracted from the audio taste vector,
        or None if no negative signals exist.
        """
        if not config.MUQ_ENABLED:
            return None
        return self._compute_repulsion_vector(config.MUQ_AUDIO_COLLECTION)

    def get_positive_signals_for_clustering(self, limit=2000):
        """Return positive signals with song IDs for constellation clustering.

        Convenience wrapper for the constellation system.
        Returns list of dicts with song_id, signal, played_at.
        """
        return database.get_recent_signals(
            signal_types=["completed", "hearted", "replayed"],
            limit=limit,
        )

    def record_signal(self, song_id, signal, session_id=None):
        """Record a behavioral signal to the database.

        Args:
            song_id: ID of the song
            signal: signal type ('hearted', 'replayed', 'completed', etc.)
            session_id: optional session ID
        """
        if song_id:
            database.record_play_event(
                song_id=song_id,
                session_id=session_id,
                signal=signal,
            )
        self._cache.clear()
