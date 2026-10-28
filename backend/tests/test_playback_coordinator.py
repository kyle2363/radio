from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from db.playback_store import PlaybackStore, ConflictError


class Queue:
    def __init__(self):
        self.items = [{"song_id": 1}, {"song_id": 2}]
        self.claims = []
        self.commits = []
        self.releases = []
    def reserve_next(self):
        held = {id(handle.item) for handle in self.claims}
        item = next((item for item in self.items if id(item) not in held), None)
        if item is None:
            return None
        handle = SimpleNamespace(item=item)
        self.claims.append(handle)
        return handle
    def commit_reservation(self, handle):
        if handle not in self.claims:
            return None
        self.claims.remove(handle)
        self.items.remove(handle.item)
        self.commits.append(handle.item)
        return handle.item
    def release_reservation(self, handle):
        if handle in self.claims:
            self.claims.remove(handle)
            self.releases.append(handle.item)
    def request_rebuild(self):
        pass
    def shutdown(self):
        self.claims.clear()


@pytest.fixture
def setup():
    from engine.playback_coordinator import PlaybackCoordinator
    store = PlaybackStore()
    planner = lambda item, state: [{"kind": "song", "song_id": item["song_id"], "duration_ms": 1000}]
    coordinator = PlaybackCoordinator(store, planner)
    queue, transport = Queue(), Mock()
    transport.start.return_value = True
    state = coordinator.create_session("client-a", "browser", queue, transport)
    return coordinator, store, state, queue, transport


def test_plan_preparation_and_preload_do_not_consume_or_record_playback(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    assert len(queue.items) == 2
    assert queue.commits == []
    transport.start.assert_called_once()
    item = plan["items"][0]
    result = coordinator.observe(state["id"], "client-a", "start-1", plan["instance_id"], item["item_id"], "started")
    assert result["accepted"] is True
    assert queue.commits == [{"song_id": 1}]


def test_stop_during_slow_preparation_fences_late_audio_and_releases_claim(setup):
    coordinator, store, state, queue, transport = setup
    entered, release = threading.Event(), threading.Event()
    original = coordinator.planner
    def slow(item, snapshot):
        entered.set()
        assert release.wait(2)
        return original(item, snapshot)
    coordinator.planner = slow
    results = []
    worker = threading.Thread(target=lambda: results.append(coordinator.advance(state["id"], "client-a", "next-1", 0)))
    worker.start()
    try:
        assert entered.wait(1)
        coordinator.stop(state["id"], "client-a", "stop-1", 0)
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    transport.start.assert_not_called()
    assert len(queue.items) == 2
    assert queue.claims == []
    assert store.snapshot(state["id"], "client-a")["status"] == "stopped"


def test_retry_of_advance_does_not_start_or_reserve_another_track(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    repeated = coordinator.advance(state["id"], "client-a", "next-1", 0)
    assert repeated["duplicate"] is True
    assert repeated["instance_id"] == plan["instance_id"]
    assert len(queue.claims) == 1
    transport.start.assert_called_once()


def test_old_stop_retry_cannot_stop_newer_playback(setup):
    coordinator, store, state, queue, transport = setup
    stopped = coordinator.stop(state["id"], "client-a", "stop-1", 0)
    coordinator.advance(state["id"], "client-a", "next-1", stopped["revision"])
    coordinator.stop(state["id"], "client-a", "stop-1", 0)
    transport.stop.assert_called_once()


def test_independent_sessions_never_consume_each_others_queues(setup):
    coordinator, store, state, queue, transport = setup
    other_queue, other_transport = Queue(), Mock()
    other = coordinator.create_session("client-b", "browser", other_queue, other_transport)
    coordinator.advance(state["id"], "client-a", "next-1", 0)
    assert other_queue.claims == []
    other_transport.start.assert_not_called()
    assert store.snapshot(other["id"], "client-b")["status"] == "idle"


def test_shutdown_closes_admission_even_for_inflight_prepare(setup):
    coordinator, store, state, queue, transport = setup
    coordinator.shutdown()
    with pytest.raises(ConflictError):
        coordinator.advance(state["id"], "client-a", "next-1", 0)
    transport.start.assert_not_called()


def test_wrong_item_start_cannot_consume_the_reserved_pick(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    result = coordinator.observe(state["id"], "client-a", "wrong-item", plan["instance_id"], "unrelated-item", "started")
    assert result["accepted"] is False
    assert queue.commits == []
    assert len(queue.claims) == 1


def test_optional_feedback_hook_failure_does_not_reject_committed_playback(setup):
    coordinator, store, state, queue, transport = setup
    coordinator.feedback = Mock(side_effect=RuntimeError("optional model unavailable"))
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    result = coordinator.observe(state["id"], "client-a", "start-1", plan["instance_id"], plan["items"][0]["item_id"], "started")
    assert result["accepted"] is True


def test_advance_requires_terminal_or_idle_plan(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    with pytest.raises(ConflictError):
        coordinator.advance(state["id"], "client-a", "next-2", plan["revision"])
    assert len(queue.claims) == 1






def test_invalid_position_does_not_consume_reservation(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    with pytest.raises(ValueError):
        coordinator.observe(state["id"], "client-a", "start-1", plan["instance_id"],
                            plan["items"][0]["item_id"], "started", position_ms=-1)
    assert queue.commits == []
    assert len(queue.claims) == 1


def test_stopping_during_intro_does_not_consume_unplayed_song(setup):
    coordinator, store, state, queue, transport = setup
    coordinator.planner = lambda item, snapshot: [
        {"kind": "speech", "resource_id": "intro"},
        {"kind": "song", "song_id": item["song_id"], "duration_ms": 1000},
    ]
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    intro = coordinator.observe(state["id"], "client-a", "intro-start", plan["instance_id"], plan["items"][0]["item_id"], "started")
    assert queue.commits == []
    coordinator.stop(state["id"], "client-a", "stop-1", intro["session"]["revision"])
    assert len(queue.items) == 2
    assert queue.claims == []


@pytest.mark.parametrize("failure", [False, RuntimeError("output unavailable")])
def test_transport_failure_leaves_a_retryable_session_and_unconsumed_pick(setup, failure):
    coordinator, store, state, queue, transport = setup
    if failure is False:
        transport.start.return_value = False
    else:
        transport.start.side_effect = failure
    with pytest.raises(RuntimeError):
        coordinator.advance(state["id"], "client-a", "next-1", 0)
    failed = store.snapshot(state["id"], "client-a")
    assert failed["status"] in ("stopped", "failed")
    assert len(queue.items) == 2
    transport.start.side_effect = None
    transport.start.return_value = True
    assert coordinator.advance(state["id"], "client-a", "retry-next", failed["revision"])["status"] == "ready"




def test_retry_reconciles_queue_commit_without_duplicate_listen(setup, monkeypatch):
    from db import database
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    original = queue.commit_reservation
    monkeypatch.setattr(queue, "commit_reservation", Mock(side_effect=RuntimeError("temporary queue interruption")))
    first = coordinator.observe(state["id"], "client-a", "start-1", plan["instance_id"], plan["items"][0]["item_id"], "started")
    assert first["accepted"] is True
    assert first["queue_commit_pending"] is True
    monkeypatch.setattr(queue, "commit_reservation", original)
    second = coordinator.observe(state["id"], "client-a", "start-1", plan["instance_id"], plan["items"][0]["item_id"], "started")
    assert second["duplicate"] is True
    assert queue.commits == [{"song_id": 1}]
    assert database.get_song(1)["play_count"] == 1


def confirmed(transport, plan, command="move-1", position=420):
    transport.quiesce.return_value = {"confirmed": True, "position_ms": position,
                                     "instance_id": plan["instance_id"], "command_id": command}


def test_handoff_waits_for_correlated_quiescence_without_blocking(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    target = Mock()
    transport.quiesce.return_value = {"confirmed": False}
    pending = coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                         "vlc", None, target_transport=target)
    assert pending["awaiting_confirmation"] is True
    assert pending["id"] == state["id"]
    assert len(queue.claims) == 1
    target.start.assert_not_called()
    transport.quiesce.assert_called_once_with(plan["instance_id"], command_id="move-1")
    confirmed(transport, plan, command="stale-command")
    assert coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                      "vlc", None, target_transport=target)["awaiting_confirmation"] is True
    confirmed(transport, plan)
    moved = coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                       "vlc", None, target_transport=target)
    assert moved["status"] == "ready"
    assert moved["position_ms"] == 420
    assert coordinator._runtimes[state["id"]].queue is queue


def test_whole_session_handoff_preserves_context_claim_and_other_sessions(setup):
    coordinator, store, state, queue, transport = setup
    runtime = coordinator._runtimes[state["id"]]
    runtime.port = Mock()
    queue.mood, queue.energy, queue.guest = object(), object(), object()
    contexts = (queue.mood, queue.energy, queue.guest)
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    claim = queue.claims[0]
    other_queue, other_transport = Queue(), Mock()
    other = coordinator.create_session("client-other", "browser", other_queue, other_transport)
    target = Mock()
    target.start.return_value = True
    confirmed(transport, plan)
    moved = coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                       "browser", "screen-b", target_transport=target)
    assert moved["id"] == state["id"]
    assert moved["history_session_id"] == state["history_session_id"]
    assert moved["items"][0]["attempt_id"] == plan["items"][0]["attempt_id"]
    assert runtime.queue is queue and queue.claims == [claim]
    assert (queue.mood, queue.energy, queue.guest) == contexts
    runtime.port.bind_output.assert_called_once_with(target, instance_id=moved["instance_id"])
    assert store.snapshot(other["id"], "client-other")["status"] == "idle"
    other_transport.start.assert_not_called()
    result = coordinator.observe(state["id"], "client-a", "destination-start", moved["instance_id"],
                                 moved["items"][0]["item_id"], "started", client_id="screen-b")
    assert result["accepted"] is True
    assert queue.commits == [{"song_id": 1}]
    assert other_queue.commits == []


@pytest.mark.parametrize("failure", [False, RuntimeError("target unplugged")])
def test_failed_target_start_retains_same_plan_and_retry_activates_once(setup, failure):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    target = Mock()
    if failure is False:
        target.start.return_value = False
    else:
        target.start.side_effect = failure
    confirmed(transport, plan)
    failed = coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                        "browser", "screen-b", target_transport=target)
    assert failed["status"] == "awaiting_output"
    assert failed["id"] == state["id"]
    assert failed["items"][0]["attempt_id"] == plan["items"][0]["attempt_id"]
    assert len(queue.claims) == 1 and queue.releases == []
    target.start.side_effect = None
    target.start.return_value = True
    resumed = coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                         "browser", "screen-b", target_transport=target)
    assert resumed["status"] == "ready"
    assert resumed["instance_id"] == failed["instance_id"]
    transport.quiesce.assert_called_once()
    coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                "browser", "screen-b", target_transport=target)
    assert target.start.call_count == 2


def test_unavailable_target_rejected_before_source_pause(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    with pytest.raises(ConflictError):
        coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                    "vlc", None, target_transport=None)
    transport.quiesce.assert_not_called()


def test_old_instance_and_old_client_disconnect_cannot_control_handed_output(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    target = Mock()
    confirmed(transport, plan)
    moved = coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                       "browser", "screen-b", target_transport=target)
    for instance, client in ((plan["instance_id"], "screen-b"), (moved["instance_id"], "screen-old")):
        result = coordinator.observe(state["id"], "client-a", "disconnect-" + client, instance,
                                     moved["items"][0]["item_id"], "interrupted", client_id=client)
        assert result["accepted"] is False
    assert queue.commits == []
    assert store.snapshot(state["id"], "client-a")["status"] == "ready"
    target.stop.assert_not_called()


def test_pending_handoff_command_cannot_be_reused_for_different_target(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    target = Mock()
    transport.quiesce.return_value = {"confirmed": False}
    coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                "browser", "screen-b", target_transport=target)
    confirmed(transport, plan)
    with pytest.raises(ConflictError):
        coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                    "browser", "screen-c", target_transport=target)
    target.start.assert_not_called()


def test_delayed_pause_observation_does_not_make_handoff_command_unretryable(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    started = coordinator.observe(state["id"], "client-a", "start-1", plan["instance_id"],
                                   plan["items"][0]["item_id"], "started")["session"]
    target = Mock()
    transport.quiesce.return_value = {"confirmed": False}
    coordinator.handoff_output(state["id"], "client-a", "move-1", started["revision"],
                                "browser", "screen-b", target_transport=target)
    coordinator.observe(state["id"], "client-a", "source-paused", plan["instance_id"],
                         plan["items"][0]["item_id"], "paused", position_ms=420)
    confirmed(transport, plan)
    moved = coordinator.handoff_output(state["id"], "client-a", "move-1", started["revision"],
                                       "browser", "screen-b", target_transport=target)
    assert moved["status"] == "ready" and moved["position_ms"] == 420


def test_target_can_report_actual_start_synchronously_while_handoff_starts_output(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    target = Mock()
    confirmations = []
    def start(moved):
        confirmations.append(coordinator.observe(state["id"], "client-a", "target-start", moved["instance_id"],
                                                   moved["items"][0]["item_id"], "started", client_id="screen-b"))
        return True
    target.start.side_effect = start
    confirmed(transport, plan)
    moved = coordinator.handoff_output(state["id"], "client-a", "move-1", plan["revision"],
                                       "browser", "screen-b", target_transport=target)
    assert confirmations[0]["accepted"] is True
    assert confirmations[0]["logical_start"] is True
    assert moved["status"] == "playing"
    assert queue.commits == [{"song_id": 1}]


def test_attach_after_restart_reconstructs_runtime_without_starting_output(setup):
    from engine.playback_coordinator import PlaybackCoordinator
    coordinator, store, state, queue, transport = setup
    coordinator.advance(state["id"], "client-a", "next-1", 0)
    store.recover_interrupted()
    restored = PlaybackCoordinator(store, coordinator.planner)
    restored_queue, restored_output, port = Queue(), Mock(), Mock()
    attached = restored.attach_session(state["id"], "client-a", restored_queue, restored_output, port=port)
    runtime = restored._runtimes[state["id"]]
    assert attached["status"] == "interrupted"
    assert runtime.queue is restored_queue and runtime.transport is restored_output and runtime.port is port
    assert runtime.instance_id == attached["instance_id"]
    restored_output.start.assert_not_called()
    port.bind_output.assert_not_called()
    with pytest.raises(ConflictError):
        restored.attach_session(state["id"], "client-a", Queue(), Mock())
    with pytest.raises(PermissionError):
        PlaybackCoordinator(store, coordinator.planner).attach_session(state["id"], "intruder", Queue(), Mock())


def test_explicit_selection_uses_shared_planner_without_queue_consumption(setup):
    from db import database
    coordinator, store, state, queue, transport = setup
    original = coordinator.planner
    coordinator.planner = Mock(side_effect=original)
    selected = coordinator.select(state["id"], "client-a", "select-1", 0, {"song_id": 5})
    coordinator.planner.assert_called_once()
    assert selected["items"][0]["song_id"] == 5
    assert queue.claims == [] and queue.commits == [] and len(queue.items) == 2
    assert database.get_song(5)["play_count"] == 0
    result = coordinator.observe(state["id"], "client-a", "start-5", selected["instance_id"],
                                 selected["items"][0]["item_id"], "started")
    assert result["logical_start"] is True and database.get_song(5)["play_count"] == 1
    assert queue.commits == []


def test_selection_replaces_plan_releases_old_claim_and_fences_old_observations(setup):
    coordinator, store, state, queue, transport = setup
    old = coordinator.advance(state["id"], "client-a", "next-1", 0)
    selected = coordinator.select(state["id"], "client-a", "select-1", old["revision"], {"song_id": 5})
    assert selected["instance_id"] != old["instance_id"]
    assert queue.claims == [] and queue.releases == [{"song_id": 1}]
    assert coordinator.observe(state["id"], "client-a", "old-start", old["instance_id"],
                                 old["items"][0]["item_id"], "started")["accepted"] is False


def test_selection_command_intent_is_idempotent_and_never_contains_private_paths(setup):
    from db import database
    coordinator, store, state, queue, transport = setup
    pick = {"song_id": 5, "prepared_override": {"filepath": "private-location"}}
    selected = coordinator.select(state["id"], "client-a", "select-1", 0, pick)
    assert coordinator.select(state["id"], "client-a", "select-1", 0, pick)["duplicate"] is True
    with pytest.raises(ConflictError):
        coordinator.select(state["id"], "client-a", "select-1", 0, {"song_id": 6})
    with pytest.raises(ConflictError):
        coordinator.advance(state["id"], "client-a", "select-1", 0)
    transport.start.assert_called_once()
    with database.get_connection_cm() as conn:
        receipt = conn.execute("SELECT result_json FROM playback_receipts WHERE event_id='select-1'").fetchone()[0]
    assert "private-location" not in receipt


def test_stop_fences_slow_explicit_selection(setup):
    coordinator, store, state, queue, transport = setup
    entered, release = threading.Event(), threading.Event()
    original = coordinator.planner
    def slow(pick, snapshot):
        entered.set()
        assert release.wait(2)
        return original(pick, snapshot)
    coordinator.planner = slow
    results = []
    worker = threading.Thread(target=lambda: results.append(coordinator.select(state["id"], "client-a", "select-1", 0, {"song_id": 5})))
    worker.start()
    try:
        assert entered.wait(1)
        coordinator.stop(state["id"], "client-a", "stop-1", 0)
    finally:
        release.set()
        worker.join(2)
    assert results[0]["cancelled"] is True
    transport.start.assert_not_called()
    assert queue.claims == []


@pytest.mark.parametrize("failure", [False, RuntimeError("output unplugged")])
def test_reactivation_start_failure_keeps_plan_retryable(setup, failure):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state["id"], "client-a", "next-1", 0)
    store.recover_interrupted()
    interrupted = store.snapshot(state["id"], "client-a")
    if failure is False:
        transport.start.return_value = False
    else:
        transport.start.side_effect = failure
    failed = coordinator.reactivate_output(state["id"], "client-a", "resume-1", interrupted["revision"], "screen-new")
    assert failed["status"] == "awaiting_output"
    assert failed["items"][0]["attempt_id"] == plan["items"][0]["attempt_id"]
    assert len(queue.claims) == 1
    transport.start.side_effect = None
    transport.start.return_value = True
    resumed = coordinator.reactivate_output(state["id"], "client-a", "resume-1", interrupted["revision"], "screen-new")
    assert resumed["instance_id"] == failed["instance_id"] and resumed["status"] == "ready"
    count = transport.start.call_count
    assert coordinator.reactivate_output(state["id"], "client-a", "resume-1", interrupted["revision"], "screen-new")["duplicate"] is True
    assert transport.start.call_count == count


def test_closed_session_releases_runtime_admission_and_stops_queue(setup):
    coordinator, store, state, queue, transport = setup
    coordinator.max_sessions = 1
    queue.shutdown = Mock()
    closed = coordinator.close_session(state["id"], "client-a", "close-1", 0)
    assert closed["status"] == "stopped"
    assert state["id"] not in coordinator._runtimes
    queue.shutdown.assert_called_once()
    transport.stop.assert_called_once()
    assert coordinator.create_session("client-b", "browser", Queue(), Mock())["status"] == "idle"


def test_old_handoff_retry_cannot_start_or_rebind_newer_awaiting_grant(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state['id'], 'client-a', 'next-1', 0)
    first, second, port = Mock(), Mock(), Mock()
    first.start.return_value = second.start.return_value = False
    coordinator._runtimes[state['id']].port = port
    confirmed(transport, plan, command='move-first')
    failed_first = coordinator.handoff_output(state['id'], 'client-a', 'move-first', plan['revision'],
                                               'browser', 'first-screen', target_transport=first)
    failed_second = coordinator.handoff_output(state['id'], 'client-a', 'move-second', failed_first['revision'],
                                                'browser', 'second-screen', target_transport=second)
    assert failed_second['status'] == 'awaiting_output'
    assert failed_first['instance_id'] != failed_second['instance_id']
    first_count, bind_count = first.start.call_count, port.bind_output.call_count
    retried = coordinator.handoff_output(state['id'], 'client-a', 'move-first', plan['revision'],
                                          'browser', 'first-screen', target_transport=first)
    assert retried['instance_id'] == failed_second['instance_id'] and retried['duplicate']
    assert first.start.call_count == first_count
    assert port.bind_output.call_count == bind_count
    assert coordinator._runtimes[state['id']].transport is second
    assert store.snapshot(state['id'], 'client-a')['output_client_id'] == 'second-screen'


def test_old_reactivation_retry_cannot_start_newer_awaiting_grant(setup):
    coordinator, store, state, queue, transport = setup
    coordinator.advance(state['id'], 'client-a', 'next-1', 0)
    store.recover_interrupted()
    interrupted = store.snapshot(state['id'], 'client-a')
    transport.start.return_value = False
    port = Mock()
    coordinator._runtimes[state['id']].port = port
    first = coordinator.reactivate_output(state['id'], 'client-a', 'resume-first', interrupted['revision'], 'first-screen')
    second = coordinator.reactivate_output(state['id'], 'client-a', 'resume-second', first['revision'], 'second-screen')
    assert first['instance_id'] != second['instance_id'] and second['status'] == 'awaiting_output'
    start_count, bind_count = transport.start.call_count, port.bind_output.call_count
    retried = coordinator.reactivate_output(state['id'], 'client-a', 'resume-first', interrupted['revision'], 'first-screen')
    assert retried['duplicate'] and retried['instance_id'] == second['instance_id']
    assert transport.start.call_count == start_count and port.bind_output.call_count == bind_count
    assert store.snapshot(state['id'], 'client-a')['output_client_id'] == 'second-screen'


def test_failed_reactivation_receipt_remains_retryable_after_runtime_reattach(setup):
    from engine.playback_coordinator import PlaybackCoordinator
    coordinator, store, state, queue, transport = setup
    coordinator.advance(state['id'], 'client-a', 'next-1', 0)
    store.recover_interrupted()
    interrupted = store.snapshot(state['id'], 'client-a')
    transport.start.return_value = False
    failed = coordinator.reactivate_output(state['id'], 'client-a', 'resume', interrupted['revision'], 'new-screen')
    restarted = PlaybackCoordinator(store, coordinator.planner)
    output = Mock()
    restarted.attach_session(state['id'], 'client-a', Queue(), output)
    resumed = restarted.reactivate_output(state['id'], 'client-a', 'resume', interrupted['revision'], 'new-screen')
    assert resumed['instance_id'] == failed['instance_id'] and resumed['status'] == 'ready'
    output.start.assert_called_once()


def test_runtime_repr_never_includes_owner_capability(setup):
    coordinator, store, state, queue, transport = setup
    assert 'client-a' not in repr(coordinator._runtimes[state['id']])


def test_command_instance_reads_original_receipt_and_requires_owner(setup):
    coordinator, store, state, queue, transport = setup
    first = coordinator.advance(state['id'], 'client-a', 'first', 0)
    second = coordinator.select(state['id'], 'client-a', 'second', first['revision'], {'song_id': 2})
    assert store.command_instance(state['id'], 'client-a', 'first') == first['instance_id']
    assert store.command_instance(state['id'], 'client-a', 'second') == second['instance_id']
    assert store.command_instance(state['id'], 'client-a', 'missing') is None
    with pytest.raises(PermissionError):
        store.command_instance(state['id'], 'wrong-owner', 'missing')


def test_failed_handoff_receipt_remains_retryable_after_runtime_reattach(setup):
    from engine.playback_coordinator import PlaybackCoordinator
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state['id'], 'client-a', 'next-1', 0)
    target = Mock()
    target.start.return_value = False
    confirmed(transport, plan)
    failed = coordinator.handoff_output(state['id'], 'client-a', 'move-1', plan['revision'],
                                         'browser', 'new-screen', target_transport=target)
    restarted = PlaybackCoordinator(store, coordinator.planner)
    output = Mock()
    restarted.attach_session(state['id'], 'client-a', Queue(), output)
    resumed = restarted.handoff_output(state['id'], 'client-a', 'move-1', plan['revision'],
                                         'browser', 'new-screen', target_transport=output)
    assert resumed['instance_id'] == failed['instance_id'] and resumed['status'] == 'ready'
    output.start.assert_called_once()


def test_reactivation_target_rebind_waits_for_receipt_identity_and_current_grant(setup):
    coordinator, store, state, queue, transport = setup
    coordinator.advance(state['id'], 'client-a', 'next-1', 0)
    store.recover_interrupted()
    interrupted = store.snapshot(state['id'], 'client-a')
    first_output, second_output = Mock(), Mock()
    first_output.start.return_value = second_output.start.return_value = False
    first = coordinator.reactivate_output(state['id'], 'client-a', 'first', interrupted['revision'],
                                           'screen-first', target_transport=first_output)
    second = coordinator.reactivate_output(state['id'], 'client-a', 'second', first['revision'],
                                            'screen-second', target_transport=second_output)
    starts = first_output.start.call_count
    old = coordinator.reactivate_output(state['id'], 'client-a', 'first', interrupted['revision'],
                                         'screen-first', target_transport=first_output)
    assert old['duplicate'] and old['instance_id'] == second['instance_id']
    assert coordinator._runtimes[state['id']].transport is second_output
    assert first_output.start.call_count == starts
    with pytest.raises(ConflictError):
        coordinator.reactivate_output(state['id'], 'client-a', 'first', interrupted['revision'],
                                       'different-screen', target_transport=Mock())
    assert coordinator._runtimes[state['id']].transport is second_output


@pytest.mark.parametrize('was_paused', [False, True])
def test_handoff_preserves_pre_quiesce_pause_intent_and_logical_attempt(setup, was_paused):
    from db import database
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state['id'], 'client-a', 'next-1', 0)
    item = plan['items'][0]
    started = coordinator.observe(state['id'], 'client-a', 'start', plan['instance_id'], item['item_id'], 'started')['session']
    if was_paused:
        before = coordinator.observe(state['id'], 'client-a', 'pause', plan['instance_id'], item['item_id'],
                                       'paused', position_ms=125)['session']
    else:
        before = started
    def quiesce(instance_id, command_id):
        if not was_paused:
            coordinator.observe(state['id'], 'client-a', 'quiesce-pause', instance_id, item['item_id'],
                                  'paused', position_ms=125)
        return {'confirmed': True, 'instance_id': instance_id, 'command_id': command_id, 'position_ms': 125}
    transport.quiesce.side_effect = quiesce
    target = Mock()
    moved = coordinator.handoff_output(state['id'], 'client-a', 'move', before['revision'],
                                        'browser', 'new-screen', target_transport=target)
    expected = 'paused' if was_paused else 'playing'
    assert moved['desired_status'] == expected
    assert target.start.call_args.args[0]['desired_status'] == expected
    assert moved['status'] == ('paused' if was_paused else 'ready')
    assert moved['items'][0]['attempt_id'] == item['attempt_id']
    assert database.get_song(1)['play_count'] == 1
    resumed = coordinator.observe(state['id'], 'client-a', 'target-start', moved['instance_id'],
                                    moved['items'][0]['item_id'], 'started', client_id='new-screen')
    assert resumed['accepted'] and not resumed['logical_start']
    assert database.get_song(1)['play_count'] == 1


def test_failed_paused_handoff_retry_stays_paused(setup):
    coordinator, store, state, queue, transport = setup
    plan = coordinator.advance(state['id'], 'client-a', 'next-1', 0)
    item = plan['items'][0]
    coordinator.observe(state['id'], 'client-a', 'start', plan['instance_id'], item['item_id'], 'started')
    paused = coordinator.observe(state['id'], 'client-a', 'pause', plan['instance_id'], item['item_id'],
                                   'paused', position_ms=125)['session']
    confirmed(transport, paused)
    target = Mock()
    target.start.return_value = False
    failed = coordinator.handoff_output(state['id'], 'client-a', 'move-1', paused['revision'],
                                         'browser', 'new-screen', target_transport=target)
    assert failed['status'] == 'awaiting_output' and failed['desired_status'] == 'paused'
    target.start.return_value = True
    moved = coordinator.handoff_output(state['id'], 'client-a', 'move-1', paused['revision'],
                                        'browser', 'new-screen', target_transport=target)
    assert moved['status'] == 'paused' and target.start.call_args.args[0]['status'] == 'paused'


