"""Recommendation setting overlays are isolated while shared settings delegate."""

from engine.session_settings import SessionSettings
from tests.test_playback_sessions import domain, start
import pytest


@pytest.fixture
def main_contract(monkeypatch):
    """Keep optional vector/ingest adapters outside these route unit tests.

    The actual route functions, session settings and command dispatch are used.
    Starting ingestion or a vector client is never part of these contracts.
    """
    import importlib
    import sys
    from types import ModuleType
    from unittest.mock import Mock
    import ingest

    if 'main' not in sys.modules:
        vector = ModuleType('chromadb')
        vector.PersistentClient = Mock(side_effect=AssertionError('No vector service in route unit tests'))
        pipeline = Mock(name='isolated_ingest_adapter')
        monkeypatch.setitem(sys.modules, 'chromadb', vector)
        monkeypatch.setitem(sys.modules, 'ingest.pipeline', pipeline)
        monkeypatch.setattr(ingest, 'pipeline', pipeline, raising=False)
    return importlib.import_module('main')


class FakeDatabase:
    def __init__(self):
        self.values = {
            "play_mode": "ai", "current_mode": "radio", "exploration_level": "0.3",
            "tts_enabled": "true",
        }
        self.writes = []

    def get_setting(self, key):
        return self.values.get(key)

    def get_setting_float(self, key, default=0.0):
        value = self.get_setting(key)
        return float(value) if value else default

    def set_setting(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value


def test_recommendation_settings_are_session_local_and_initialized_from_profile():
    db = FakeDatabase()
    first, second = SessionSettings(db), SessionSettings(db)

    first.set_setting("play_mode", "shuffle")
    first.set_setting("exploration_level", "0.8")

    assert first.get_setting("play_mode") == "shuffle"
    assert first.get_setting_float("exploration_level", 0.3) == 0.8
    assert second.get_setting("play_mode") == "ai"
    assert second.get_setting_float("exploration_level", 0.3) == 0.3
    assert db.writes == []


def test_non_recommendation_settings_delegate_to_global_database():
    db = FakeDatabase()
    settings = SessionSettings(db)

    assert settings.get_setting("tts_enabled") == "true"
    settings.set_setting("tts_enabled", "false")

    assert db.get_setting("tts_enabled") == "false"
    assert db.writes == [("tts_enabled", "false")]


def test_settings_overlay_mutates_the_supplied_preferences_dict_in_place():
    db = FakeDatabase()
    preferences = {"play_mode": "shuffle"}
    settings = SessionSettings(db, preferences)

    settings.set_setting("artist_filter", "Example")

    assert settings.overrides is preferences
    assert preferences["artist_filter"] == "Example"
    assert preferences["current_mode"] == "radio"


def test_all_talk_controls_and_numeric_reads_are_session_local():
    from engine.session_settings import SESSION_SETTING_KEYS
    controls = {'talk_segments_enabled', 'talk_caller_enabled', 'talk_caller_mode',
        'talk_monologue_enabled', 'talk_guest_takeover_enabled', 'talk_frequency',
        'talk_guest_songs_per_set', 'talk_segment_cooldown',
        'talk_caller_heartbroken_enabled', 'talk_caller_melancholy_enabled', 'talk_volume'}
    assert controls <= SESSION_SETTING_KEYS
    db = FakeDatabase()
    db.values.update(talk_frequency='4', talk_volume='100')
    first, second = SessionSettings(db), SessionSettings(db)
    first.set_setting('talk_frequency', '2')
    first.set_setting('talk_volume', '150')
    assert first.get_setting_int('talk_frequency', 4) == 2
    assert second.get_setting_int('talk_frequency', 4) == 4
    for invalid in ('-9', '1000000000000000000000', 'bad', 'NaN'):
        first.set_setting('talk_frequency', invalid)
        assert first.get_setting_int('talk_frequency', 4) == 4
    assert db.writes == [] and db.values['talk_volume'] == '100'


def test_queue_passes_explicit_session_talk_settings(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import config
    from engine.queue import QueueManager
    db = FakeDatabase()
    db.values.update(talk_segments_enabled='true', talk_frequency='7', talk_guest_songs_per_set='3')
    talk = SimpleNamespace(_song_counter=0, _current_guest_takeover=None, _recent_callers=[],
        _recent_guests=[], _recent_segment_types=[], get_next_talk_segment=Mock(return_value=None))
    queue = QueueManager.__new__(QueueManager)
    queue._db, queue._talk_context = SessionSettings(db), talk
    queue._pending_talk_segments, queue._current_song_id, queue._current_session_id = [], None, None
    queue._skip_streak, queue._mood = 0, SimpleNamespace(get_state=lambda: 'neutral')
    monkeypatch.setattr(config, 'TTS_ENABLED', True)
    queue._maybe_schedule_talk_segment()
    passed = talk.get_next_talk_segment.call_args.kwargs['settings']
    assert passed['talk_frequency'] == 7 and passed['talk_guest_songs_per_set'] == 3


def test_guest_set_limit_comes_from_passed_settings(monkeypatch):
    from types import SimpleNamespace
    from engine.session_context import new_talk_context
    from db import database
    database.set_setting('talk_guest_songs_per_set', '9')
    talk = new_talk_context()
    talk._song_counter = 1
    talk._talk_debug = lambda *args: None
    talk._pick_segment_type = lambda *args: 'guest_takeover'
    talk._pick_guest = lambda *args: SimpleNamespace(id='guest', name='Guest')
    talk.generate_guest_takeover_intro = lambda *args, **kwargs: SimpleNamespace()
    talk.get_next_talk_segment(settings={'talk_frequency': 1, 'talk_guest_songs_per_set': 2}, increment=False)
    assert talk._current_guest_takeover['songs_remaining'] == 2


def test_main_talk_endpoints_and_nl_toggle_do_not_write_profile(monkeypatch, main_contract):
    import importlib
    import threading
    from types import SimpleNamespace
    from unittest.mock import Mock
    from commands import parser
    main = main_contract
    db = FakeDatabase()
    db.values['talk_segments_enabled'] = 'true'
    first, second = SessionSettings(db), SessionSettings(db)
    player = SimpleNamespace(context=SimpleNamespace(talk=SimpleNamespace(reset_talk_counters=Mock())), get_current_song_id=lambda: None)
    monkeypatch.setattr(main.player_engine, 'get_player', lambda: player)
    monkeypatch.setattr(main, '_session_database', lambda: first)
    main.update_talk_config({'talk_frequency': '8', 'talk_volume': '140'})
    assert main.get_talk_config()['talk_frequency'] == '8'
    monkeypatch.setattr(main, '_get_queue_manager', lambda: SimpleNamespace(_queue=[], _condition=threading.Condition()))
    monkeypatch.setattr(main, '_get_mood_state_machine', lambda: SimpleNamespace(get_state=lambda: 'neutral'))
    monkeypatch.setattr(main.archetype_engine, 'detect_archetype', lambda: ('explorer', {}))
    monkeypatch.setattr(parser, 'parse_command', lambda *args: {'action': 'talk_show', 'enabled': False})
    assert not main.handle_command(main.CommandRequest(text='disable talk'))['enabled']
    assert first.get_setting('talk_segments_enabled') == 'false'
    monkeypatch.setattr(main, '_session_database', lambda: second)
    assert main.get_talk_config()['talk_segments_enabled'] == 'true'
    assert db.writes == []


def test_energy_endpoint_reads_bound_context(monkeypatch, main_contract):
    import importlib
    from types import SimpleNamespace
    from engine.energy import EnergyTrajectory
    main = main_contract
    local = EnergyTrajectory()
    local.record_energy(1, 0.1)
    local.record_energy(2, 0.9)
    monkeypatch.setattr(main.player_engine, 'get_player', lambda: SimpleNamespace(context=SimpleNamespace(energy=local)))
    assert main.energy_trajectory() == {'direction': 'rising', 'current_energy': 0.5, 'recent_energies': [0.1, 0.9]}


def test_observed_logical_start_records_energy_only_once_in_own_context(domain):
    service, sid, owner, *_ = domain
    other = service.create(client_id='other')
    plan, _ = start(service, sid, owner)
    context = service.context_for(sid, owner)
    assert context.energy._recent_energies == [{'song_id': 1, 'energy': 0.5}]
    item = plan['items'][0]
    service.observe(sid, owner, 'duplicate-start', plan['instance_id'], item['item_id'], 'started', client_id='tab-a')
    assert len(context.energy._recent_energies) == 1
    assert service.context_for(other['session']['id'], other['owner_key']).energy._recent_energies == []
