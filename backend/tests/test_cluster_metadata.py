from types import SimpleNamespace

import pytest

from db import database
from engine import retriever


class SearchCollection:
    def query(self, **kwargs):
        return dict(ids=[['1', '99999']], distances=[[0., 0.]],
                    embeddings=[[[1., 0.], [1., 0.]]],
                    metadatas=[[dict(song_id=1, cluster_id=99, energy=.5, valence=.5),
                                dict(song_id=99999, cluster_id=99, energy=.5, valence=.5)]])

    def get(self, **kwargs):
        r = self.query()
        return {'ids': r['ids'][0], 'metadatas': r['metadatas'][0]}


@pytest.mark.parametrize('mode', ['text', 'audio', 'centroid', 'vibe'])
def test_retrieval_uses_sqlite_clusters_even_with_stale_vector_metadata(monkeypatch, mode):
    database.update_song(1, cluster_id=0)
    monkeypatch.setattr(retriever, 'get_chroma_collection', lambda *a: SearchCollection())
    monkeypatch.setattr(database, 'get_cluster_staleness', lambda: {0: 12, 99: 0})
    monkeypatch.setattr(retriever.config, 'MUQ_ENABLED', True)
    if mode == 'centroid':
        rows = retriever.search_by_centroid()
    elif mode == 'vibe':
        rows = retriever._apply_vibe_lock_boost([], {'active': True, 'audio_embedding': [1., 0.]}, [])
    else:
        rows = retriever.search_by_vector([1., 0.], exploration_level=.2,
                                         collection_name='songs_audio' if mode == 'audio' else 'songs')
    assert [r['song_id'] for r in rows] == [1]
    assert rows[0]['cluster_id'] == 0
    if mode in ('text', 'audio'):
        assert rows[0]['diagnostics']['cluster_staleness_boost'] == .06


def test_metadata_refresh_updates_both_spaces_without_replacing_vectors(monkeypatch):
    from db import chroma_sync

    class Collection:
        def __init__(self):
            self.metadata = {'1': {'cluster_id': 99, 'artist': 'keep'}, '2': {'cluster_id': 1}}
            self.calls = []

        def get(self, ids, include):
            found = [sid for sid in ids if sid in self.metadata]
            return {'ids': found, 'metadatas': [dict(self.metadata[sid]) for sid in found]}

        def update(self, ids, metadatas):
            self.calls.append((ids, metadatas))
            for sid, meta in zip(ids, metadatas):
                self.metadata[sid].update(meta)

    spaces = {name: Collection() for name in ['songs', 'songs_audio']}
    client = SimpleNamespace(list_collections=lambda: list(spaces), get_collection=lambda name: spaces[name])
    monkeypatch.setattr(chroma_sync.config, 'MUQ_AUDIO_COLLECTION', 'songs_audio')
    result = chroma_sync.sync_cluster_metadata({1: 0, 2: 1, 3: 2}, client=client)
    for name, coll in spaces.items():
        assert coll.calls == [(['1'], [{'cluster_id': 0}])]
        assert coll.metadata['1']['artist'] == 'keep'
        assert result[name] == {'checked': 2, 'updated': 1, 'missing': 1}
    assert all(r['updated'] == 0 for r in chroma_sync.sync_cluster_metadata({1: 0, 2: 1}, client=client).values())


def test_metadata_refresh_detects_unsuccessful_update():
    from db import chroma_sync
    coll = SimpleNamespace(get=lambda **kw: {'ids': ['1'], 'metadatas': [{'cluster_id': 99}]},
                           update=lambda **kw: None)
    client = SimpleNamespace(list_collections=lambda: ['songs'], get_collection=lambda name: coll)
    with pytest.raises(RuntimeError, match='verification'):
        chroma_sync.sync_cluster_metadata({1: 0}, client=client)


def test_vibe_boost_refreshes_existing_candidate_cluster(monkeypatch):
    database.update_song(1, cluster_id=0)
    monkeypatch.setattr(retriever, 'get_chroma_collection', lambda *a: SearchCollection())
    monkeypatch.setattr(retriever.config, 'MUQ_ENABLED', True)
    rows = retriever._apply_vibe_lock_boost(
        [{'song_id': 1, 'cluster_id': 99, 'score': .5}],
        {'active': True, 'audio_embedding': [1., 0.]}, [])
    assert rows[0]['cluster_id'] == 0
    assert rows[0]['score'] > .5
