"""Regression coverage for desktop library and diagnostics review findings."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from fastapi import HTTPException
from db import database
from db.playback_store import PlaybackStore


def route(name, **namespace):
    source = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    env = dict(database=database, HTTPException=HTTPException, time=time,
               logger=logging.getLogger(__name__), **namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), env)
    return env[name]


@pytest.mark.parametrize('batch', [False, True])
def test_delete_song_cleans_durable_attempts_and_suppression(batch):
    store = PlaybackStore()
    session = store.create_session('owner', 'browser')
    plan = store.prepare(session['id'], 'owner', 'prepare', 0,
                         [{'kind': 'song', 'song_id': 1, 'duration_ms': 1000}])
    item = plan['items'][0]
    store.observe(session['id'], 'owner', 'start', plan['instance_id'], item['item_id'], 'started')
    store.observe(session['id'], 'owner', 'end', plan['instance_id'], item['item_id'], 'completed')
    database.add_song_suppression(1, 'test')
    if batch:
        assert len(database.delete_songs_batch([1])) == 1
    else:
        assert database.delete_song(1)['id'] == 1
    assert database.get_song(1) is None
    with database.get_connection_cm() as conn:
        assert conn.execute('SELECT count(*) FROM playback_attempts WHERE song_id=1').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM song_suppression WHERE song_id=1').fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert store.observe(session['id'], 'owner', 'end', plan['instance_id'], item['item_id'], 'completed')['duplicate']


@pytest.mark.parametrize('params', [{'page_size': 0}, {'page_size': -1}, {'page': 0}, {'page_size': 201}])
def test_song_pagination_rejects_unbounded_or_invalid_values(params):
    with pytest.raises(HTTPException) as exc:
        route('list_songs')(**params)
    assert exc.value.status_code == 422


def test_summary_lists_omit_large_fields_but_detail_stays_available():
    database.update_song(1, lyrics='long lyrics', ai_description='long caption', rms_envelope='[1,2]')
    summary = route('list_songs')(summary=True)['items'][0]
    assert not {'lyrics', 'ai_description', 'rms_envelope'} & summary.keys()
    assert database.get_song(1)['lyrics'] == 'long lyrics'
    assert 'lyrics' in route('list_songs')()['items'][0]


def test_partial_metadata_edit_preserves_omitted_name(monkeypatch):
    from ingest import metadata_writer
    database.update_song(1, artist='Existing Artist', title='Existing Title')
    calls = []
    monkeypatch.setattr(metadata_writer, 'rename_song_files', lambda *args: calls.append(args) or {})
    monkeypatch.setattr(metadata_writer, 'write_id3_tags', lambda *args, **kwargs: {})
    monkeypatch.setattr(metadata_writer, 'write_acestep_json', lambda *args, **kwargs: {})
    req = SimpleNamespace(**{key: None for key in ('artist', 'title', 'album', 'bpm', 'duration_secs',
        'keyscale', 'timesignature', 'vocal_language', 'lyrics', 'caption')})
    req.title = 'New Title'
    result = route('update_song_metadata', MetadataUpdateRequest=object)(1, req)
    assert calls[0][1:] == ('Existing Artist', 'New Title')
    assert result['song']['artist'] == 'Existing Artist'


def test_caption_only_metadata_edit_does_not_rename(monkeypatch):
    from ingest import metadata_writer
    database.update_song(1, artist='Artist', title='Title')
    monkeypatch.setattr(metadata_writer, 'rename_song_files', lambda *args: pytest.fail('Unexpected rename'))
    monkeypatch.setattr(metadata_writer, 'write_id3_tags', lambda *args, **kwargs: {})
    monkeypatch.setattr(metadata_writer, 'write_acestep_json', lambda *args, **kwargs: {})
    req = SimpleNamespace(**{key: None for key in ('artist', 'title', 'album', 'bpm', 'duration_secs',
        'keyscale', 'timesignature', 'vocal_language', 'lyrics', 'caption')})
    req.caption = 'New caption'
    route('update_song_metadata', MetadataUpdateRequest=object)(1, req)


def test_archetype_counts_real_clusters_and_only_wildcard_signals():
    from engine.archetype import detect_archetype
    for song_id in range(1, 7):
        database.update_song(song_id, cluster_id=0)
        database.record_play_event(song_id, None, 'playing', queue_slot='exploit')
        database.record_play_event(song_id, None, 'hearted', queue_slot='exploit')
        database.record_play_event(song_id, None, 'completed', queue_slot='exploit')
    database.record_play_event(1, None, 'hearted', queue_slot='wildcard')
    database.record_play_event(2, None, 'skip_early', queue_slot='wildcard')
    _, metrics = detect_archetype()
    assert metrics['cluster_diversity'] == pytest.approx(1 / 28)
    assert metrics['wildcard_acceptance'] == .5
    assert metrics['total_plays'] == 6


def test_vector_health_rejects_missing_and_stale_ids_even_with_equal_counts():
    class Collection:
        def count(self): return 28
        def get(self, **kwargs): return {'ids': [str(i) for i in range(2, 30)]}
    result = route('_chromadb_health_check', get_chroma_client=lambda: SimpleNamespace(
        get_collection=lambda name: Collection()), config=SimpleNamespace(MUQ_ENABLED=False))()
    assert result['healthy'] is False
    assert result['missing_ids'] == ['1']
    assert result['stale_ids'] == ['29']


def test_vector_health_rejects_unavailable_collection():
    def missing(name): raise RuntimeError('unavailable')
    result = route('_chromadb_health_check', get_chroma_client=lambda: SimpleNamespace(
        get_collection=missing), config=SimpleNamespace(MUQ_ENABLED=False))()
    assert result['healthy'] is False


def test_vector_health_route_has_one_authoritative_registration():
    source = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    routes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
              for decorator in node.decorator_list if isinstance(decorator, ast.Call)
              and decorator.args and isinstance(decorator.args[0], ast.Constant)
              and decorator.args[0].value == '/api/chroma/health']
    assert len(routes) == 1


def test_playback_attempt_records_its_recommendation_slot():
    store = PlaybackStore()
    session = store.create_session('owner', 'browser')
    plan = store.prepare(session['id'], 'owner', 'prepare', 0,
        [{'kind': 'song', 'song_id': 1, 'duration_ms': 1000, 'queue_slot': 'wildcard', 'mood_state': 'chill'}])
    item = plan['items'][0]
    for kind in ('started', 'hearted', 'completed'):
        store.observe(session['id'], 'owner', kind, plan['instance_id'], item['item_id'], kind)
    with database.get_connection_cm() as conn:
        rows = conn.execute('SELECT signal, queue_slot, mood_state FROM play_events').fetchall()
    assert [(r['signal'], r['queue_slot'], r['mood_state']) for r in rows] == [
        ('playing', 'wildcard', 'chill'), ('hearted', 'wildcard', 'chill'), ('completed', 'wildcard', 'chill')]
