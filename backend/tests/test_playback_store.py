"""Durable playback acceptance must survive retries, stale events and restart."""
from pathlib import Path
import sqlite3
import threading

import pytest

from db import database


@pytest.fixture
def store():
    from db.playback_store import PlaybackStore
    return PlaybackStore()


def ready(store, owner="client-a", song_id=1):
    session = store.create_session(owner, "browser")
    return store.prepare(session["id"], owner, "prepare-1", session["revision"],
                         [{"kind": "song", "song_id": song_id, "duration_ms": 1000}])


def observe(store, state, kind, event_id, owner="client-a", instance=None):
    return store.observe(state["id"], owner, event_id, instance or state["instance_id"],
                         state["items"][0]["item_id"], kind, position_ms=500)


def test_preparing_does_not_record_a_listen_and_sessions_are_independent(store):
    first, second = ready(store), ready(store, "client-b", 2)
    assert first["id"] != second["id"]
    assert first["instance_id"] != second["instance_id"]
    assert database.get_song(1)["play_count"] == 0
    assert database.get_song(2)["play_count"] == 0
    observe(store, first, "started", "start-1")
    assert database.get_song(1)["play_count"] == 1
    assert store.snapshot(second["id"], "client-b")["status"] == "ready"


def test_duplicate_start_and_terminal_events_change_history_once(store):
    state = ready(store)
    observe(store, state, "started", "start-1")
    duplicate = observe(store, state, "started", "start-1")
    assert duplicate["duplicate"] is True
    observe(store, state, "completed", "end-1")
    observe(store, state, "completed", "different-id-same-terminal")
    observe(store, state, "skipped", "late-skip")
    song = database.get_song(1)
    assert (song["play_count"], song["complete_count"], song["skip_count"]) == (1, 1, 0)


def test_stale_instance_cannot_end_the_new_plan(store):
    state = ready(store)
    observe(store, state, "started", "start-1")
    current = store.snapshot(state["id"], "client-a")
    new = store.prepare(state["id"], "client-a", "prepare-2", current["revision"],
                        [{"kind": "song", "song_id": 2, "duration_ms": 1000}])
    result = observe(store, state, "completed", "stale-end")
    assert result["accepted"] is False
    assert result["session"]["instance_id"] == new["instance_id"]
    assert database.get_song(1)["complete_count"] == 0


def test_completion_before_actual_start_is_rejected(store):
    state = ready(store)
    assert observe(store, state, "completed", "prefetch-is-not-completion")["accepted"] is False
    assert database.get_song(1)["complete_count"] == 0


def test_owner_is_required_and_event_identity_cannot_be_reused_for_other_content(store):
    from db.playback_store import ConflictError
    state = ready(store)
    with pytest.raises(PermissionError):
        observe(store, state, "started", "bad-owner", "observer")
    observe(store, state, "started", "same-id")
    with pytest.raises(ConflictError):
        observe(store, state, "completed", "same-id")


def test_receipts_survive_reopening_store(store):
    from db.playback_store import PlaybackStore
    state = ready(store)
    observe(store, state, "started", "start-1")
    observe(store, state, "completed", "end-1")
    reopened = PlaybackStore()
    assert observe(reopened, state, "completed", "end-1")["duplicate"] is True
    assert database.get_song(1)["complete_count"] == 1


def test_speech_only_media_never_enters_song_history(store):
    session = store.create_session("client-a", "browser")
    state = store.prepare(session["id"], "client-a", "speech", 0,
                          [{"kind": "speech", "resource_id": "prepared-clip"}])
    observe(store, state, "started", "speech-start")
    observe(store, state, "completed", "speech-end")
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT count(*) FROM play_events").fetchone()[0] == 0


def test_migration_backup_is_a_restorable_snapshot(store):
    backups = list((Path(database.DB_PATH).parent / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(str(backups[0])) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT count(*) FROM songs").fetchone()[0] == 28
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='playback_sessions'").fetchone()[0] == 0


def test_history_error_rolls_back_receipt_state_and_counter_together(store, monkeypatch):
    state = ready(store)
    original = database.insert_play_event
    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated interrupted transaction")
    monkeypatch.setattr(database, "insert_play_event", fail_after_insert)
    with pytest.raises(RuntimeError):
        observe(store, state, "started", "start-1")
    assert database.get_song(1)["play_count"] == 0
    assert store.snapshot(state["id"], "client-a")["status"] == "ready"
    monkeypatch.setattr(database, "insert_play_event", original)
    assert observe(store, state, "started", "start-1")["accepted"] is True
    assert database.get_song(1)["play_count"] == 1


def test_concurrent_retry_is_accepted_once(store):
    state = ready(store)
    barrier = threading.Barrier(3)
    results, errors = [], []
    def report():
        try:
            barrier.wait(2)
            results.append(observe(store, state, "started", "start-1"))
        except Exception as exc:
            errors.append(exc)
    workers = [threading.Thread(target=report) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(2)
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    assert errors == []
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert database.get_song(1)["play_count"] == 1


def test_next_item_starts_at_zero_after_speech_completion(store):
    session = store.create_session("client-a", "browser")
    state = store.prepare(session["id"], "client-a", "speech-song", 0, [
        {"kind": "speech", "resource_id": "prepared-clip"},
        {"kind": "song", "song_id": 1, "duration_ms": 1000},
    ])
    observe(store, state, "started", "speech-start")
    result = observe(store, state, "completed", "speech-end")
    assert result["session"]["active_index"] == 1
    assert result["session"]["position_ms"] == 0


@pytest.mark.parametrize("duration", [-1, "oops", None])
def test_bad_duration_cannot_create_an_unusable_playback_plan(store, duration):
    session = store.create_session("client-a", "browser")
    with pytest.raises(ValueError):
        store.prepare(session["id"], "client-a", "bad-plan", 0,
                      [{"kind": "song", "song_id": 1, "duration_ms": duration}])


def test_prepare_retry_returns_current_snapshot_with_duplicate_marker(store):
    state = ready(store)
    observe(store, state, "started", "start-1")
    replay = store.prepare(state["id"], "client-a", "prepare-1", 0,
                           [{"kind": "song", "song_id": 1, "duration_ms": 1000}])
    assert replay["duplicate"] is True
    assert replay["status"] == "playing"






def test_restart_retires_old_instance_without_recording_skip_or_completion(store):
    state = ready(store)
    observe(store, state, "started", "start-1")
    assert state["id"] in store.recover_interrupted()
    recovered = store.snapshot(state["id"], "client-a")
    assert recovered["status"] == "interrupted"
    assert recovered["instance_id"] != state["instance_id"]
    assert observe(store, state, "completed", "late-before-restart")["accepted"] is False
    assert (database.get_song(1)["skip_count"], database.get_song(1)["complete_count"]) == (0, 0)


def test_output_handoff_preserves_session_history_attempt_and_persisted_mode(store):
    from db.playback_store import PlaybackStore
    source = ready(store)
    observe(store, source, "started", "start-1")
    current = store.snapshot(source["id"], "client-a")
    moved = store.handoff_output(source["id"], "client-a", "move-1", current["revision"],
                                 "vlc", None, position_ms=420)
    assert moved["id"] == source["id"]
    assert moved["history_session_id"] == source["history_session_id"]
    assert moved["instance_id"] != source["instance_id"]
    assert moved["items"][0]["attempt_id"] == source["items"][0]["attempt_id"]
    assert moved["position_ms"] == 420
    assert moved["status"] == "awaiting_output"
    assert PlaybackStore().snapshot(source["id"], "client-a")["mode"] == "vlc"
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT mode FROM playback_sessions WHERE id=?", (source["id"],)).fetchone()[0] == "vlc"
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert observe(store, source, "completed", "old-instance")["accepted"] is False
    store.output_ready(source["id"], "client-a", moved["instance_id"])
    assert observe(store, moved, "started", "resumed-start")["accepted"] is True
    assert observe(store, moved, "completed", "resumed-end")["accepted"] is True
    assert (database.get_song(1)["play_count"], database.get_song(1)["complete_count"]) == (1, 1)


def test_handoff_retry_is_idempotent_and_rejects_changed_target(store):
    from db.playback_store import ConflictError
    source = ready(store)
    moved = store.handoff_output(source["id"], "client-a", "move-1", source["revision"],
                                 "browser", "screen-b", position_ms=50)
    repeated = store.handoff_output(source["id"], "client-a", "move-1", source["revision"],
                                    "browser", "screen-b", position_ms=999)
    assert repeated["duplicate"] is True
    assert repeated["instance_id"] == moved["instance_id"]
    assert repeated["position_ms"] == 50
    with pytest.raises(ConflictError):
        store.handoff_output(source["id"], "client-a", "move-1", source["revision"],
                             "browser", "screen-c", position_ms=50)


def test_output_client_grant_is_checked_before_replay(store):
    source = ready(store)
    moved = store.handoff_output(source["id"], "client-a", "move-1", source["revision"],
                                 "browser", "screen-b", position_ms=0)
    store.output_ready(source["id"], "client-a", moved["instance_id"])
    arguments = (source["id"], "client-a", "start-on-b", moved["instance_id"], moved["items"][0]["item_id"], "started")
    assert store.observe(*arguments, client_id="screen-b")["accepted"] is True
    assert store.observe(*arguments, client_id="screen-old")["accepted"] is False
    assert store.observe(*arguments)["accepted"] is False
    assert store.observe(*arguments, client_id="screen-b")["duplicate"] is True


def test_retired_instance_receipt_cannot_authorize_replayed_start_on_vlc(store):
    source = ready(store)
    assert observe(store, source, "started", "old-start")["accepted"] is True
    current = store.snapshot(source["id"], "client-a")
    moved = store.handoff_output(source["id"], "client-a", "move-1", current["revision"],
                                 "vlc", None, position_ms=500)
    store.output_ready(source["id"], "client-a", moved["instance_id"])
    assert observe(store, source, "started", "old-start")["accepted"] is False


def test_physical_restart_after_handoff_is_not_another_logical_song_start(store):
    source = ready(store)
    assert observe(store, source, "started", "old-start")["logical_start"] is True
    current = store.snapshot(source["id"], "client-a")
    moved = store.handoff_output(source["id"], "client-a", "move-1", current["revision"],
                                 "vlc", None, position_ms=500)
    store.output_ready(source["id"], "client-a", moved["instance_id"])
    resumed = observe(store, moved, "started", "resumed-start")
    assert resumed["accepted"] is True
    assert resumed["logical_start"] is False
    assert database.get_song(1)["play_count"] == 1


def test_context_save_is_defensive_and_does_not_overwrite_current_playback(store):
    state = ready(store)
    first = store.save_context(state["id"], "client-a", {"mood": "chill", "queue": [2, 3]})
    assert first["revision"] == state["revision"]
    assert first["context_revision"] == 1
    observe(store, state, "started", "start-1")
    context = {"mood": "focus", "queue": [3, 2]}
    updated = store.save_context(state["id"], "client-a", context)
    context["queue"].clear()
    assert updated["context"] == {"mood": "focus", "queue": [3, 2]}
    assert updated["context_revision"] == 2
    assert updated["revision"] == state["revision"] + 1
    assert updated["status"] == "playing"
    assert database.get_song(1)["play_count"] == 1


@pytest.mark.parametrize("context", [
    {"text": "x" * 1048576}, {"mood": float("nan")}, {"nested": {"api_key": "credential"}},
    {"nested": {"owner": "capability"}}, ["not-a-context-object"],
])
def test_context_rejects_oversized_nonfinite_or_credential_payloads(store, context):
    state = ready(store)
    with pytest.raises(ValueError):
        store.save_context(state["id"], "client-a", context)
    assert "context" not in store.snapshot(state["id"], "client-a")


def test_context_rejects_deep_payload_without_changing_state(store):
    state = ready(store)
    context = {}
    cursor = context
    for _ in range(20):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    with pytest.raises(ValueError):
        store.save_context(state["id"], "client-a", context)


def test_position_checkpoints_are_monotonic_and_do_not_create_commands_or_history(store):
    state = ready(store)
    started = observe(store, state, "started", "start-1")["session"]
    arguments = (state["id"], "client-a", state["instance_id"], state["items"][0]["item_id"], None)
    accepted = store.checkpoint_position(*arguments, sequence=2, position_ms=600)
    assert accepted["accepted"] is True
    assert accepted["session"]["revision"] == started["revision"]
    assert accepted["session"]["position_ms"] == 600
    assert accepted["session"]["position_seq"] == 2
    assert store.checkpoint_position(*arguments, sequence=1, position_ms=10)["accepted"] is False
    assert store.checkpoint_position(*arguments, sequence=2, position_ms=20)["accepted"] is False
    assert store.snapshot(state["id"], "client-a")["position_ms"] == 600
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT count(*) FROM playback_receipts").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM play_events").fetchone()[0] == 1


def test_position_checkpoint_requires_current_started_item_and_grant(store):
    state = ready(store)
    moved = store.handoff_output(state["id"], "client-a", "move-1", state["revision"],
                                 "browser", "screen-b", position_ms=0)
    item = moved["items"][0]["item_id"]
    args = (state["id"], "client-a", moved["instance_id"], item)
    assert store.checkpoint_position(*args, "screen-b", sequence=1, position_ms=10)["accepted"] is False
    store.output_ready(state["id"], "client-a", moved["instance_id"])
    store.observe(state["id"], "client-a", "target-start", moved["instance_id"], item, "started", client_id="screen-b")
    for instance, item_id, client in ((state["instance_id"], item, "screen-b"),
                                     (moved["instance_id"], "wrong-item", "screen-b"),
                                     (moved["instance_id"], item, "old-client")):
        assert store.checkpoint_position(state["id"], "client-a", instance, item_id, client,
                                          sequence=1, position_ms=10)["accepted"] is False
    assert store.checkpoint_position(*args, "screen-b", sequence=1, position_ms=50)["accepted"] is True


@pytest.mark.parametrize("sequence,position", [(True, 0), (-1, 0), (1, -1), (1, float("inf"))])
def test_bad_checkpoint_values_cannot_mutate_state(store, sequence, position):
    state = ready(store)
    with pytest.raises(ValueError):
        store.checkpoint_position(state["id"], "client-a", state["instance_id"],
                                  state["items"][0]["item_id"], None, sequence, position)


def test_reactivation_preserves_attempt_context_and_position_with_fresh_identity(store):
    state = ready(store)
    observe(store, state, "started", "start-1")
    store.save_context(state["id"], "client-a", {"mood": "chill"})
    store.checkpoint_position(state["id"], "client-a", state["instance_id"],
                              state["items"][0]["item_id"], None, 5, 500)
    store.recover_interrupted()
    interrupted = store.snapshot(state["id"], "client-a")
    resumed = store.reactivate(state["id"], "client-a", "reactivate-1", interrupted["revision"], "screen-new")
    assert resumed["status"] == "ready"
    assert resumed["instance_id"] != interrupted["instance_id"]
    assert resumed["history_session_id"] == state["history_session_id"]
    assert resumed["items"][0]["attempt_id"] == state["items"][0]["attempt_id"]
    assert resumed["items"][0]["started"] is False
    assert resumed["context"] == {"mood": "chill"}
    assert resumed["position_ms"] == 500 and resumed["position_seq"] == -1
    assert resumed["output_client_id"] == "screen-new"
    duplicate = store.reactivate(state["id"], "client-a", "reactivate-1", interrupted["revision"], "screen-new")
    assert duplicate["duplicate"] is True and duplicate["instance_id"] == resumed["instance_id"]
    assert database.get_song(1)["play_count"] == 1


def test_reactivation_requires_explicitly_recoverable_state(store):
    from db.playback_store import ConflictError
    state = ready(store)
    with pytest.raises(ConflictError):
        store.reactivate(state["id"], "client-a", "bad-resume", state["revision"], "screen-new")


def test_prepare_and_command_snapshot_match_explicit_intent(store):
    from db.playback_store import ConflictError
    state = store.create_session("client-a", "browser")
    intent = {"operation": "select", "song_id": 1, "type": "song"}
    items = [{"kind": "song", "song_id": 1, "duration_ms": 1000}]
    selected = store.prepare(state["id"], "client-a", "select-1", 0, items, intent=intent)
    assert store.command_snapshot(state["id"], "client-a", "select-1", "prepare", 0, intent=intent)["duplicate"] is True
    with pytest.raises(ConflictError):
        store.command_snapshot(state["id"], "client-a", "select-1", "prepare", 0, intent={"operation": "advance"})
    assert store.prepare(state["id"], "client-a", "select-1", 0, items, intent=intent)["instance_id"] == selected["instance_id"]


def test_closed_history_can_restart_without_replacing_playback_context_or_old_attempts(store):
    from db.playback_store import PlaybackStore
    state = ready(store)
    old_history = state["history_session_id"]
    started = observe(store, state, "started", "old-start")["session"]
    stopped = store.stop(state["id"], "client-a", "stop-1", started["revision"])
    store.save_context(state["id"], "client-a", {"mood": "focus", "queue": [2, 3]})
    database.close_session(old_history)
    renewed = store.begin_history_session(state["id"], "client-a")
    new_history = renewed["history_session_id"]
    assert new_history != old_history
    assert renewed["id"] == state["id"] and renewed["revision"] == stopped["revision"]
    assert renewed["context"] == {"mood": "focus", "queue": [2, 3]}
    assert PlaybackStore().snapshot(state["id"], "client-a")["history_session_id"] == new_history
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT history_session_id FROM playback_sessions WHERE id=?", (state["id"],)).fetchone()[0] == new_history
        assert conn.execute("SELECT history_session_id FROM playback_attempts WHERE id=?", (state["items"][0]["attempt_id"],)).fetchone()[0] == old_history
        assert conn.execute("SELECT session_id FROM play_events WHERE song_id=1").fetchone()[0] == old_history
    new = store.prepare(state["id"], "client-a", "new-plan", renewed["revision"],
                         [{"kind": "song", "song_id": 2, "duration_ms": 1000}])
    store.observe(state["id"], "client-a", "new-start", new["instance_id"], new["items"][0]["item_id"], "started")
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT history_session_id FROM playback_attempts WHERE id=?", (new["items"][0]["attempt_id"],)).fetchone()[0] == new_history
        assert conn.execute("SELECT session_id FROM play_events WHERE song_id=2").fetchone()[0] == new_history
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
    assert database.get_song(1)["play_count"] == 1


def test_open_history_is_reused_without_changing_revisions_or_creating_rows(store):
    state = ready(store)
    reused = store.begin_history_session(state["id"], "client-a")
    assert reused["history_session_id"] == state["history_session_id"]
    assert reused["revision"] == state["revision"]
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


def test_active_plan_cannot_switch_away_from_its_closed_history(store):
    from db.playback_store import ConflictError
    state = ready(store)
    database.close_session(state["history_session_id"])
    with pytest.raises(ConflictError):
        store.begin_history_session(state["id"], "client-a")
    assert store.snapshot(state["id"], "client-a")["history_session_id"] == state["history_session_id"]
