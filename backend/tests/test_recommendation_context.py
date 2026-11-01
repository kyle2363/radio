import pytest
from engine import retriever, taste


def test_history_survives_a_new_session(monkeypatch):
    from db import database
    monkeypatch.setattr(database, 'get_recent_signals', lambda **kw: [] if kw.get('session_id') else [
        dict(song_id=1, played_at='2026-01-01', signal='completed')])
    memory = taste.MemorySystem()
    monkeypatch.setattr(memory, '_batch_get_chroma_embeddings', lambda ids, collection: {'1': [1., 0.]})
    vector, _ = memory.get_blended_taste_vector(session_id=99)
    assert vector == pytest.approx([1., 0.])


def test_context_query_uses_anchor_without_positive_history():
    from engine.recommendation_context import context_query
    assert context_query(None, [0., 2.], .2) == pytest.approx([0., 1.])
    assert context_query(None, None, .2) is None


def test_lower_diversity_follows_current_song_more():
    from engine.recommendation_context import context_query
    low = context_query([1., 0.], [0., 1.], .2)
    high = context_query([1., 0.], [0., 1.], .9)
    assert low[1] > high[1]


def test_query_does_not_mutate_vectors():
    from engine.recommendation_context import context_query
    profile, anchor = [2., 0.], [0., 2.]
    context_query(profile, anchor, .2)
    assert profile == [2., 0.] and anchor == [0., 2.]


def test_cold_start_retrieves_audio_from_current_song(monkeypatch):
    import config
    monkeypatch.setattr(config, 'MUQ_ENABLED', True)
    class Collection:
        def get(self, **kwargs):
            return {'embeddings': [[0., 1.]]}
    monkeypatch.setattr(retriever, 'get_chroma_collection', lambda name: Collection())
    calls = []
    def search(vector, **kwargs):
        calls.append((vector, kwargs))
        return [dict(song_id=2, score=.9, energy=.5, diagnostics={'basis': 'current song'})]
    monkeypatch.setattr(retriever, 'search_by_vector', search)
    rows = retriever.build_candidate_list(current_song_id=1, exploration_level=.2)
    assert rows[0]['song_id'] == 2
    assert {kw['collection_name'] for _, kw in calls} == {'songs', 'songs_audio'}
    assert all(v == [0., 1.] and kw['exploration_level'] == .2 for v, kw in calls)


def test_metadata_fallback_is_labelled_and_filters_are_not_relaxed(monkeypatch):
    from db import database
    monkeypatch.setattr(retriever, 'get_chroma_collection', lambda name: pytest.fail('No vector lookup without anchor'))
    monkeypatch.setattr(database, 'get_neutral_centroid_songs', lambda n: [dict(id=1), dict(id=2)])
    monkeypatch.setattr(database, 'get_song', lambda sid: dict(id=sid, subfolder='keep' if sid == 1 else 'other', energy=.5, valence=.5))
    rows = retriever.build_candidate_list(subfolder_filter=['keep'], exploration_level=.2)
    assert [r['song_id'] for r in rows] == [1]
    assert rows[0]['diagnostics']['fallback']
    assert retriever.build_candidate_list(subfolder_filter=['keep'], exclude_ids=[1]) == []


def test_deterministic_reasons_do_not_claim_taste_when_none_exists():
    from engine.reranker import deterministic_assignment
    song = dict(song_id=2, diagnostics={'basis': 'current song', 'current_similarity': .9, 'taste_similarity': None})
    picks = deterministic_assignment([song], {'exploit': [song]})
    assert 'Text song similarity 0.90' in picks[0]['reason']
    assert 'taste' not in picks[0]['reason']


@pytest.mark.parametrize('blend,weights', [(.5, '50/50'), (.75, '75/25')])
def test_reason_labels_audio_scores_and_separates_weighting(blend, weights):
    from engine.reranker import deterministic_assignment
    basis = dict(basis='current song and listening history', current_similarity=.91, taste_similarity=.85)
    song = dict(song_id=2, diagnostics=dict(audio=basis, text=basis, audio_blend=blend))
    reason = deterministic_assignment([song], {'exploit': [song]})[0]['reason']
    assert 'Audio song similarity 0.91' in reason
    assert 'Audio taste similarity 0.85' in reason
    assert f'Audio/text: {weights}' in reason
    assert 'audio blend' not in reason


@pytest.mark.parametrize('source,label', [('audio', 'Audio'), ('text', 'Text')])
def test_single_space_reason_does_not_claim_a_blend(source, label):
    from engine.reranker import deterministic_assignment
    song = dict(song_id=2, source=source, diagnostics=dict(basis='current song', current_similarity=.9))
    reason = deterministic_assignment([song], {'exploit': [song]})[0]['reason']
    assert f'{label} song similarity 0.90' in reason
    assert 'Audio/text' not in reason


def test_neighborhood_union_scores_all_candidates_and_serializes(monkeypatch):
    import json
    import numpy as np
    from db import database
    class Collection:
        calls = []
        def query(self, **kwargs):
            self.calls.append(kwargs)
            sid = '2' if len(self.calls) == 1 else '3'
            vector = [0., 1.] if sid == '2' else [1., 0.]
            return dict(ids=[[sid]], distances=[[.1]], metadatas=[[dict(energy=.5, valence=.5)]],
                        embeddings=[np.array([vector], dtype=np.float32)])
    collection = Collection()
    monkeypatch.setattr(retriever, 'get_chroma_collection', lambda name: collection)
    monkeypatch.setattr(database, 'get_cluster_staleness', lambda: {})
    monkeypatch.setattr(database, '_get_cached_setting', lambda key: pytest.fail('Session diversity must be explicit'))
    rows = retriever.search_by_vector([1., 0.], exploration_level=.2,
                                     profile_vector=[0., 1.], anchor_vector=[1., 0.])
    assert [r['song_id'] for r in rows] == [3, 2]
    assert len(collection.calls) == 3
    assert rows[0]['diagnostics']['current_similarity'] == 1.
    json.dumps(rows)
