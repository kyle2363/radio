"""Multi-timescale memory system for taste tracking."""

import logging
import math
from dataclasses import dataclass
import chromadb
import config
from db import database

logger = logging.getLogger(__name__)


def cosine_similarity(a, b):
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class MemoryLayer:
    name: str
    window: int  # number of recent signals to consider
    weight: float  # base weight in blend
    decay: float  # exponential decay factor per position


LAYERS = [
    MemoryLayer('immediate', window=10, weight=0.40, decay=0.85),
    MemoryLayer('session', window=50, weight=0.25, decay=0.92),
    MemoryLayer('daily', window=200, weight=0.20, decay=0.96),
    MemoryLayer('longterm', window=2000, weight=0.15, decay=0.99),
]


class MemorySystem:
    """Multi-timescale taste memory.

    Tracks liked/completed/replayed songs across 4 timescales,
    blends them into a single taste vector via weighted exponential decay.
    """

    def __init__(self):
        pass

    def _get_chroma_embedding(self, song_id: int) -> list | None:
        """Fetch a song's embedding from ChromaDB."""
        try:
            client = chromadb.PersistentClient(path=config.CHROMA_PATH)
            collection = client.get_collection("songs")
            result = collection.get(ids=[str(song_id)], include=["embeddings"])
            if result and result["embeddings"]:
                return result["embeddings"][0]
        except Exception as e:
            logger.debug("Could not fetch ChromaDB embedding for song %s: %s", song_id, e)
        return None

    def get_recent_signals(self, session_id: int = None, skip_streak: int = 0) -> list:
        """Get recent behavioral signals (likes, completions, replays).

        Returns list of dicts: {song_id, embedding, timestamp, signal_type}
        """
        signals = []
        # Get recent hearted/replayed/completed songs
        liked = database.get_recent_signals(
            signal_types=['hearted', 'replayed', 'completed'],
            limit=2000,
            session_id=session_id,
        )
        for ev in liked:
            embedding = self._get_chroma_embedding(ev['song_id'])
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
        # Drift is inverse of similarity, shifted so 0.0-1.0 range
        return max(0.0, 1.0 - sim)

    def get_blended_taste_vector(self, session_id: int = None, skip_streak: int = 0) -> tuple:
        """Compute blended taste vector from all memory layers.

        Returns (blended_vector, drift_score) where blended_vector
        is a list of floats or None if no taste signals available.
        """
        signals = self.get_recent_signals(session_id=session_id, skip_streak=skip_streak)
        if not signals:
            return None, 0.0

        # Apply skip streak rebalancing on a COPY to avoid mutating LAYERS
        layers = [MemoryLayer(l.name, l.window, l.weight, l.decay) for l in LAYERS]
        if skip_streak >= 3:
            for layer in layers:
                if layer.name == 'immediate':
                    layer.weight += 0.20
                elif layer.name in ('longterm', 'daily'):
                    layer.weight -= 0.05

        # Normalize weights
        total_weight = sum(l.weight for l in layers)
        for layer in layers:
            layer.weight /= total_weight

        # Compute per-layer vectors for drift detection
        layer_vectors = self.get_layer_vectors(signals, layers)
        drift = self.detect_drift(layer_vectors)

        # Blend layers
        blended = None
        blended_count = 0
        for layer in layers:
            lv = layer_vectors.get(layer.name)
            if lv is None:
                continue
            if blended is None:
                blended = [v * layer.weight for v in lv]
            else:
                blended = [a + b * layer.weight for a, b in zip(blended, lv)]
            blended_count += 1

        if blended:
            return blended, drift
        return None, drift

    def get_neutral_centroid(self) -> list:
        """Return a neutral centroid embedding (all zeros, same dim as embeddings).

        Used for cold start when no taste signals exist.
        """
        return [0.0] * 1024

    def get_repulsion_vector(self) -> list | None:
        """Compute average embedding of disliked songs (skip_early, thumbed_down).

        Returns a vector to be subtracted from the taste vector,
        or None if no negative signals exist.
        """
        signals = database.get_recent_signals(
            signal_types=['skip_early', 'thumbed_down'],
            limit=500,
        )
        if not signals:
            return None
        embeddings = []
        for ev in signals:
            emb = self._get_chroma_embedding(ev['song_id'])
            if emb:
                embeddings.append(emb)
        if not embeddings:
            return None
        dim = len(embeddings[0])
        avg = [0.0] * dim
        for emb in embeddings:
            for i in range(dim):
                avg[i] += emb[i]
        avg = [v / len(embeddings) for v in avg]
        return avg

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
