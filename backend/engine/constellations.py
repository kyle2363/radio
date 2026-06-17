"""Taste Constellations — multi-centroid taste model.

Instead of a single averaged taste vector, clusters positive signals
into taste pockets. Each pocket has its own centroid, weight, and
primary mood. Used to reweight candidates and enable intelligent
taste hopping.
"""

import logging
import random
import math
import threading
from dataclasses import dataclass, field
from time import time

import config
from db import database
from db.chroma import get_chroma_client
from engine.utils import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass
class TastePocket:
    """A single taste cluster — a pocket of musical affinity."""
    constellation_id: int
    centroid_text: list | None
    centroid_audio: list | None
    weight: float                    # proportional to signal count
    signal_count: int
    primary_mood: str                # most common mood during plays in this pocket
    song_ids: list[int] = field(default_factory=list)
    energy_avg: float = 0.5
    valence_avg: float = 0.5


class TasteConstellations:
    """Multi-centroid taste model.

    Computes taste pockets from positive behavioral signals,
    then uses them to reweight candidates and enable taste hopping.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._pockets: list[TastePocket] = []
        self._active_pocket_idx: int | None = None
        self._pocket_similarity: list[list[float]] = []
        self._last_built: float = 0
        self._total_positive_signals: int = 0
        self._last_silhouette: float | None = None
        self._last_k: int | None = None
        self._n_pca_components: int | None = None
        self._global_energy_mean: float = 0.5
        self._global_valence_mean: float = 0.5

    @property
    def pockets(self) -> list[TastePocket]:
        with self._lock:
            return list(self._pockets)

    @property
    def is_active(self) -> bool:
        """True when constellations are computed and usable."""
        with self._lock:
            return len(self._pockets) >= 2

    @property
    def active_pocket(self) -> TastePocket | None:
        with self._lock:
            if self._active_pocket_idx is not None and self._active_pocket_idx < len(self._pockets):
                return self._pockets[self._active_pocket_idx]
        return None

    def _batch_fetch_embeddings(self, song_ids: list[int], collection_name: str) -> dict:
        """Batch-fetch embeddings from ChromaDB. Returns {str(song_id): embedding}."""
        if not song_ids:
            return {}
        # Deduplicate IDs — ChromaDB requires unique IDs
        seen = set()
        unique_ids = []
        for sid in song_ids:
            key = str(sid)
            if key not in seen:
                seen.add(key)
                unique_ids.append(key)
        try:
            client = get_chroma_client()
            collection = client.get_collection(collection_name)
            result = collection.get(ids=unique_ids, include=["embeddings"])
            embs = result["embeddings"]
            return {
                eid: emb
                for eid, emb in zip(result["ids"], embs)
                if emb is not None
            }
        except Exception as e:
            logger.debug("Batch fetch failed for %d songs from %s: %s", len(unique_ids), collection_name, e)
            return {}

    def _fetch_signal_context(self, song_ids: list[int]) -> dict:
        """Fetch mood_state and energy/valence for each song from play_events."""
        if not song_ids:
            return {}
        try:
            conn = database.get_connection()
            try:
                placeholders = ",".join("?" for _ in song_ids)
                rows = conn.execute(
                    f"""SELECT song_id, mood_state,
                               AVG(0.7 * s.energy + 0.3 * COALESCE(s.user_energy, s.energy)) as energy,
                               AVG(s.valence) as valence
                        FROM play_events pe
                        LEFT JOIN songs s ON s.id = pe.song_id
                        WHERE pe.song_id IN ({placeholders})
                          AND pe.signal IN ('completed', 'hearted', 'replayed')
                        GROUP BY pe.song_id""",
                    song_ids,
                ).fetchall()
                return {
                    r["song_id"]: {
                        "mood_state": r["mood_state"] or "neutral",
                        "energy": r["energy"] if r["energy"] is not None else 0.5,
                        "valence": r["valence"] if r["valence"] is not None else 0.5,
                    }
                    for r in rows
                }
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Failed to fetch signal context: %s", e)
            return {}

    def _fetch_song_artists(self, song_ids: list[int]) -> dict:
        """Fetch artist name for each song. Returns {song_id: artist_name}."""
        if not song_ids:
            return {}
        try:
            conn = database.get_connection()
            try:
                placeholders = ",".join("?" for _ in song_ids)
                rows = conn.execute(
                    f"SELECT id, artist FROM songs WHERE id IN ({placeholders})",
                    song_ids,
                ).fetchall()
                return {r["id"]: r["artist"] or "Unknown" for r in rows}
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Failed to fetch song artists: %s", e)
            return {}

    def _blend_with_artist_centroids(self, song_ids, text_embeddings, artists):
        """Blend each song's embedding with its artist centroid.

        Same-artist songs pull toward their artist's centroid,
        making artist identity a stronger clustering signal.

        Args:
            song_ids: list of song IDs (aligned with text_embeddings keys)
            text_embeddings: dict {str(song_id): embedding_list}
            artists: dict {song_id: artist_name}

        Returns:
            dict {str(song_id): blended_embedding} — same keys as text_embeddings
        """
        blend = config.CONSTELLATION_ARTIST_BLEND
        if blend <= 0:
            return text_embeddings

        # Group song IDs by artist
        artist_songs = {}
        for sid in song_ids:
            artist = artists.get(sid, "Unknown")
            if artist not in artist_songs:
                artist_songs[artist] = []
            artist_songs[artist].append(sid)

        # Compute per-artist centroid (average of their song embeddings)
        artist_centroids = {}
        for artist, aids in artist_songs.items():
            vecs = [text_embeddings[str(a)] for a in aids if str(a) in text_embeddings]
            if not vecs:
                continue
            dim = len(vecs[0])
            centroid = [0.0] * dim
            for vec in vecs:
                for d in range(dim):
                    centroid[d] += vec[d]
            artist_centroids[artist] = [v / len(vecs) for v in centroid]

        # Blend each song with its artist centroid
        blended = {}
        for sid in song_ids:
            key = str(sid)
            if key not in text_embeddings:
                continue
            original = text_embeddings[key]
            artist = artists.get(sid, "Unknown")
            centroid = artist_centroids.get(artist)
            if centroid is None:
                blended[key] = original
                continue
            # blended = (1 - blend) * original + blend * artist_centroid
            dim = len(original)
            b = [0.0] * dim
            for d in range(dim):
                b[d] = (1.0 - blend) * original[d] + blend * centroid[d]
            blended[key] = b

        return blended

    def _adaptive_k(self, n_samples: int) -> int:
        """Pick K using silhouette score across k=2..max_k.

        Falls back to a heuristic if sklearn is unavailable.
        """
        max_k = min(config.CONSTELLATION_MAX_CLUSTERS, n_samples // config.CONSTELLATION_MIN_CLUSTER_SIZE)
        if max_k < 2:
            return 0

        try:
            import numpy as np
            from sklearn.cluster import KMeans
            from sklearn.metrics import silhouette_score
        except ImportError:
            logger.debug("sklearn unavailable, using heuristic K selection")
            return self._heuristic_k(n_samples)

        # Can't compute silhouette with k >= n_samples
        max_k = min(max_k, n_samples - 1)
        if max_k < 2:
            return 0

        best_k = 2
        best_score = -1

        for k in range(2, max_k + 1):
            try:
                km = KMeans(n_clusters=k, random_state=42, n_init=5, max_iter=100)
                labels = km.fit_predict(self._feature_matrix)
                if len(set(labels)) < 2:
                    continue
                score = silhouette_score(self._feature_matrix, labels, sample_size=min(500, n_samples))
                if score > best_score:
                    best_score = score
                    best_k = k
            except Exception:
                continue

        logger.info("Adaptive K: best_k=%d silhouette=%.3f", best_k, best_score)
        return best_k

    def _heuristic_k(self, n_samples: int) -> int:
        """Fallback K selection when sklearn silhouette is unavailable."""
        if n_samples < 15:
            return 2
        elif n_samples < 40:
            return 3
        elif n_samples < 80:
            return 4
        elif n_samples < 150:
            return 5
        else:
            return min(6, config.CONSTELLATION_MAX_CLUSTERS)

    def build(self, memory_system=None):
        """Build taste constellations from positive signals.

        Called during queue rebuild when signal count crosses threshold.
        All shared state is updated atomically at the end.
        """
        with self._lock:
            if time() - self._last_built < config.CONSTELLATION_CACHE_TTL:
                return

        if (database.get_setting("constellations_enabled") or "true") == "false":
            with self._lock:
                self._pockets = []
                self._total_positive_signals = 0
                self._last_built = time()
            return

        try:
            signals = database.get_recent_signals(
                signal_types=["completed", "hearted", "replayed"],
                limit=2000,
            )
        except Exception as e:
            logger.warning("Failed to fetch signals for constellations: %s", e)
            return

        total_signals = len(signals)

        if total_signals < config.CONSTELLATION_MIN_SIGNALS:
            logger.debug("Not enough signals for constellations (%d < %d)",
                         total_signals, config.CONSTELLATION_MIN_SIGNALS)
            with self._lock:
                self._pockets = []
                self._total_positive_signals = total_signals
                self._last_built = time()
            return

        # Collect unique song IDs
        song_ids = list(dict.fromkeys(s["song_id"] for s in signals))
        if not song_ids:
            with self._lock:
                self._pockets = []
                self._total_positive_signals = total_signals
                self._last_built = time()
            return

        # Fetch text embeddings
        text_embeddings = self._batch_fetch_embeddings(song_ids, "songs")

        # Fetch audio embeddings if available
        audio_embeddings = {}
        if config.MUQ_ENABLED:
            audio_embeddings = self._batch_fetch_embeddings(song_ids, config.MUQ_AUDIO_COLLECTION)

        # Fetch mood/energy context
        context = self._fetch_signal_context(song_ids)

        # Build aligned arrays — only songs with text embeddings
        aligned_ids = [sid for sid in song_ids if str(sid) in text_embeddings]
        if len(aligned_ids) < config.CONSTELLATION_MIN_CLUSTER_SIZE * 2:
            logger.debug("Not enough embedded songs for constellations (%d)", len(aligned_ids))
            with self._lock:
                self._pockets = []
                self._total_positive_signals = total_signals
                self._last_built = time()
            return

        # Optional artist centroid blending
        blended_embeddings = text_embeddings
        if config.CONSTELLATION_ARTIST_BLEND > 0:
            artists = self._fetch_song_artists(song_ids)
            blended_embeddings = self._blend_with_artist_centroids(aligned_ids, text_embeddings, artists)
            logger.debug("Artist blending: blend=%.2f, %d songs blended",
                         config.CONSTELLATION_ARTIST_BLEND, len(blended_embeddings))

        # Build feature matrix for clustering
        try:
            import numpy as np
        except ImportError:
            logger.warning("numpy unavailable, cannot build constellations")
            with self._lock:
                self._pockets = []
                self._total_positive_signals = total_signals
                self._last_built = time()
            return

        # --- Audio-primary feature construction ---
        # For songs with audio embeddings, use audio as the primary signal (512-dim).
        # For songs without audio, fall back to text embeddings (1024-dim).
        # PCA reduces both to a common low-dim space.
        feature_rows = []
        audio_centric_ids = []
        text_fallback_ids = []
        for sid in aligned_ids:
            sk = str(sid)
            audio_emb = audio_embeddings.get(sk) if config.MUQ_ENABLED else None
            if audio_emb is not None:
                feature_rows.append(audio_emb)
                audio_centric_ids.append(sid)
            else:
                feature_rows.append(blended_embeddings[sk])
                text_fallback_ids.append(sid)

        feature_matrix = np.array(feature_rows)
        self._feature_matrix = feature_matrix

        # --- PCA dimensionality reduction (20 components) ---
        try:
            from sklearn.decomposition import PCA
            n_comp = min(20, len(feature_matrix) - 1)
            if n_comp >= 2 and feature_matrix.shape[1] > n_comp:
                pca = PCA(n_components=n_comp, random_state=42)
                feature_matrix = pca.fit_transform(feature_matrix)
            self._n_pca_components = n_comp
        except Exception as e:
            logger.debug("PCA failed, using raw features: %s", e)
            self._n_pca_components = feature_matrix.shape[1]

        # --- Global energy/valence mean for diversity filter ---
        all_energies = [context.get(sid, {}).get("energy", 0.5) for sid in aligned_ids]
        self._global_energy_mean = sum(all_energies) / len(all_energies) if all_energies else 0.5
        all_valences = [context.get(sid, {}).get("valence", 0.5) for sid in aligned_ids]
        self._global_valence_mean = sum(all_valences) / len(all_valences) if all_valences else 0.5

        # Adaptive K selection
        k = self._adaptive_k(len(aligned_ids))
        if k < 2:
            logger.debug("Adaptive K returned %d, skipping constellations", k)
            with self._lock:
                self._pockets = []
                self._total_positive_signals = total_signals
                self._last_built = time()
            return

        # Run KMeans
        try:
            from sklearn.cluster import KMeans
            from sklearn.metrics import silhouette_score
            kmeans = KMeans(n_clusters=k, random_state=42, n_init=10, max_iter=300)
            labels = kmeans.fit_predict(feature_matrix)
            if len(set(labels)) >= 2:
                self._last_silhouette = float(silhouette_score(feature_matrix, labels, sample_size=min(500, len(feature_matrix))))
            else:
                self._last_silhouette = None
        except Exception as e:
            logger.warning("KMeans failed: %s", e)
            self._last_silhouette = None
            with self._lock:
                self._pockets = []
                self._total_positive_signals = total_signals
                self._last_built = time()
            return

        self._last_k = k

        # Build pockets from clusters
        raw_pockets = []
        for cluster_id in range(k):
            mask = labels == cluster_id
            cluster_song_ids = [aligned_ids[i] for i in range(len(aligned_ids)) if mask[i]]
            cluster_signal_count = sum(
                1 for s in signals if s["song_id"] in set(cluster_song_ids)
            )

            if cluster_signal_count < config.CONSTELLATION_MIN_CLUSTER_SIZE:
                continue

            # Text centroid (from original embedding space — not PCA-reduced)
            cluster_text_embs = [
                blended_embeddings[str(sid)]
                for sid in cluster_song_ids
                if str(sid) in blended_embeddings
            ]
            centroid_text = []
            if cluster_text_embs:
                dim = len(cluster_text_embs[0])
                centroid_text = [0.0] * dim
                for emb in cluster_text_embs:
                    for d in range(dim):
                        centroid_text[d] += emb[d]
                centroid_text = [v / len(cluster_text_embs) for v in centroid_text]

            # Audio centroid (mean of raw audio embeddings in this cluster)
            centroid_audio = None
            if audio_embeddings:
                audio_vecs = [
                    audio_embeddings[str(sid)]
                    for sid in cluster_song_ids
                    if str(sid) in audio_embeddings
                ]
                if audio_vecs:
                    dim = len(audio_vecs[0])
                    centroid_audio = [0.0] * dim
                    for vec in audio_vecs:
                        for d in range(dim):
                            centroid_audio[d] += vec[d]
                    centroid_audio = [v / len(audio_vecs) for v in centroid_audio]

            # Energy/valence average
            energies = [context.get(sid, {}).get("energy", 0.5) for sid in cluster_song_ids]
            valences = [context.get(sid, {}).get("valence", 0.5) for sid in cluster_song_ids]
            energy_avg = sum(energies) / len(energies) if energies else 0.5
            valence_avg = sum(valences) / len(valences) if valences else 0.5

            # Primary mood (mode of mood states in this cluster)
            moods = [context.get(sid, {}).get("mood_state", "neutral") for sid in cluster_song_ids]
            primary_mood = max(set(moods), key=moods.count) if moods else "neutral"

            raw_pockets.append(TastePocket(
                constellation_id=cluster_id,
                centroid_text=centroid_text,
                centroid_audio=centroid_audio,
                weight=cluster_signal_count / max(1, total_signals),
                signal_count=cluster_signal_count,
                primary_mood=primary_mood,
                song_ids=cluster_song_ids,
                energy_avg=energy_avg,
                valence_avg=valence_avg,
            ))

        # --- Diversity filter: drop energy-similar low-weight pockets ---
        pockets = []
        for p in raw_pockets:
            energy_diff = abs(p.energy_avg - self._global_energy_mean)
            is_energy_similar = energy_diff < 0.05
            is_low_weight = p.weight < 0.15
            if is_energy_similar and is_low_weight:
                logger.debug("Dropping pocket %d (energy=%.3f, weight=%.3f) — too similar to global mean",
                             p.constellation_id, p.energy_avg, p.weight)
                continue
            pockets.append(p)

        # Fallback: if filter depletes us below 2, keep top-2 by weight
        if len(pockets) < 2 and raw_pockets:
            pockets = sorted(raw_pockets, key=lambda p: p.weight, reverse=True)[:2]
            # Reassign constellation_ids to be sequential
            for i, p in enumerate(pockets):
                p.constellation_id = i

        if len(pockets) < 2:
            logger.debug("Only %d pockets after diversity filter, skipping constellations", len(pockets))
            with self._lock:
                self._pockets = []
                self._total_positive_signals = total_signals
                self._last_built = time()
            return

        # Atomic state update
        with self._lock:
            self._pockets = pockets
            self._total_positive_signals = total_signals
            self._compute_pocket_similarity()
            self._last_built = time()
            _summary_parts = [
                f"Built {len(pockets)} pockets from {total_signals} signals (K={k})",
                f"silhouette={self._last_silhouette:.3f}" if self._last_silhouette is not None else "silhouette=N/A",
                f"PCA={self._n_pca_components}",
                f"energies=[{','.join(f'{p.energy_avg:.2f}' for p in pockets)}]",
            ]
            logger.info("Constellation: %s", " | ".join(_summary_parts))

    def _compute_pocket_similarity(self):
        """Compute cosine similarity between all pocket centroids."""
        n = len(self._pockets)
        self._pocket_similarity = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                ci = self._pockets[i].centroid_text
                cj = self._pockets[j].centroid_text
                if ci and cj:
                    sim = cosine_similarity(ci, cj)
                    self._pocket_similarity[i][j] = sim
                    self._pocket_similarity[j][i] = sim

    def select_active_pocket(self, current_song=None, mood_state="neutral", hour=12):
        """Select the active taste pocket based on current context.

        Returns the selected TastePocket or None.
        """
        with self._lock:
            if not self._pockets:
                return None

            # If current song exists, find which pocket it belongs to
            if current_song:
                song_id = current_song.get("id")
                if song_id is not None:
                    for i, pocket in enumerate(self._pockets):
                        if song_id in pocket.song_ids:
                            self._active_pocket_idx = i
                            return pocket

                # Fallback: find nearest pocket by centroid similarity
                song_emb = current_song.get("embedding")
                if song_emb:
                    best_idx = 0
                    best_sim = -1
                    for i, pocket in enumerate(self._pockets):
                        if pocket.centroid_text:
                            sim = cosine_similarity(song_emb, pocket.centroid_text)
                            if sim > best_sim:
                                best_sim = sim
                                best_idx = i
                    self._active_pocket_idx = best_idx
                    return self._pockets[best_idx]

            # No current song: select by mood alignment
            best_idx = 0
            best_score = -1
            for i, pocket in enumerate(self._pockets):
                score = pocket.weight  # base score = weight
                # Mood alignment bonus
                if pocket.primary_mood == mood_state:
                    score += 0.2
                # Time-of-day bias: morning → higher energy, night → lower energy
                hour_factor = math.sin((hour - 6) * math.pi / 12)  # peaks at noon, troughs at midnight
                energy_diff = abs(pocket.energy_avg - (0.5 + 0.3 * hour_factor))
                score -= energy_diff * config.CONSTELLATION_TIME_WEIGHT
                if score > best_score:
                    best_score = score
                    best_idx = i

        self._active_pocket_idx = best_idx
        return self._pockets[best_idx]

    def reweight_candidates(self, candidates, active_pocket=None):
        """Boost candidates aligned with the active constellation.

        Energy-aware: only boosts candidates whose energy is within ±0.15
        of the active pocket's energy average.  This prevents uniform
        boosting of every pocket-aligned song regardless of energy context.

        Returns the reweighted candidates (mutates in place).
        """
        if not active_pocket or not candidates:
            return candidates

        pocket_energy = active_pocket.energy_avg
        energy_low = pocket_energy - 0.15
        energy_high = pocket_energy + 0.15

        with self._lock:
            pocket_sets = {p.constellation_id: set(p.song_ids) for p in self._pockets}
            active_set = pocket_sets.get(active_pocket.constellation_id, set())
            neighbor_threshold = config.CONSTELLATION_NEIGHBOR_THRESHOLD
            neighbor_sims = {}
            for i, pocket in enumerate(self._pockets):
                if pocket.constellation_id == active_pocket.constellation_id:
                    continue
                sim = self._pocket_similarity[active_pocket.constellation_id][i] if (
                    active_pocket.constellation_id < len(self._pocket_similarity)
                    and i < len(self._pocket_similarity[active_pocket.constellation_id])
                ) else 0.0
                if sim > neighbor_threshold:
                    neighbor_sims[pocket.constellation_id] = (sim, pocket.weight)
        for c in candidates:
            c_energy = c.get("energy", 0.5)
            if c_energy < energy_low or c_energy > energy_high:
                continue  # energy mismatch — no boost

            boost = 0.0

            # Direct constellation match: candidate song is in the active pocket
            if c["song_id"] in active_set:
                boost = config.CONSTELLATION_WEIGHT_BOOST * active_pocket.weight
            else:
                # Check neighboring pockets
                for cid, (sim, weight) in neighbor_sims.items():
                    cid_set = pocket_sets.get(cid)
                    if cid_set and c["song_id"] in cid_set:
                        boost = config.CONSTELLATION_WEIGHT_BOOST * 0.5 * weight
                        break

            if boost > 0:
                c["score"] = min(1.0, c.get("score", 0.5) + boost)

        return candidates

    def should_hop(self, active_pocket, exploration_level=0.3):
        """Decide whether to taste-hop and to which pocket.

        Returns a TastePocket to hop to, or None.
        """
        with self._lock:
            if not active_pocket or not self._pockets or len(self._pockets) < 2:
                return None

            hop_chance = config.CONSTELLATION_HOP_CHANCE * (1 + exploration_level * 0.5)
            if random.random() > hop_chance:
                return None

            # Find neighboring pockets (high similarity to active)
            active_idx = active_pocket.constellation_id
            neighbors = []
            for i, pocket in enumerate(self._pockets):
                if pocket.constellation_id == active_idx:
                    continue
                sim = self._pocket_similarity[active_idx][i] if (
                    active_idx < len(self._pocket_similarity)
                    and i < len(self._pocket_similarity[active_idx])
                ) else 0.0
                if sim > 0.5:
                    neighbors.append((pocket, sim))

            if not neighbors:
                return None

            # Weight by similarity
            total_sim = sum(s for _, s in neighbors)
            if total_sim <= 0:
                return None

            r = random.random() * total_sim
            cumulative = 0
            for pocket, sim in neighbors:
                cumulative += sim
                if r <= cumulative:
                    return pocket

            return neighbors[-1][0]

    def get_constellation_summary(self) -> dict:
        """Return constellation data for the API/debug view."""
        if (database.get_setting("constellations_enabled") or "true") == "false":
            return {
                "active": False,
                "reason": "disabled_by_setting",
                "total_positive_signals": 0,
            }
        with self._lock:
            if not self._pockets:
                return {
                    "active": False,
                    "reason": "no_pockets",
                    "total_positive_signals": self._total_positive_signals,
                }

            return {
                "active": self.is_active,
                "influencing": self.active_pocket is not None,
                "pocket_count": len(self._pockets),
                "total_positive_signals": self._total_positive_signals,
                "pockets": [
                    {
                        "id": p.constellation_id,
                        "weight": round(p.weight, 3),
                        "signal_count": p.signal_count,
                        "primary_mood": p.primary_mood,
                        "energy_avg": round(p.energy_avg, 3),
                        "valence_avg": round(p.valence_avg, 3),
                        "song_count": len(p.song_ids),
                        "song_ids": p.song_ids,
                    }
                    for p in self._pockets
                ],
                "active_pocket": self.active_pocket.constellation_id if self.active_pocket else None,
                "similarity_matrix": [
                    [round(val, 3) for val in row]
                    for row in self._pocket_similarity
                ],
            }

    def get_diagnostics(self) -> dict:
        """Return detailed diagnostics about constellation health.

        Computes per-pocket completion/skip/heart rates from play_events,
        plus overall silhouette score from the last build.
        """
        if (database.get_setting("constellations_enabled") or "true") == "false":
            return {
                "active": False,
                "reason": "disabled_by_setting",
                "total_positive_signals": 0,
                "silhouette": None,
                "pockets": [],
            }
        import statistics
        with self._lock:
            if not self._pockets:
                return {
                    "active": False,
                    "total_positive_signals": self._total_positive_signals,
                    "silhouette": None,
                    "pockets": [],
                }

            # Determine which songs belong to which pocket
            pocket_of_song = {}
            for p in self._pockets:
                for sid in p.song_ids:
                    pocket_of_song[sid] = p.constellation_id

            try:
                all_song_ids = list(pocket_of_song.keys())
                conn = database.get_connection()
                try:
                    placeholders = ",".join("?" for _ in all_song_ids)
                    rows = conn.execute(
                        f"""SELECT song_id, signal FROM play_events
                            WHERE song_id IN ({placeholders})
                              AND signal IN ('completed','skip_early','skip_late','hearted','thumbed_down')""",
                        all_song_ids,
                    ).fetchall()
                finally:
                    conn.close()

                # Per-pocket signal tallies
                tally: dict[int, dict[str, int]] = {}
                for p in self._pockets:
                    tally[p.constellation_id] = {
                        "completed": 0, "skip_early": 0, "skip_late": 0,
                        "hearted": 0, "thumbed_down": 0, "total": 0,
                    }
                for r in rows:
                    pid = pocket_of_song.get(r["song_id"])
                    if pid is not None and r["signal"] in tally.get(pid, {}):
                        tally[pid][r["signal"]] += 1
                        tally[pid]["total"] += 1

                pocket_diagnostics = []
                for p in self._pockets:
                    t = tally[p.constellation_id]
                    total = t["total"] or 1
                    pocket_diagnostics.append({
                        "id": p.constellation_id,
                        "song_count": len(p.song_ids),
                        "signal_count": p.signal_count,
                        "weight": round(p.weight, 3),
                        "completion_rate": round(t["completed"] / total, 3),
                        "skip_early_rate": round(t["skip_early"] / total, 3),
                        "skip_late_rate": round(t["skip_late"] / total, 3),
                        "heart_rate": round(t["hearted"] / max(1, t["completed"]), 3),
                        "thumbed_down_count": t["thumbed_down"],
                        "energy_avg": round(p.energy_avg, 3),
                        "valence_avg": round(p.valence_avg, 3),
                        "primary_mood": p.primary_mood,
                    })

                # Overall silhouette
                silhouette = self._last_silhouette
                if silhouette is not None:
                    silhouette = round(silhouette, 4)

                # Mean pairwise similarity between pockets (lower = more distinct)
                mean_sim = 0.0
                sim_count = 0
                for i in range(len(self._pockets)):
                    for j in range(i + 1, len(self._pockets)):
                        mean_sim += self._pocket_similarity[i][j]
                        sim_count += 1
                mean_sim = round(mean_sim / max(1, sim_count), 4)

                return {
                    "active": self.is_active,
                    "total_positive_signals": self._total_positive_signals,
                    "pocket_count": len(self._pockets),
                    "silhouette": silhouette,
                    "mean_pocket_similarity": mean_sim,
                    "k": self._last_k,
                    "pca_components": self._n_pca_components,
                    "global_energy_mean": round(self._global_energy_mean, 3),
                    "global_valence_mean": round(self._global_valence_mean, 3),
                    "pockets": pocket_diagnostics,
                }

            except Exception as e:
                logger.debug("Constellation diagnostics failed: %s", e)
                return {"error": str(e)}

    def invalidate(self):
        """Force rebuild on next call."""
        with self._lock:
            self._last_built = 0
            self._pockets = []
            self._active_pocket_idx = None
            self._last_silhouette = None
            self._last_k = None
            self._n_pca_components = None
