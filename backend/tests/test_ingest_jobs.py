"""Job and recovery guarantees independent of model implementations."""
from contextlib import nullcontext
import threading

import pytest

from ingest import pipeline
from db import database


def test_reserved_job_rejects_overlap_even_when_phase_resets_progress():
    entered, release = threading.Event(), threading.Event()
    def work():
        pipeline._reset_progress()
        entered.set()
        assert release.wait(5)
    try:
        assert pipeline.start_job(work)
        assert entered.wait(5)
        assert pipeline.get_ingest_progress()['running']
        assert not pipeline.start_job(lambda: None)
        assert pipeline.run_job(lambda: None)['status'] == 'already_running'
    finally:
        release.set()
    # Acquire after the worker releases its ownership, without polling.
    assert pipeline._job_lock.acquire(timeout=5)
    pipeline._job_lock.release()
    assert not pipeline.get_ingest_progress()['running']


def test_job_releases_after_failure_and_nested_phases_remain_owned():
    def work():
        assert pipeline.run_job(lambda: 42) == 42
        raise ValueError('failed')
    with pytest.raises(ValueError, match='failed'):
        pipeline.run_job(work)
    assert not pipeline.get_ingest_progress()['running']
    assert pipeline.run_job(lambda: 43) == 43


def test_metadata_survives_tag_provider_failure_and_can_retry(monkeypatch):
    monkeypatch.setattr(pipeline, 'model_stage', lambda _: nullcontext())
    database.set_ingest_phase(1, 'analyzed')
    monkeypatch.setattr(pipeline.metadata, 'load_all_metadata', lambda: {})
    monkeypatch.setattr(pipeline.metadata, 'get_metadata_for_mp3', lambda *a: {'caption': 'primary caption', 'lyrics': 'primary lyrics'})
    def failed(*a, **kw):
        raise RuntimeError('offline')
    monkeypatch.setattr(pipeline.llm_tags, 'generate_tags', failed)
    pipeline.run_phase_describe()
    song = database.get_song(1)
    assert song['ai_description'] == 'primary caption'
    assert song['ingest_phase'] == 'analyzed'
    assert song['ingest_error']
    monkeypatch.setattr(pipeline.llm_tags, 'generate_tags', lambda *a, **kw: {'emotional_tags': ['calm'], 'genre_tags': ['pop'], 'instrument_tags': ['piano']})
    pipeline.run_phase_describe()
    song = database.get_song(1)
    assert song['ingest_phase'] == 'described'
    assert not song['ingest_error']


def test_normalization_invalidates_only_changed_finished_inputs():
    song = database.get_song(1)
    pipeline.apply_normalization_updates([{'id': 1, 'energy': song['energy'], 'valence': song['valence']}])
    assert database.get_song(1)['ingest_phase'] == 'clustered'
    pipeline.apply_normalization_updates([{'id': 1, 'energy': 0.1234}])
    assert database.get_song(1)['ingest_phase'] == 'described'


def test_model_stage_releases_previous_model_before_loading_next(monkeypatch):
    from engine import process_manager
    calls = []
    class Manager:
        def get(self, name):
            return type('Process', (), {'is_running': name == 'llama_chat'})()
        def start(self, name):
            calls.append(('start', name))
            return True
        def stop(self, name):
            calls.append(('stop', name))
    monkeypatch.setattr(process_manager, 'get_process_manager', lambda: Manager())
    with pipeline.model_stage('llama_embed'):
        pass
    assert calls.index(('stop', 'llama_chat')) < calls.index(('start', 'llama_embed'))
