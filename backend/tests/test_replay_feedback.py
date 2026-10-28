from db import database
from db.playback_store import PlaybackStore


def test_replay_intent_is_once_and_new_play_count_waits_for_output():
    store = PlaybackStore()
    state = store.create_session('owner', 'browser', output_client_id='tab')
    sid = state['id']
    items = [{'kind': 'song', 'song_id': 1, 'duration_ms': 1000, 'url': '/api/media/tracks/1/stream'}]
    first = store.prepare(sid, 'owner', 'first', 0, items)
    item = first['items'][0]['item_id']
    started = store.observe(sid, 'owner', 'start', first['instance_id'], item, 'started', client_id='tab')
    replay = store.observe(sid, 'owner', 'replay', first['instance_id'], item, 'replayed', client_id='tab')
    assert replay['accepted']
    again = store.observe(sid, 'owner', 'another-click', first['instance_id'], item, 'replayed', client_id='tab')
    assert not again['accepted']
    song = database.get_song(1)
    assert song['play_count'] == 1 and song['replay_count'] == 1
    second = store.prepare(sid, 'owner', 'second', again['session']['revision'], items)
    assert database.get_song(1)['play_count'] == 1
    store.observe(sid, 'owner', 'start-second', second['instance_id'], second['items'][0]['item_id'], 'started', client_id='tab')
    assert database.get_song(1)['play_count'] == 2


def test_duplicate_physical_resume_is_acknowledged_without_new_transition():
    store = PlaybackStore()
    state = store.create_session('owner', 'browser', output_client_id='tab')
    sid = state['id']
    plan = store.prepare(sid, 'owner', 'plan', 0, [{'kind': 'song', 'song_id': 1, 'duration_ms': 1000}])
    args = (sid, 'owner')
    item, instance = plan['items'][0]['item_id'], plan['instance_id']
    started = store.observe(*args, 'started', instance, item, 'started', client_id='tab')
    resumed = store.observe(*args, 'physical-playing-again', instance, item, 'resumed', client_id='tab')
    assert resumed['accepted'] and resumed['duplicate']
    assert resumed['session']['revision'] == started['session']['revision']
    assert database.get_song(1)['play_count'] == 1


def test_explicit_new_selection_clears_previous_paused_handoff_intent():
    store = PlaybackStore()
    state = store.create_session('owner', 'browser', output_client_id='tab')
    sid = state['id']
    items = [{'kind': 'song', 'song_id': 1, 'duration_ms': 1000}]
    plan = store.prepare(sid, 'owner', 'plan', 0, items)
    store.observe(sid, 'owner', 'start', plan['instance_id'], plan['items'][0]['item_id'], 'started', client_id='tab')
    current = store.snapshot(sid, 'owner')
    moved = store.handoff_output(sid, 'owner', 'paused-transfer', current['revision'], 'vlc', None,
                                position_ms=25, desired_status='paused')
    selected = store.prepare(sid, 'owner', 'new-selection', moved['revision'], items)
    assert selected.get('desired_status') == 'playing'
