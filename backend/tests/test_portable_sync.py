"""Manual profile synchronization uses disposable storage, never playback sessions."""
import pytest

from db import database
from portable_sync import PortableProfileStore, SyncConflict


def event(number=1, kind="song_started", **changes):
    return {"protocol_version": 1, "event_id": f"e{number}", "device_id": "phone",
            "catalog_id": PortableProfileStore().catalog_id,
            "logical_clock": number, "kind": kind, "song_id": "1",
            "attempt_id": "attempt-a", "occurred_at": 1000.0, **changes}


def test_retry_out_of_order_projection_and_no_session_transfer():
    store = PortableProfileStore()
    completed = event(2, "song_completed")
    started = event()
    store.import_events("phone", [completed, started])
    store.import_events("phone", [started, completed])
    with database.get_connection_cm() as conn:
        row = conn.execute("SELECT play_count,complete_count FROM songs WHERE id=1").fetchone()
        assert tuple(row) == (1, 1)
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    result = store.events(0, 100)
    assert len(result["events"]) == 2
    assert result["cursor"] == 2
    assert store.events(result["cursor"], 100)["events"] == []


def test_conflicting_retry_and_spoofed_origin_roll_back_whole_batch():
    store = PortableProfileStore()
    store.import_events("phone", [event()])
    with pytest.raises(SyncConflict):
        store.import_events("phone", [event(kind="song_skipped")])
    with pytest.raises(ValueError):
        store.import_events("phone", [event(2), event(3, device_id="other")])
    assert len(store.events(0, 100)["events"]) == 1


def test_mutable_state_converges_without_using_device_wall_clock():
    store = PortableProfileStore()
    newer = event(9, "favorite_changed", value=False, occurred_at=1)
    older = event(8, "favorite_changed", value=True, occurred_at=9999999999)
    store.import_events("phone", [newer, older])
    assert store.profile()["favorites"]["1"] is False
    store.import_events("tablet", [event(9, "favorite_changed", device_id="tablet", value=True)])
    assert store.profile()["favorites"]["1"] is True


def test_attempt_start_is_once_and_prefetch_never_a_play():
    store = PortableProfileStore()
    store.import_events("phone", [event(), event(2)])
    assert database.get_song(1)["play_count"] == 1
    with pytest.raises(ValueError):
        store.import_events("phone", [event(3, "preloaded")])


def test_unknown_local_only_songs_remain_events_without_guessed_mapping():
    store = PortableProfileStore()
    store.import_events("phone", [event(song_id=None, local_id="local-only")])
    assert len(store.events(0, 10)["events"]) == 1
    assert database.get_song(1)["play_count"] == 0


def test_device_tokens_are_hashed_scoped_persistent_and_revocable():
    store = PortableProfileStore()
    device = store.issue_device("Phone", ["catalog:read"])
    assert store.authorize(device["token"])["scopes"] == ["catalog:read"]
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT token_hash FROM portable_devices").fetchone()[0] != device["token"]
    assert PortableProfileStore().authorize(device["token"])
    store.revoke_device(device["device_id"])
    assert store.authorize(device["token"]) is None


def test_desktop_history_is_exported_once_without_echoing_imported_plays():
    database.record_play_event(1, None, "playing")
    store = PortableProfileStore()
    store.import_events("phone", [event()])
    store.capture_desktop()
    first = store.events(0, 100)["events"]
    store.capture_desktop()
    assert store.events(0, 100)["events"] == first
    assert len([row for row in first if row["event"]["kind"] == "song_started"]) == 2


def test_profile_projects_blacklist_energy_and_future_session_preference():
    store = PortableProfileStore()
    store.import_events("phone", [event(1, "blacklist_changed", value=True),
        event(2, "energy_changed", value=0.9),
        event(3, "preference_changed", song_id=None, key="exploration_level", value=0.7)])
    assert database.get_song(1)["user_energy"] == 0.9
    assert database.get_setting_float("exploration_level") == 0.7
    assert database.get_blacklisted()[0]["song_id"] == 1


def test_local_edit_back_to_original_value_after_import_is_not_lost():
    store = PortableProfileStore()
    store.capture_desktop()  # original favorite false
    store.import_events("phone", [event(10, "favorite_changed", value=True)])
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE play_events SET undone=1 WHERE song_id=1 AND signal='hearted'")
        conn.commit()
    store.capture_desktop()
    assert store.profile()["favorites"]["1"] is False
    last = store.events(0, 100)["events"][-1]["event"]
    assert last["device_id"] == "server" and last["logical_clock"] > 10


def test_wrong_source_catalog_is_rejected_before_song_projection():
    store = PortableProfileStore()
    with pytest.raises(ValueError, match="source catalog"):
        store.import_events("phone", [event(catalog_id="another-library")])
    assert database.get_song(1)["play_count"] == 0


def test_imported_positive_feedback_changes_cached_active_desktop_taste(monkeypatch):
    from engine.taste import MemorySystem
    memory = MemorySystem()
    monkeypatch.setattr(memory, "_batch_get_chroma_embeddings", lambda ids, space: {str(sid): [1., 0.] if int(sid) == 1 else [0., 1.] for sid in ids})
    with database.get_connection_cm() as conn:
        session_id = conn.execute("INSERT INTO sessions DEFAULT VALUES").lastrowid
        conn.commit()
    database.record_play_event(2, session_id, "completed")
    first, _ = memory.get_blended_taste_vector(session_id)
    assert first == [0., 1.]
    store = PortableProfileStore()
    store.import_events("phone", [event(10, "favorite_changed", value=True)])
    changed, _ = memory.get_blended_taste_vector(session_id)
    assert changed[0] > 0 and changed != first
    assert len(database.get_recent_signals(["completed", "hearted"], session_id=session_id)) == 1


def test_skip_undone_before_first_sync_is_not_exported_as_active_feedback():
    database.record_play_event(1, None, "skip_early")
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE play_events SET undone=1")
        conn.commit()
    store = PortableProfileStore()
    store.capture_desktop()
    assert not any(row["event"]["kind"] == "song_skipped" for row in store.events()["events"])


def test_catalog_sized_steady_sync_capture_does_not_rewrite_unchanged_profile():
    from time import perf_counter
    with database.get_connection_cm() as conn:
        conn.executemany("INSERT INTO songs(id,filepath,file_hash,title) VALUES (?,?,?,?)",
                         [(i, f"synthetic/{i}.wav", f"synthetic-{i}", f"Song {i}") for i in range(29, 2301)])
        conn.commit()
    store = PortableProfileStore()
    start = perf_counter()
    store.capture_desktop()
    initial_ms = (perf_counter() - start) * 1000
    with database.get_connection_cm() as observer:
        before = observer.execute("PRAGMA data_version").fetchone()[0]
        start = perf_counter()
        store.capture_desktop()
        steady_ms = (perf_counter() - start) * 1000
        assert observer.execute("PRAGMA data_version").fetchone()[0] == before
    print(f"portable_capture_2300 initial_ms={initial_ms:.2f} steady_ms={steady_ms:.2f}")


def test_local_file_ids_from_different_devices_cannot_overwrite_each_other():
    store = PortableProfileStore()
    store.import_events("phone", [event(1, "favorite_changed", song_id=None, local_id="42", value=True)])
    store.import_events("tablet", [event(2, "favorite_changed", device_id="tablet", song_id=None, local_id="42", value=False)])
    favorites = store.profile()["favorites"]
    assert favorites['local:["phone","42"]'] is True
    assert favorites['local:["tablet","42"]'] is False


def test_removed_catalog_song_does_not_block_unrelated_or_unique_offline_events():
    store = PortableProfileStore()
    with database.get_connection_cm() as conn:
        conn.execute("DELETE FROM songs WHERE id=1")
        conn.commit()
    store.import_events("phone", [event(), event(2, song_id="2", attempt_id="another")])
    assert len(store.events()["events"]) == 2
    assert database.get_song(2)["play_count"] == 1
