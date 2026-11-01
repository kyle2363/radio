from types import SimpleNamespace

import numpy as np

from db import database
from ingest import clusterer, embedder


class ExistingVectors:
    def __init__(self, ids, embeddings):
        self.ids = ids
        self.embeddings = embeddings

    def get(self, ids=None, include=None):
        return {"ids": self.ids, "embeddings": self.embeddings}


def test_described_checkpoint_re_upserts_existing_vectors_and_only_promotes_described(monkeypatch):
    collection = ExistingVectors(["1", "2"], [[1.0], [2.0]])
    client = SimpleNamespace(get_or_create_collection=lambda *args, **kwargs: collection)
    monkeypatch.setattr(embedder, "_get_chroma_client", lambda: client)
    phases = []
    songs = {
        1: {"id": 1, "ingest_phase": "described"},
        2: {"id": 2, "ingest_phase": "clustered"},
    }
    monkeypatch.setattr(database, "get_song", lambda song_id: songs[song_id])
    monkeypatch.setattr(database, "set_ingest_phase", lambda song_id, phase: phases.append((song_id, phase)))
    upserts = []
    monkeypatch.setattr(embedder, "embed_song", lambda song_id, song: upserts.append(song_id) or True)

    result = embedder.embed_described_songs([1, 2])

    assert result == [1, 2]
    assert upserts == [1, 2]
    assert phases == [(1, "embedded"), (2, "clustered")]


def test_embed_song_replaces_existing_vector_without_deleting_first(monkeypatch):
    calls = []

    class Collection:
        def delete(self, **kwargs):
            calls.append(("delete", kwargs))

        def upsert(self, **kwargs):
            calls.append(("upsert", kwargs))

    collection = Collection()
    client = SimpleNamespace(get_or_create_collection=lambda *args, **kwargs: collection)
    monkeypatch.setattr(embedder, "_get_chroma_client", lambda: client)
    monkeypatch.setattr(embedder, "llm_embed", lambda: SimpleNamespace(
        embed=lambda **kwargs: {"embeddings": [[0.25, 0.75]]}
    ))

    assert embedder.embed_song(7, {"title": "Track"}) is True
    assert [call[0] for call in calls] == ["upsert"]


def test_audio_embed_replaces_existing_vector_without_deleting_first(monkeypatch):
    from llm import muq_engine

    calls = []

    class Collection:
        def delete(self, **kwargs):
            calls.append(("delete", kwargs))

        def upsert(self, **kwargs):
            calls.append(("upsert", kwargs))

    monkeypatch.setattr(muq_engine, "embed_audio", lambda filepath: [0.1, 0.9])
    monkeypatch.setattr(embedder, "get_audio_collection", lambda: Collection())

    assert embedder.embed_audio_song(7, "track.mp3", {"title": "Track"}) is True
    assert [call[0] for call in calls] == ["upsert"]


def test_embedding_failure_is_reported_and_does_not_advance_phase(monkeypatch, caplog):
    monkeypatch.setattr(embedder, "_get_chroma_client", lambda: SimpleNamespace(
        get_or_create_collection=lambda *args, **kwargs: ExistingVectors([], [])
    ))
    monkeypatch.setattr(database, "get_song", lambda song_id: {"id": song_id, "ingest_phase": "described"})
    phases = []
    monkeypatch.setattr(database, "set_ingest_phase", lambda *args: phases.append(args))
    monkeypatch.setattr(embedder, "embed_song", lambda *args: False)

    assert embedder.embed_described_songs([4]) == []
    assert phases == []
    assert "Failed to embed song 4" in caplog.text


def test_embed_song_persists_provider_error_for_ingest_review(monkeypatch):
    errors = []
    monkeypatch.setattr(embedder, "llm_embed", lambda: SimpleNamespace(
        embed=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider offline"))
    ))
    monkeypatch.setattr(database, "set_ingest_error", lambda song_id, error: errors.append((song_id, error)))

    assert embedder.embed_song(9, {"title": "Track"}) is False
    assert errors == [(9, "embedding failed: provider offline")]


def test_partial_embedding_coverage_falls_back_to_full_coverage_text(monkeypatch):
    songs = [
        {"id": 1, "energy": 0.2, "valence": 0.4, "bpm": 90},
        {"id": 2, "energy": 0.8, "valence": 0.6, "bpm": 130},
        {"id": 3, "energy": 0.0, "valence": 0.9, "bpm": 160},
    ]
    collections = {
        "songs_audio": ExistingVectors(["1"], [[0.1, 0.2]]),
        "songs": ExistingVectors(["1", "2", "3"], [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]),
    }
    monkeypatch.setattr(clusterer.config, "MUQ_ENABLED", True)
    monkeypatch.setattr(clusterer.config, "MUQ_AUDIO_COLLECTION", "songs_audio")
    monkeypatch.setattr(clusterer, "_get_chroma_collection", lambda name: collections[name])

    matrix, ids, dimensions = clusterer._build_feature_matrix(songs)

    assert ids == [1, 2, 3]
    assert dimensions == (0, 2)
    assert matrix.shape[0] == len(songs)


def test_partial_coverage_without_a_complete_embedding_space_uses_features_for_every_song(monkeypatch):
    songs = [
        {"id": 1, "energy": 0.2, "valence": 0.4, "bpm": 90},
        {"id": 2, "energy": 0.8, "valence": 0.6, "bpm": 130},
    ]
    collections = {
        "songs_audio": ExistingVectors(["1"], [[0.1, 0.2]]),
        "songs": ExistingVectors(["2"], [[0.3, 0.4]]),
    }
    monkeypatch.setattr(clusterer.config, "MUQ_ENABLED", True)
    monkeypatch.setattr(clusterer.config, "MUQ_AUDIO_COLLECTION", "songs_audio")
    monkeypatch.setattr(clusterer, "_get_chroma_collection", lambda name: collections[name])

    matrix, ids, dimensions = clusterer._build_feature_matrix(songs)

    assert ids == [1, 2]
    assert dimensions == (0, 0)
    assert matrix.shape == (2, 3)


def test_zero_scalar_values_are_not_replaced_by_defaults(monkeypatch):
    songs = [
        {"id": 1, "energy": 0.0, "valence": 0.0, "bpm": 0},
        {"id": 2, "energy": 1.0, "valence": 1.0, "bpm": 180},
    ]
    monkeypatch.setattr(clusterer.config, "MUQ_ENABLED", False)
    monkeypatch.setattr(clusterer, "_get_chroma_collection", lambda name: ExistingVectors([], []))

    matrix, ids, _ = clusterer._build_feature_matrix(songs)

    assert ids == [1, 2]
    assert np.all(matrix[0, :] == 0.0)
    assert np.all(matrix[1, :] > 0.0)


def test_single_song_uses_one_cluster(monkeypatch):
    songs = [{"id": 1}]
    monkeypatch.setattr(clusterer, "_get_all_embedded_songs", lambda: songs)
    monkeypatch.setattr(clusterer, "_build_feature_matrix", lambda _: (np.array([[0.0, 0.0, 0.0]]), [1], (0, 0)))
    created = []

    class FakeKMeans:
        def __init__(self, n_clusters, **kwargs):
            created.append(n_clusters)
            self.cluster_centers_ = np.zeros((n_clusters, 3))

        def fit_predict(self, matrix):
            return np.zeros(len(matrix), dtype=int)

    monkeypatch.setattr(clusterer, "KMeans", FakeKMeans)
    monkeypatch.setattr(database, "get_connection", lambda: SimpleNamespace(
        execute=lambda *args: None,
        executemany=lambda *args: None,
        commit=lambda: None,
        close=lambda: None,
    ))

    class Centroids:
        def get(self, **kwargs):
            return {"ids": []}

        def add(self, **kwargs):
            pass

    class Client:
        def get_or_create_collection(self, *args, **kwargs):
            return Centroids()

    monkeypatch.setitem(__import__("sys").modules, "chromadb", SimpleNamespace(PersistentClient=lambda **kwargs: Client()))
    monkeypatch.setattr(clusterer.config, "CHROMA_PATH", "unused-test-path")

    assert clusterer.cluster_songs() == [1]
    assert created == [1]
