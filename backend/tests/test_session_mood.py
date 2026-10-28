from db import database
from engine.mood import MoodStateMachine


def test_independent_browser_vibe_locks_do_not_read_or_write_desktop_state():
    before = {key: database.get_setting(key) for key in (
        "vibe_lock_active", "vibe_lock_song_id", "vibe_lock_expires")}
    first = MoodStateMachine(persist_state=False)
    second = MoodStateMachine(persist_state=False)
    first.set_state("chill")
    first.activate_vibe_lock(1, [1.0, 0.0])
    assert first.is_vibe_lock_active()
    assert not second.is_vibe_lock_active()
    assert second.get_state() == "neutral"
    assert {key: database.get_setting(key) for key in before} == before
