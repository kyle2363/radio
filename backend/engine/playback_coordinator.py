"""Session authority and preparation fencing, independent of the audio device.

Adapters synchronously publish/start an already prepared plan. They must not do
ranking, synthesis or remote round trips inside start/stop. Native transport
operations are ordered separately from the short state critical sections.
"""
from dataclasses import dataclass, field
import logging
import math
import threading
from uuid import uuid4

from db.playback_store import ConflictError

logger = logging.getLogger(__name__)


@dataclass
class _Runtime:
    owner: str = field(repr=False)
    queue: object
    transport: object
    port: object = None
    lock: object = field(default_factory=threading.RLock)
    transport_lock: object = field(default_factory=threading.RLock)
    epoch: int = 0
    active_epoch: int = 0
    instance_id: str | None = None
    active_reservation: object = None
    active_queue: object = None
    pending: dict = field(default_factory=dict)
    handoff_intent: dict | None = None
    closed: bool = False


class PlaybackCoordinator:
    def __init__(self, store, planner, *, feedback=None, max_sessions=16):
        self.store, self.planner = store, planner
        self.feedback = feedback or (lambda *args: None)
        self.max_sessions = max_sessions
        self._runtimes = {}
        self._registry_lock = threading.RLock()
        self._closed = False

    def create_session(self, owner, mode, queue, transport, *, port=None, output_client_id=None):
        with self._registry_lock:
            if self._closed or len(self._runtimes) >= self.max_sessions:
                raise ConflictError("Playback session admission is closed")
            state = self.store.create_session(owner, mode, output_client_id=output_client_id)
            self._runtimes[state["id"]] = _Runtime(owner, queue, transport, port=port)
            return state

    def attach_session(self, session_id, owner, queue, transport, *, port=None):
        """Attach restored session objects without automatically starting audio."""
        state = self.store.snapshot(session_id, owner)
        with self._registry_lock:
            if self._closed or len(self._runtimes) >= self.max_sessions:
                raise ConflictError("Playback session admission is closed")
            if session_id in self._runtimes:
                raise ConflictError("Playback session is already attached")
            self._runtimes[session_id] = _Runtime(owner, queue, transport, port=port,
                                                   instance_id=state["instance_id"])
        return state

    def _runtime(self, session_id, owner):
        self.store.snapshot(session_id, owner)  # Capability ownership is authoritative in storage.
        with self._registry_lock:
            if self._closed:
                raise ConflictError("Playback coordinator is shut down")
            runtime = self._runtimes.get(session_id)
            if runtime is None or runtime.closed:
                raise ConflictError("Playback session needs reconnection")
            return runtime

    @staticmethod
    def _release_claims(runtime):
        claims = [(runtime.queue, claim) for claim in runtime.pending.values()]
        if runtime.active_reservation is not None:
            claims.append((runtime.active_queue or runtime.queue, runtime.active_reservation))
        runtime.pending.clear()
        runtime.active_reservation = None
        runtime.active_queue = None
        for queue, claim in claims:
            queue.release_reservation(claim)

    def advance(self, session_id, owner, command_id, expected_revision):
        return self._prepare_and_start(session_id, owner, command_id, expected_revision,
                                       intent={"operation": "advance"})

    def select(self, session_id, owner, command_id, expected_revision, pick):
        """Prepare an explicit internal selection without claiming queued media."""
        if not isinstance(pick, dict) or not pick:
            raise ValueError("An explicit internal media selection is required")
        identity = {"operation": "select", "song_id": pick.get("song_id"), "type": pick.get("type", "song")}
        return self._prepare_and_start(session_id, owner, command_id, expected_revision,
                                       selected_pick=pick, intent=identity)

    def _prepare_and_start(self, session_id, owner, command_id, expected_revision,
                           *, selected_pick=None, intent):
        runtime = self._runtime(session_id, owner)
        reservation = None
        with runtime.lock:
            if runtime.closed:
                raise ConflictError("Playback session is closed")
            duplicate = self.store.command_snapshot(session_id, owner, command_id, "prepare", expected_revision,
                                                    intent=intent)
            if duplicate is not None:
                return duplicate
            snapshot = self.store.snapshot(session_id, owner)
            if snapshot["revision"] != expected_revision:
                raise ConflictError("Playback revision changed")
            if selected_pick is None and snapshot["status"] not in ("idle", "stopped"):
                raise ConflictError("Finish, skip or stop the current plan before advancing")
            runtime.epoch += 1
            epoch = runtime.epoch
            self._release_claims(runtime)
            if selected_pick is None:
                reservation = runtime.queue.reserve_next()
                if reservation is not None:
                    runtime.pending[epoch] = reservation
                    selected_pick = reservation.item
        if selected_pick is None:
            runtime.queue.request_rebuild()
            return {**snapshot, "pending": True}
        plan = None
        try:
            items = self.planner(selected_pick, snapshot)
            with runtime.transport_lock:
                with runtime.lock:
                    if runtime.epoch != epoch or self._closed or runtime.closed:
                        return {**self.store.snapshot(session_id, owner), "cancelled": True}
                    plan = self.store.prepare(session_id, owner, command_id, expected_revision, items, intent=intent)
                    if plan["duplicate"]:
                        return plan
                    runtime.active_reservation = reservation
                    runtime.active_queue = runtime.queue if reservation is not None else None
                    runtime.pending.pop(epoch, None)
                    runtime.active_epoch = epoch
                    runtime.instance_id = plan["instance_id"]
                if runtime.transport.start(plan) is False:
                    raise RuntimeError("Playback transport refused prepared media")
                return plan
        except Exception:
            with runtime.transport_lock:
                with runtime.lock:
                    current = runtime.epoch == epoch and not runtime.closed
                    if current:
                        failed = self.store.snapshot(session_id, owner)
                        if plan is not None and failed["instance_id"] == plan["instance_id"]:
                            self.store.stop(session_id, owner, uuid4().hex, failed["revision"])
                        self._release_claims(runtime)
                if current and plan is not None:
                    try:
                        runtime.transport.stop()
                    except Exception:
                        logger.exception("Failed transport cleanup: session=%s", session_id)
            raise
        finally:
            if reservation is not None:
                with runtime.lock:
                    runtime.pending.pop(epoch, None)
                    if runtime.active_reservation is not reservation:
                        runtime.queue.release_reservation(reservation)

    def reactivate_output(self, session_id, owner, command_id, expected_revision, client_id, *, target_transport=None):
        """Explicitly start restored media, keeping failed starts retryable."""
        runtime = self._runtime(session_id, owner)
        with runtime.transport_lock:
            with runtime.lock:
                state = self.store.reactivate(session_id, owner, command_id, expected_revision, client_id)
                if state["duplicate"]:
                    if (state["status"] != "awaiting_output" or
                            self.store.command_instance(session_id, owner, command_id) != state["instance_id"]):
                        return state
                if target_transport is not None:
                    runtime.transport = target_transport
                if not state["duplicate"]:
                    runtime.epoch += 1
                epoch = runtime.epoch
                runtime.active_epoch = epoch
                runtime.instance_id = state["instance_id"]
                runtime.handoff_intent = None
                if runtime.port is not None:
                    runtime.port.bind_output(runtime.transport, instance_id=state["instance_id"])
            try:
                if runtime.transport.start(state) is False:
                    raise RuntimeError("Playback output refused restored media")
            except Exception:
                logger.exception("Restored output awaits retry: session=%s", session_id)
                with runtime.lock:
                    failed = self.store.output_failed(session_id, owner, state["instance_id"])
                try:
                    runtime.transport.stop()
                except Exception:
                    logger.exception("Failed restored output cleanup: session=%s", session_id)
                return {**failed, "awaiting_output": failed["status"] == "awaiting_output"}
            with runtime.lock:
                if runtime.epoch != epoch or runtime.closed or self._closed:
                    return {**self.store.snapshot(session_id, owner), "cancelled": True}
                return self.store.output_ready(session_id, owner, state["instance_id"])

    def close_session(self, session_id, owner, command_id, expected_revision):
        """Stop and detach runtime resources, releasing session admission."""
        self.store.snapshot(session_id, owner)
        with self._registry_lock:
            runtime = self._runtimes.get(session_id)
        if runtime is None:
            return self.store.stop(session_id, owner, command_id, expected_revision)
        with runtime.transport_lock:
            with runtime.lock:
                state = self.store.stop(session_id, owner, command_id, expected_revision)
                runtime.closed = True
                runtime.epoch += 1
                self._release_claims(runtime)
            try:
                runtime.transport.stop()
            finally:
                try:
                    runtime.queue.shutdown()
                finally:
                    with self._registry_lock:
                        self._runtimes.pop(session_id, None)
            return state

    def stop(self, session_id, owner, command_id, expected_revision):
        runtime = self._runtime(session_id, owner)
        with runtime.lock:
            state = self.store.stop(session_id, owner, command_id, expected_revision)
            if state["duplicate"]:
                return state
            runtime.epoch += 1
            epoch = runtime.epoch
            self._release_claims(runtime)
        with runtime.transport_lock:
            with runtime.lock:
                current = runtime.epoch == epoch
            if current:
                runtime.transport.stop()
        return state

    def observe(self, session_id, owner, event_id, instance_id, item_id, kind, **kwargs):
        runtime = self._runtime(session_id, owner)
        with runtime.lock:
            if runtime.active_epoch != runtime.epoch:
                return {"accepted": False, "duplicate": False,
                        "session": self.store.snapshot(session_id, owner)}
            snapshot = self.store.snapshot(session_id, owner)
            item = next((entry for entry in snapshot["items"] if entry["item_id"] == item_id), None)
            result = self.store.observe(session_id, owner, event_id, instance_id, item_id, kind, **kwargs)
            # Durable validation/history acceptance comes first. A retried
            # accepted event can reconcile a prior queue-commit interruption.
            if result["accepted"] and runtime.instance_id == instance_id and runtime.active_reservation is not None:
                observed = next((entry for entry in result["session"]["items"] if entry["item_id"] == item_id), None)
                pick = runtime.active_reservation.item
                logical_start = bool(observed and observed["started"] and (
                    observed["kind"] == "song" and observed["song_id"] == pick.get("song_id") or
                    pick.get("type") == "talk_segment" and observed["kind"] == "speech"))
                if logical_start:
                    try:
                        committed = (runtime.active_queue or runtime.queue).commit_reservation(runtime.active_reservation)
                        if committed is None:
                            logger.warning("Played item has no live queue claim: session=%s", session_id)
                        runtime.active_reservation = None
                        runtime.active_queue = None
                    except Exception:
                        result["queue_commit_pending"] = True
                        logger.exception("Queue commit needs reconciliation: session=%s", session_id)
        if result["accepted"] and not result["duplicate"]:
            try:
                self.feedback(session_id, kind, result["session"], item)
            except Exception:
                logger.exception("Playback feedback hook failed: session=%s kind=%s", session_id, kind)
        return result

    def handoff_output(self, session_id, owner, command_id, expected_revision,
                       target_mode, target_client_id, *, target_transport):
        """Move this whole listening session to an internally resolved output.

        Service admission must reserve exclusive outputs before calling here.
        Quiesce only enqueues/polls a correlated acknowledgement; it never waits
        for a remote client. A failed target retains the same retryable plan.
        """
        if target_transport is None:
            raise ConflictError("Playback output is unavailable")
        runtime = self._runtime(session_id, owner)
        with runtime.transport_lock:
            with runtime.lock:
                duplicate = self.store.handoff_snapshot(session_id, owner, command_id, expected_revision,
                                                        target_mode, target_client_id)
                before = duplicate or self.store.snapshot(session_id, owner)
                if duplicate is not None:
                    if (before["status"] != "awaiting_output" or
                            self.store.command_instance(session_id, owner, command_id) != before["instance_id"]):
                        return duplicate
                if duplicate is None:
                    request = (command_id, expected_revision, target_mode, target_client_id)
                    intent = runtime.handoff_intent
                    if intent is not None and intent["epoch"] == runtime.epoch:
                        if intent["request"] != request:
                            raise ConflictError("An output handoff is already awaiting confirmation")
                        if intent["instance_id"] != before["instance_id"]:
                            raise ConflictError("Playback instance changed")
                    else:
                        if before["revision"] != expected_revision:
                            raise ConflictError("Playback revision changed")
                        runtime.handoff_intent = {"request": request, "instance_id": before["instance_id"],
                                                  "epoch": runtime.epoch,
                                                  "desired_status": ("paused" if before["status"] == "paused" else
                                                      before.get("desired_status", "playing") if before["status"] == "awaiting_output" else "playing")}
                epoch = runtime.epoch
            if duplicate is None:
                if before["items"] and before["status"] != "awaiting_output":
                    confirmation = runtime.transport.quiesce(before["instance_id"], command_id=command_id)
                    correlated = bool(confirmation and confirmation.get("confirmed") is True
                                      and confirmation.get("instance_id") == before["instance_id"]
                                      and confirmation.get("command_id") == command_id)
                    if not correlated:
                        return {**self.store.snapshot(session_id, owner), "awaiting_confirmation": True}
                    position = confirmation.get("position_ms", before["position_ms"])
                else:
                    position = before["position_ms"]
                with runtime.lock:
                    if runtime.epoch != epoch or self._closed:
                        return {**self.store.snapshot(session_id, owner), "cancelled": True}
                    current = self.store.snapshot(session_id, owner)
                    if current["instance_id"] != before["instance_id"]:
                        raise ConflictError("Playback instance changed")
                    moved = self.store.handoff_output(session_id, owner, command_id, expected_revision,
                                                      target_mode, target_client_id, position_ms=position,
                                                      observed_revision=current["revision"],
                                                      desired_status=runtime.handoff_intent["desired_status"])
                    runtime.epoch += 1
                    epoch = runtime.epoch
                    runtime.active_epoch = epoch
                    runtime.instance_id = moved["instance_id"]
                    runtime.handoff_intent = None
                    # The same queue, claims, mood, energy and DJ context stay
                    # attached to this runtime. Only its output is rebound.
                    runtime.transport = target_transport
                    if runtime.port is not None:
                        runtime.port.bind_output(target_transport, instance_id=moved["instance_id"])
            else:
                moved = before
                with runtime.lock:
                    runtime.transport = target_transport
                    if runtime.port is not None:
                        runtime.port.bind_output(target_transport, instance_id=moved["instance_id"])
            if not moved["items"]:
                return moved
            if moved.get("desired_status") == "paused":
                # Publish a paused grant before adapters prime it. Browser
                # subscribers must never receive an autoplay-ready snapshot.
                moved = self.store.output_ready(session_id, owner, moved["instance_id"])
            try:
                if target_transport.start(moved) is False:
                    raise RuntimeError("Playback output refused prepared media")
            except Exception:
                logger.exception("Output handoff awaits retry: session=%s", session_id)
                self.store.output_failed(session_id, owner, moved["instance_id"])
                # Never stop the durable session or release its active claim.
                # The source acknowledged quiescence; keep its position and
                # logical attempt until an admitted target accepts the plan.
                try:
                    target_transport.stop()
                except Exception:
                    logger.exception("Failed output cleanup: session=%s", session_id)
                return {**self.store.snapshot(session_id, owner), "awaiting_output": True}
            with runtime.lock:
                if runtime.epoch != epoch or self._closed:
                    return {**self.store.snapshot(session_id, owner), "cancelled": True}
                return self.store.output_ready(session_id, owner, moved["instance_id"])

    def shutdown(self):
        with self._registry_lock:
            if self._closed:
                return
            self._closed = True
            runtimes = list(self._runtimes.items())
        for session_id, runtime in runtimes:
            with runtime.transport_lock:
                with runtime.lock:
                    runtime.epoch += 1
                    self._release_claims(runtime)
                    snapshot = self.store.snapshot(session_id, runtime.owner)
                    try:
                        position = runtime.transport.get_time()
                    except Exception:
                        position = None
                    if type(position) not in (int, float) or not math.isfinite(position) or position < 0:
                        position = None
                    self.store.interrupt_session(session_id, runtime.owner, snapshot["instance_id"], position_ms=position)
                try:
                    runtime.transport.stop()
                finally:
                    runtime.queue.shutdown()
