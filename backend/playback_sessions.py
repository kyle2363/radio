"""Listening sessions own recommendation context; outputs are replaceable adapters.

Construction is inert. The caller owns API authentication, lazy store installation,
and routing output-scoped events. A controller capability is returned once at
creation and must be carried in a header, never a URL or an emitted payload.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
import logging
import math
from pathlib import Path
import secrets
import threading
import time
from uuid import uuid4
from uuid import NAMESPACE_URL, uuid5

from db.playback_store import ConflictError
from engine.audio_profiles import allowed_presets, bands_for
from engine.playback_coordinator import PlaybackCoordinator
from engine.session_port import SessionPlaybackPort

logger = logging.getLogger(__name__)


def _trace(action, session_id, state, *, command_id=None, event_id=None, item_id=None, accepted=None):
    # Deliberately exclude owner keys, producer identities, URLs and media paths.
    logger.info("playback action=%s session=%s instance=%s item=%s command=%s event=%s revision=%s accepted=%s duplicate=%s",
                action, session_id, state.get("instance_id"), item_id, command_id, event_id,
                state.get("revision"), accepted, bool(state.get("duplicate")))


class BrowserOutput:
    """A browser grant publishes prepared state and awaits actual observations."""
    def __init__(self, session_id, client_id, emit):
        self.session_id, self.client_id, self.emit = session_id, client_id, emit
        self._lock = threading.RLock()
        self._snapshot = {"items": [], "active_index": 0, "status": "idle", "position_ms": 0}
        self._pending = None
        self._ack = None
        self._volume, self._eq = 70, "flat"
        self._explicitly_stopped = False

    def start(self, snapshot):
        self._explicitly_stopped = False
        self.update(snapshot)
        self.emit("playback_state", deepcopy(snapshot))
        return True

    def update(self, snapshot):
        with self._lock:
            self._snapshot = deepcopy(snapshot)

    def _control(self, action, **kwargs):
        with self._lock:
            data = {"session_id": self.session_id, "output_client_id": self.client_id,
                    "instance_id": self._snapshot.get("instance_id"), "action": action, **kwargs}
            data["id"] = data.get("command_id") or uuid4().hex
        self.emit("playback_control", data)
        return True

    def quiesce(self, instance_id, command_id):
        with self._lock:
            key = (instance_id, command_id)
            if self._pending == key and self._ack is not None:
                return dict(self._ack)
            publish = self._pending != key
            self._pending, self._ack = key, None
        if publish:
            self._control("quiesce", instance_id=instance_id, command_id=command_id)
        return None

    def ack_quiesce(self, instance_id, command_id, position_ms):
        with self._lock:
            if self._pending != (instance_id, command_id):
                raise ValueError("Quiesce acknowledgement does not match a pending command")
            self._ack = {"confirmed": True, "instance_id": instance_id,
                         "command_id": command_id, "position_ms": position_ms}
            return dict(self._ack)

    def cancel_quiesce(self, command_id):
        with self._lock:
            if self._pending is None or self._pending[1] != command_id:
                return
            self._pending, self._ack = None, None
        self.pause()

    def stop(self):
        self._explicitly_stopped = True
        return self._control("stop")

    def pause(self):
        return self._control("pause")

    def resume(self):
        return self._control("resume")

    def seek(self, position_ms):
        return self._control("seek", position_ms=position_ms)

    def set_volume(self, volume):
        self._volume = volume
        return self._control("volume", volume=volume)

    def set_eq_preset(self, preset):
        self._eq = preset
        return self._control("eq", preset=preset, eq=preset, eq_bands=bands_for(preset))

    def set_normalization_enabled(self, enabled):
        return self._control("normalization", enabled=bool(enabled))

    def get_current_song_id(self):
        item = self._item()
        return item.get("song_id") if item.get("started") else None

    def _item(self):
        with self._lock:
            items, index = self._snapshot.get("items", []), self._snapshot.get("active_index", 0)
            return dict(items[index]) if index < len(items) else {}

    def get_time(self):
        with self._lock:
            return self._snapshot.get("position_ms", 0)

    def get_length(self):
        return self._item().get("duration_ms", 0)

    def get_position(self):
        length = self.get_length()
        return self.get_time() / length if length else 0

    def get_state(self):
        with self._lock:
            return self._snapshot.get("status", "idle")

    def get_volume(self):
        return self._volume

    def is_playing(self):
        return self.get_state() == "playing"

    def is_talk_playing(self):
        item = self._item()
        return item.get("kind") == "speech" and item.get("started", False)


@dataclass
class ListeningContext:
    queue: object
    mood: object
    energy: object
    talk: object
    announcements: object
    port: object
    output: object = None
    preferences: dict = field(default_factory=lambda: {
        "volume": 70, "eq": "flat", "normalization": True, "auto_advance": True,
        "play_mode": "ai", "folder_filter": None, "sleep_timer": None})
    lock: object = field(default_factory=threading.RLock)
    progress: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)
    native_sequences: dict = field(default_factory=dict)
    lease_renewed_at: float = float("-inf")
    timer: object = None
    timer_cancel: object = None
    closing: bool = False
    handoff_timer: object = None


class PlaybackSessions:
    def __init__(self, store, media_factory, queue_factory=None, emit=None, vlc_output_factory=None,
                 producer_online=None):
        self.store, self._media_factory = store, media_factory
        self._media_instance = None
        self._queue_factory, self._emit = queue_factory, emit or (lambda *args: None)
        self._vlc_output_factory = vlc_output_factory
        # Optional probe: does this browser client still hold a control channel?
        self._producer_online = producer_online
        self._contexts, self._owners, self._prepared = {}, {}, {}
        self._lock = threading.RLock()
        self._local = threading.local()
        self._closed, self._vlc_session = False, None
        self._executor, self._jobs = None, {}
        self._clock = time.monotonic
        self._wall_clock, self._timer_factory = time.time, threading.Timer
        self._handoff_timer_factory = threading.Timer
        self.coordinator = PlaybackCoordinator(store, self._plan)

    @property
    def media(self):
        with self._lock:
            if self._media_instance is None:
                self._media_instance = self._media_factory()
            return self._media_instance

    @staticmethod
    def _client(value):
        value = uuid4().hex if value is None else value
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
            raise ValueError("Output client ID must contain 1 to 128 printable characters")
        return value

    def _admit(self):
        if self._closed:
            raise ConflictError("Playback session admission is closed")

    def _new_context(self, session_id=None, queue=None):
        from db import database
        from engine.energy import EnergyTrajectory
        from engine.mood import MoodStateMachine
        from engine.session_context import new_talk_context, new_announcement_context
        from engine.session_settings import SessionSettings
        port = SessionPlaybackPort()
        context = ListeningContext(None, None, None, None, None, port)
        settings = SessionSettings(database, context.preferences.setdefault("settings", {}))
        if queue is None:
            mood, energy = MoodStateMachine(persist_state=False), EnergyTrajectory()
            talk, announcements = new_talk_context(), new_announcement_context()
            factory = self._queue_factory
            if factory is None:
                from engine.queue import QueueManager
                factory = QueueManager
            holder = {}
            def broadcast(event, data):
                if "queue" in holder:
                    self._queue_event(holder["queue"], event, data)
            queue = factory(port, settings, mood_machine=mood, energy_trajectory=energy,
                            talk_context=talk, announcement_context=announcements,
                            broadcast_callback=broadcast)
            holder["queue"] = queue
        else:
            mood, energy = queue._mood, queue._energy_trajectory
            talk, announcements = queue._talk_context, queue._announcements
            queue._player = port
            queue._db = settings
            queue._broadcast = lambda event, data: self._queue_event(queue, event, data)
        context.queue, context.mood, context.energy = queue, mood, energy
        context.talk, context.announcements = talk, announcements
        return context

    def _queue_event(self, queue, event, data):
        with self._lock:
            sid = next((sid for sid, context in self._contexts.items() if context.queue is queue), None)
        if sid is not None:
            self._publish(sid, event, self._clean(data))

    def _output(self, sid, owner, mode, client_id):
        if mode == "browser":
            return BrowserOutput(sid, client_id, lambda event, data: self._publish(sid, event, data))
        if mode != "vlc":
            raise ValueError("Valid playback output mode required")
        if self._vlc_output_factory is None:
            raise ConflictError("Desktop playback is unavailable")
        return self._vlc_output_factory(sid, owner, lambda event: self._native_observation(sid, owner, event))

    def _publish(self, sid, event, data):
        if event == "playback_state":
            owner = self._owners.get(sid)
            if owner is not None and not self._closed:
                data = self.snapshot(sid, owner)
            data = {"session_id": sid, "session": data}
        else:
            data = {**data, "session_id": sid}
        parameters = inspect.signature(self._emit).parameters.values()
        positional = [p for p in parameters if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(positional) == 2:
            self._emit(event, data)
        else:
            self._emit(sid, event, data)

    def create(self, mode="browser", client_id=None, *, warm_start=False):
        if mode not in ("browser", "vlc"):
            raise ValueError("Valid playback output mode required")
        if mode == "vlc" and self._vlc_output_factory is None:
            raise ConflictError("Desktop playback is unavailable")
        client_id = self._client(client_id) if mode == "browser" else None
        owner = secrets.token_urlsafe(32)
        with self._lock:
            self._admit()
            if mode == "vlc" and self._vlc_session is not None:
                raise ConflictError("Desktop output is occupied by another listening session")
            context = self._new_context()
            sid = None
            try:
                # The ID is storage-generated; the inert browser adapter is replaced
                # before anything can prepare media or publish an output grant.
                inert = BrowserOutput("", None, lambda *args: None)
                state = self.coordinator.create_session(owner, mode, context.queue, inert,
                                                        port=context.port, output_client_id=client_id)
                sid = state["id"]
                context.output = self._output(sid, owner, mode, client_id)
                self.coordinator._runtime(sid, owner).transport = context.output
                context.port.bind_output(context.output, instance_id=state["instance_id"])
                if isinstance(context.output, BrowserOutput):
                    context.output.update(state)
                self._contexts[sid], self._owners[sid] = context, owner
                self._configure_context(sid, owner, context)
                if mode == "vlc":
                    self._vlc_session = sid
            except Exception:
                if sid is not None:
                    failed = self.store.snapshot(sid, owner)
                    self.coordinator.close_session(sid, owner, uuid4().hex, failed["revision"])
                    self._contexts.pop(sid, None)
                    self._owners.pop(sid, None)
                else:
                    context.queue.shutdown()
                raise
        self._persist(sid, owner)
        if warm_start:
            context.queue.request_rebuild()
        return {"session": self.snapshot(sid, owner), "owner_key": owner, "client_id": client_id}

    def adopt_desktop(self, queue, *, owner_key=None):
        owner = owner_key or secrets.token_urlsafe(32)
        with self._lock:
            self._admit()
            if self._vlc_session is not None:
                raise ConflictError("Desktop output is occupied by another listening session")
            context = self._new_context(queue=queue)
            state = self.coordinator.create_session(owner, "vlc", queue, None, port=context.port)
            sid = state["id"]
            context.output = self._output(sid, owner, "vlc", None)
            volume = context.output.get_volume()
            if type(volume) is int and 0 <= volume <= 100:
                context.preferences["volume"] = volume
            preset = context.output.get_eq_preset()
            if isinstance(preset, str) and len(preset) <= 64:
                context.preferences["eq"] = preset
            self.coordinator._runtime(sid, owner).transport = context.output
            context.port.bind_output(context.output)
            self._contexts[sid], self._owners[sid], self._vlc_session = context, owner, sid
            self._configure_context(sid, owner, context)
        self._persist(sid, owner)
        return {"session": self.snapshot(sid, owner), "owner_key": owner, "client_id": None}

    def context_for(self, session_id, owner_key):
        self.store.snapshot(session_id, owner_key)
        with self._lock:
            self._admit()
            context = self._contexts.get(session_id)
            if context is None or context.closing:
                raise ConflictError("Listening session needs reconnection")
            return context

    def _configure_context(self, sid, owner, context):
        def prepared(plan):
            state = self.store.snapshot(sid, owner)
            return self.select_prepared(sid, owner, plan, uuid4().hex, state["revision"])
        def song(song_id, *args, **kwargs):
            state = self.store.snapshot(sid, owner)
            return self.select_song(sid, owner, song_id, uuid4().hex, state["revision"])
        def stop(*args, **kwargs):
            state = self.store.snapshot(sid, owner)
            return self.stop(sid, owner, uuid4().hex, state["revision"])
        context.port.configure_requests(prepared=prepared, song=song)
        configure_actions = getattr(context.port, "configure_actions", None)
        if configure_actions:
            configure_actions(stop=stop, pause=lambda: self.pause(sid, owner), resume=lambda: self.resume(sid, owner),
                              seek=lambda position: self.seek(sid, owner, position),
                              volume=lambda value: self.set_volume(sid, owner, value),
                              eq=lambda value: self.set_eq(sid, owner, value),
                              normalization=lambda value: self.set_normalization(sid, owner, value))

    @staticmethod
    def _clean(value):
        """Reduce queue/event payloads to a known public metadata vocabulary."""
        allowed = {"id", "song_id", "title", "artist", "album", "duration_secs", "duration_ms",
                   "energy", "valence", "bpm", "slot", "reason", "type", "segment_type",
                   "persona_id", "secondary_persona_id", "mood", "source", "rebuilding",
                   "queue", "count", "next_song_id", "next_song_title", "next_song_artist"}
        if isinstance(value, dict):
            return {key: PlaybackSessions._clean(item) for key, item in value.items() if key in allowed}
        if isinstance(value, (tuple, list)):
            return [PlaybackSessions._clean(item) for item in value[:100]]
        return value if value is None or type(value) in (str, int, float, bool) else None

    def _queue_snapshot(self, context):
        return self._clean(context.queue._get_display_queue())[:100]

    def snapshot(self, session_id, owner_key):
        context = self.context_for(session_id, owner_key)
        state = self.store.snapshot(session_id, owner_key)
        with context.lock:
            progress = context.progress
            if progress and progress.get("instance_id") == state["instance_id"] and progress.get("item_id") == self._active(state).get("item_id"):
                state["position_ms"] = progress["position_ms"]
            preferences = deepcopy(context.preferences)
        if (state["mode"] == "vlc" and state["status"] in ("playing", "paused")
                and not isinstance(context.output, BrowserOutput)):
            position = context.output.get_time()
            if type(position) in (int, float) and math.isfinite(position) and position >= 0:
                state["position_ms"] = position
                self._checkpoint_native(session_id, owner_key, context, state)
        self._renew_resources(session_id, context, state)
        vibe = context.mood.get_vibe_lock_info()
        public_vibe = {key: vibe.get(key) for key in ("active", "song_id", "expires_at", "songs_remaining")}
        public_context = deepcopy(state.get("context", {}))
        public_context.update({
            "volume": preferences["volume"],
            "eq": preferences["eq"],
            "eq_bands": bands_for(preferences["eq"]),
            "normalization": preferences["normalization"],
            "talk_volume": context.queue._db.get_setting("talk_volume") or "100",
        })
        if state["status"] == "awaiting_output" and state.get("desired_status") == "paused":
            # Retain the durable retry state without publishing an autoplay
            # status for a grant that the listener deliberately left paused.
            state.update(status="paused", awaiting_output=True)
        return {**state, "producer_lease_remaining_ms": self.store.producer_lease_remaining(state),
                "session_id": session_id, "queue": self._queue_snapshot(context), "mood": context.mood.get_state(),
                "context": public_context, "vibe_lock": public_vibe, "preferences": preferences,
                "energy": {"direction": context.energy.get_direction(), "current": context.energy.get_current_energy()}}

    def _checkpoint_native(self, sid, owner, context, state):
        item = self._active(state)
        if not item.get("started") or item.get("terminal"):
            return
        with context.lock:
            now = self._clock()
            previous = context.progress
            if now - previous.get("checkpoint_at", float("-inf")) < 5:
                return
            sequence = max(state.get("position_seq", -1), previous.get("sequence", -1)) + 1
            result = self.store.checkpoint_position(sid, owner, state["instance_id"], item["item_id"],
                                                     None, sequence, state["position_ms"])
            if result["accepted"]:
                context.progress = {"instance_id": state["instance_id"], "item_id": item["item_id"],
                                    "sequence": sequence, "position_ms": state["position_ms"], "checkpoint_at": now}

    def _renew_resources(self, sid, context, state):
        with context.lock:
            ids = {item.get("preparation_id") for item in state["items"] if item.get("resource_id")}
            if not ids:
                return
            now = self._clock()
            if now - context.lease_renewed_at < 300:
                return
            context.lease_renewed_at = now
        for prep in ids - {None}:
            self.media.renew_session(f"{sid}:{prep}")

    @staticmethod
    def _active(state):
        index, items = state.get("active_index", 0), state.get("items", [])
        return items[index] if index < len(items) else {}

    def _persist(self, sid, owner):
        context = self.context_for(sid, owner)
        vibe = context.mood.get_vibe_lock_info()
        for key in ("embedding", "audio_embedding"):
            vector = vibe.get(key)
            if hasattr(vector, "tolist"):
                vibe[key] = vector.tolist()
        talk_keys = ("_song_counter", "_current_guest_takeover", "_recent_callers", "_recent_guests",
                     "_recent_segment_types", "_last_segment_mood")
        announcement_keys = ("_last_announcement_time", "_radio_intro_count", "_songs_since_last_skip")
        self.store.save_context(sid, owner, {"mood": context.mood.get_state(), "vibe_lock": vibe,
            "preferences": deepcopy(context.preferences), "queue": self._queue_snapshot(context),
            "energy": deepcopy(getattr(context.energy, "_recent_energies", []))[-5:],
            "talk": {key: self._primitive(getattr(context.talk, key, None)) for key in talk_keys},
            "announcements": {key: self._primitive(getattr(context.announcements, key, None)) for key in announcement_keys}})

    @staticmethod
    def _primitive(value, depth=0):
        if depth > 8:
            return None
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is float:
            return value if math.isfinite(value) else None
        if isinstance(value, str):
            return value[:2048]
        if isinstance(value, (tuple, list)):
            return [PlaybackSessions._primitive(item, depth + 1) for item in value[:32]]
        if isinstance(value, dict):
            return {str(key)[:64]: PlaybackSessions._primitive(item, depth + 1)
                    for key, item in list(value.items())[:32] if "path" not in str(key).lower()}
        return None

    def attach(self, session_id, owner_key):
        """Restore context and reconnect a controller without starting output."""
        state = self.store.snapshot(session_id, owner_key)
        with self._lock:
            self._admit()
            context = self._contexts.get(session_id)
            newly_attached = context is None
            if newly_attached:
                if state["mode"] == "vlc" and self._vlc_session not in (None, session_id):
                    raise ConflictError("Desktop output is occupied by another listening session")
                context = self._new_context()
                saved = state.get("context", {})
                context.mood.set_state(saved.get("mood", "neutral"))
                vibe = saved.get("vibe_lock")
                if isinstance(vibe, dict):
                    with context.mood._lock:
                        context.mood._vibe_lock.update(vibe)
                context.energy._recent_energies = saved.get("energy", [])[-5:]
                for section, target in (("talk", context.talk), ("announcements", context.announcements)):
                    for key, value in saved.get(section, {}).items():
                        if hasattr(target, key):
                            setattr(target, key, value)
                saved_preferences = saved.get("preferences", {})
                context.preferences.update({key: value for key, value in saved_preferences.items()
                                            if key in context.preferences and key != "settings"})
                context.preferences.setdefault("settings", {}).update(saved_preferences.get("settings", {}))
                queue_ids = [item["song_id"] for item in saved.get("queue", []) if type(item.get("song_id")) is int]
                context.queue.clear_queue()
                if queue_ids:
                    context.queue.add_manual(queue_ids[:100])
                context.output = self._output(session_id, owner_key, state["mode"], state.get("output_client_id"))
                if isinstance(context.output, BrowserOutput):
                    context.output.update(state)
                context.port.bind_output(context.output, instance_id=state["instance_id"])
                self.coordinator.attach_session(session_id, owner_key, context.queue, context.output, port=context.port)
                self._contexts[session_id], self._owners[session_id] = context, owner_key
                self._configure_context(session_id, owner_key, context)
                if state["mode"] == "vlc":
                    self._vlc_session = session_id
        if newly_attached and context.preferences.get("sleep_timer"):
            self._arm_timer(session_id, owner_key, context)
        return self.snapshot(session_id, owner_key)

    def _plan(self, pick, state):
        context = self.context_for(state["id"], self._owners[state["id"]])
        private = getattr(self._local, "prepared_override", None) or context.queue.prepare_pick(pick)
        prep = uuid4().hex
        lease = f'{state["id"]}:{prep}'
        items = []
        try:
            for item in private.items:
                entry = {"kind": item.kind, "song_id": item.song_id, "duration_ms": item.duration_ms,
                         "gain_db": item.gain_db, "role": item.role, "preparation_id": prep}
                original = self.media.lookup_song(item.song_id) if item.kind == "song" else None
                same = bool(original and original.get("filepath") and
                            Path(item.filepath).resolve() == Path(original["filepath"]).resolve())
                if same:
                    entry.update(track_id=item.song_id, url=f"/api/media/tracks/{item.song_id}/stream")
                else:
                    resource = self.media.register_generated(item.filepath, lease)
                    entry.update(resource_id=resource, url=f"/api/media/resources/{resource}/stream")
                items.append(entry)
            with self._lock:
                self._prepared[prep] = {"private": private, "lease": lease, "activated": set()}
            self._local.preparations.append(prep)
            return items
        except Exception:
            self.media.release_session(lease)
            raise

    def _release_preparations(self, session_id, ids):
        for prep in set(ids):
            with self._lock:
                prepared = self._prepared.pop(prep, None)
            if self._media_instance is not None:
                self.media.release_session(prepared["lease"] if prepared else f"{session_id}:{prep}")

    @staticmethod
    def _preparation_ids(state):
        return {item["preparation_id"] for item in state.get("items", []) if item.get("preparation_id")}

    def _prepare_command(self, sid, owner, callback, *, private=None):
        before = self.store.begin_history_session(sid, owner)
        self.context_for(sid, owner).queue._current_session_id = before["history_session_id"]
        self._local.preparations, self._local.prepared_override = [], private
        try:
            result = callback()
        finally:
            created = self._local.preparations
            self._local.preparations, self._local.prepared_override = [], None
            current = self.store.snapshot(sid, owner)
            retained = self._preparation_ids(current)
            self._release_preparations(sid, (set(created) | self._preparation_ids(before)) - retained)
            with self._lock:
                context = self._contexts.get(sid)
                alive = not self._closed and context is not None and not context.closing
            if alive:
                if isinstance(context.output, BrowserOutput):
                    context.output.update(current)
                self._persist(sid, owner)
        return self._public_state(sid, owner, result)

    def _public_state(self, sid, owner, result):
        """Return the current public shape while retaining command receipts."""
        with self._lock:
            context = self._contexts.get(sid)
            alive = not self._closed and context is not None and not context.closing
        if not alive:
            return result
        flags = {key: result[key] for key in ("duplicate", "pending", "awaiting_confirmation",
                 "awaiting_output", "cancelled", "queue_commit_pending") if key in result}
        return {**self.snapshot(sid, owner), **flags}

    @contextmanager
    def _locked_output(self, sid, owner):
        context = self.context_for(sid, owner)
        runtime = self.coordinator._runtime(sid, owner)
        with runtime.transport_lock:
            if runtime.closed or self._closed:
                raise ConflictError("Listening session is closed")
            context.output = runtime.transport
            yield context, runtime.transport

    def next(self, session_id, owner_key, command_id, expected_revision):
        context = self.context_for(session_id, owner_key)
        snapshot = self.store.snapshot(session_id, owner_key)
        runtime = self.coordinator._runtime(session_id, owner_key)
        duplicate = self.store.command_snapshot(
            session_id, owner_key, command_id, "prepare", expected_revision,
            intent={"operation": "advance"},
        )
        if (duplicate is None and snapshot["revision"] == expected_revision
                and snapshot["status"] in ("idle", "stopped")
                and callable(getattr(context.queue, "ensure_available", None))):
            with runtime.lock:
                expected_epoch = runtime.epoch

            def cancelled():
                with self._lock:
                    if self._closed or context.closing:
                        return True
                with runtime.lock:
                    if (runtime.closed or runtime.epoch != expected_epoch
                            or runtime.handoff_intent is not None):
                        return True
                current = self.store.snapshot(session_id, owner_key)
                return (current["revision"] != expected_revision
                        or current["instance_id"] != snapshot["instance_id"]
                        or current["status"] not in ("idle", "stopped"))

            context.queue.ensure_available(timeout=1.0, cancelled=cancelled)
        return self._prepare_command(session_id, owner_key, lambda: self.coordinator.advance(
            session_id, owner_key, command_id, expected_revision))

    def select_song(self, session_id, owner_key, song_id, command_id, expected_revision):
        if type(song_id) is not int or song_id <= 0:
            raise ValueError("A positive song ID is required")
        self.context_for(session_id, owner_key)
        return self._prepare_command(session_id, owner_key, lambda: self.coordinator.select(
            session_id, owner_key, command_id, expected_revision, {"song_id": song_id}))

    def select_prepared(self, session_id, owner_key, plan, command_id, expected_revision):
        self.context_for(session_id, owner_key)
        return self._prepare_command(session_id, owner_key, lambda: self.coordinator.select(
            session_id, owner_key, command_id, expected_revision, {"song_id": plan.song_id}), private=plan)

    def replay(self, session_id, owner_key, command_id, expected_revision):
        """Replay the started song in place, retaining the current session plan."""
        with self._locked_output(session_id, owner_key) as (context, output):
            result = self.coordinator.replay(session_id, owner_key, command_id, expected_revision)
            if not result.get("duplicate"):
                if isinstance(output, BrowserOutput):
                    output.update(result)
                with context.lock:
                    context.progress.clear()
                record_replay = getattr(context.queue, "record_replay", None)
                if callable(record_replay):
                    try:
                        record_replay(persist=False)
                    except Exception:
                        logger.exception("Could not update replay taste context: session=%s", session_id)
                self._persist(session_id, owner_key)
                item = self._active(result)
                _trace("replay", session_id, result, command_id=command_id, item_id=item.get("item_id"), accepted=True)
                self._publish(session_id, "song_replayed", {"song_id": item.get("song_id")})
            return self._public_state(session_id, owner_key, result)

    def stop(self, session_id, owner_key, command_id, expected_revision):
        with self._locked_output(session_id, owner_key) as (context, output):
            before = self.store.snapshot(session_id, owner_key)
            result = self.coordinator.stop(session_id, owner_key, command_id, expected_revision)
            if not result.get("duplicate"):
                self._release_preparations(session_id, self._preparation_ids(before))
                with context.lock:
                    context.progress.clear()
                if isinstance(output, BrowserOutput):
                    output.update(result)
                self._clear_targets(context)
                if result["mode"] != "vlc":
                    with self._lock:
                        if self._vlc_session == session_id:
                            self._vlc_session = None
            self._persist(session_id, owner_key)
            return self._public_state(session_id, owner_key, result)

    @staticmethod
    def _clear_targets(context):
        with context.lock:
            targets = list(context.targets.values())
            context.targets.clear()
        for target in targets:
            if target is not context.output and not isinstance(target, BrowserOutput):
                dispose = getattr(target, "dispose", None)
                if dispose:
                    dispose()

    def observe(self, session_id, owner_key, event_id, instance_id, item_id, kind, *,
                position_ms=0, client_id=None, native=False, auto_advance=True):
        context = self.context_for(session_id, owner_key)
        before = self.store.snapshot(session_id, owner_key)
        # Native grants are never admitted through an HTTP/browser observation.
        if (before["mode"] == "vlc") != bool(native):
            return {"accepted": False, "duplicate": False, "logical_start": False,
                    "session": self._public_state(session_id, owner_key, before)}
        if kind == "interrupted":
            if self._active(before).get("item_id") != item_id:
                return {"accepted": False, "duplicate": False, "logical_start": False,
                        "session": self._public_state(session_id, owner_key, before)}
            result = self.interrupt_output(session_id, owner_key, instance_id, client_id, position_ms)
            return {**result, "duplicate": False, "logical_start": False,
                    "session": self._public_state(session_id, owner_key, result["session"])}
        result = self.coordinator.observe(session_id, owner_key, event_id, instance_id, item_id,
                                          kind, position_ms=position_ms, client_id=client_id)
        _trace(kind, session_id, {**result["session"], "duplicate": result.get("duplicate")},
               event_id=event_id, item_id=item_id, accepted=result["accepted"])
        if not result["accepted"] or result["duplicate"]:
            return {**result, "session": self._public_state(session_id, owner_key, result["session"])}
        state = result["session"]
        if isinstance(context.output, BrowserOutput):
            context.output.update(state)
        item = next((item for item in before["items"] if item["item_id"] == item_id), {})
        if kind == "started":
            self._activate(context, item, result.get("logical_start", False))
            if result.get("logical_start"):
                song = context.queue._db.get_song(item["song_id"]) or {}
                energy = song.get("energy")
                if type(energy) in (int, float) and math.isfinite(energy):
                    context.energy.record_energy(item["song_id"], energy)
                context.queue.on_song_started(item["song_id"], state["history_session_id"])
        elif item.get("kind") == "song":
            if kind == "completed":
                context.queue.record_completion()
                context.queue.on_song_ended(item["song_id"])
            elif kind == "hearted":
                context.queue.record_heart()
            elif kind == "skipped":
                # Store owns durable outcome/history. Queue owns only session
                # skip streak and mood adaptation; never call legacy record_skip.
                with context.lock:
                    context.queue._skip_streak = getattr(context.queue, "_skip_streak", 0) + 1
        if kind in ("completed", "skipped", "failed", "interrupted"):
            with context.lock:
                context.progress.clear()
            if kind == "completed" and item.get("kind") == "song":
                timer = context.preferences.get("sleep_timer")
                if timer and timer.get("end_after_current"):
                    state = self._finish_sleep(session_id, owner_key, context, expected=timer)
                    result["session"] = state
            if state["status"] == "idle":
                self._release_preparations(session_id, self._preparation_ids(state))
                if auto_advance and context.preferences["auto_advance"]:
                    self._schedule_next(session_id, owner_key, state)
        self._persist(session_id, owner_key)
        self._publish(session_id, "playback_state", state)
        return {**result, "session": self._public_state(session_id, owner_key, result["session"])}

    def _activate(self, context, item, logical_start):
        prep = item.get("preparation_id")
        with self._lock:
            record = self._prepared.get(prep)
            phase = item.get("kind")
            if record is None or phase in record["activated"] or phase == "song" and not logical_start:
                return
            record["activated"].add(phase)
            private = record["private"]
        parameters = inspect.signature(context.queue.activate_plan).parameters
        if "kind" in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
            context.queue.activate_plan(private, kind=phase)
        else:
            # Backward-compatible queues activate once at first actual start.
            with self._lock:
                first = len(record["activated"]) == 1
            if first:
                context.queue.activate_plan(private)

    def _native_observation(self, sid, owner, event):
        context = self.context_for(sid, owner)
        instance = event.get("instance_id")
        sequence = event.get("observation_seq")
        if sequence is not None:
            if type(sequence) is not int or sequence < 0:
                return {"accepted": False}
            with context.lock:
                if sequence <= context.native_sequences.get(instance, -1):
                    return {"accepted": False}
                context.native_sequences[instance] = sequence
        return self.observe(sid, owner, event.get("event_id") or uuid4().hex, instance,
                            event.get("item_id"), event.get("kind"),
                            position_ms=event.get("position_ms", 0), native=True)

    def progress(self, session_id, owner_key, instance_id, item_id, sequence, position_ms, *, client_id):
        self._position(position_ms)
        if type(sequence) is not int or sequence < 0:
            raise ValueError("A nonnegative integer progress sequence is required")
        context = self.context_for(session_id, owner_key)
        state = self.store.snapshot(session_id, owner_key)
        item = self._active(state)
        with context.lock:
            previous = context.progress
            accepted = bool(state["mode"] == "browser" and state.get("output_client_id") == client_id
                            and state["instance_id"] == instance_id and item.get("item_id") == item_id
                            and item.get("started") and not item.get("terminal")
                            and state["status"] in ("playing", "paused")
                            and sequence > max(state.get("position_seq", -1), previous.get("sequence", -1)))
            if not accepted:
                return {"accepted": False}
            now = self._clock()
            checkpoint = now - previous.get("checkpoint_at", float("-inf")) >= 5
            context.progress = {"instance_id": instance_id, "item_id": item_id, "sequence": sequence,
                                "position_ms": position_ms,
                                "checkpoint_at": now if checkpoint else previous["checkpoint_at"]}
            if checkpoint:
                result = self.store.checkpoint_position(session_id, owner_key, instance_id, item_id,
                                                         client_id, sequence, position_ms)
                if not result["accepted"]:
                    context.progress.clear()
                    return {"accepted": False}
            current = {**state, "position_ms": position_ms}
            if isinstance(context.output, BrowserOutput):
                context.output.update(current)
        return {"accepted": True}

    def _schedule_next(self, sid, owner, state):
        with self._lock:
            if self._closed or sid in self._jobs:
                return
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="session-next")
            def advance():
                try:
                    context = self.context_for(sid, owner)
                    current = self.store.snapshot(sid, owner)
                    if not context.closing and current["instance_id"] == state["instance_id"] and current["status"] == "idle":
                        self.next(sid, owner, uuid4().hex, current["revision"])
                except ConflictError:
                    pass
                except Exception:
                    logger.exception("Session automatic advance failed: session=%s", sid)
            job = self._executor.submit(advance)
            self._jobs[sid] = job
            job.add_done_callback(lambda done: self._finish_job(sid, done))

    def _finish_job(self, sid, job):
        with self._lock:
            if self._jobs.get(sid) is job:
                self._jobs.pop(sid, None)

    def renew_producer_lease(self, session_id, owner_key, instance_id, client_id):
        with self._locked_output(session_id, owner_key):
            result = self.store.renew_producer_lease(session_id, owner_key, instance_id, client_id)
            logger.debug("playback action=lease session=%s instance=%s", session_id, instance_id)
            return result

    def _source_expired(self, session_id, state):
        if state["mode"] != "browser" or self.store.producer_lease_remaining(state):
            return False
        # Older clients did not acquire a lease. They still need a live socket
        # quiesce; absence permits checkpoint recovery for that legacy grant.
        if "producer_lease_expires_at" in state:
            return True
        if self._producer_online is None:
            return False
        try:
            return not self._producer_online(session_id, state.get("output_client_id"))
        except Exception:
            logger.exception("Output liveness probe failed: session=%s", session_id)
            return False

    def cancel_handoff(self, session_id, owner_key, command_id, expected_revision,
                       target_mode, target_client_id=None):
        generated = uuid5(NAMESPACE_URL, f"radio:{session_id}:{command_id}:browser").hex
        client = self._client(target_client_id if target_client_id is not None else generated) if target_mode == "browser" else None
        with self._locked_output(session_id, owner_key) as (context, output):
            runtime = self.coordinator._runtime(session_id, owner_key)
            with runtime.lock:
                duplicate = self.store.handoff_snapshot(session_id, owner_key, command_id,
                                                        expected_revision, target_mode, client)
                if duplicate is not None:
                    return self._public_state(session_id, owner_key, duplicate)
                intent = runtime.handoff_intent
                request = (command_id, expected_revision, target_mode, client)
                if intent is None or intent["request"] != request:
                    raise ConflictError("No matching pending handoff")
                result = self.store.cancel_handoff(session_id, owner_key, *request)
                _trace("handoff_cancel", session_id, result, command_id=command_id, accepted=True)
                runtime.handoff_intent = None
                if context.handoff_timer is not None:
                    context.handoff_timer.cancel()
                    context.handoff_timer = None
                if isinstance(output, BrowserOutput):
                    output.cancel_quiesce(command_id)
                with self._lock:
                    if self._vlc_session == session_id and result["mode"] != "vlc":
                        self._vlc_session = None
                self._clear_targets(context)
            return self._public_state(session_id, owner_key, result)

    def _arm_handoff_deadline(self, sid, owner, context, request):
        if context.handoff_timer is not None:
            return
        def expire():
            try:
                self.cancel_handoff(sid, owner, *request)
            except (ConflictError, KeyError, PermissionError, RuntimeError):
                pass
        context.handoff_timer = self._handoff_timer_factory(15.0, expire)
        context.handoff_timer.daemon = True
        context.handoff_timer.start()

    def handoff(self, session_id, owner_key, command_id, expected_revision, target_mode, target_client_id=None):
        # A server-assigned client ID is stable for retries of this command,
        # including after restart; it is a scoped ID, never an owner capability.
        generated = uuid5(NAMESPACE_URL, f"radio:{session_id}:{command_id}:browser").hex
        client = self._client(target_client_id if target_client_id is not None else generated) if target_mode == "browser" else None
        if target_mode not in ("vlc", "browser"):
            raise ValueError("Valid playback output mode required")
        with self._locked_output(session_id, owner_key) as (context, previous):
            runtime = self.coordinator._runtime(session_id, owner_key)
            key, target = (command_id, target_mode), None
            with self._lock:
                prior_slot = self._vlc_session
            try:
                with self._lock:
                    if target_mode == "vlc" and self._vlc_session not in (None, session_id):
                        raise ConflictError("Desktop output is occupied by another listening session")
                    if target_mode == "vlc":
                        self._vlc_session = session_id
                    target = context.targets.get(key)
                    if target is None:
                        target = self._output(session_id, owner_key, target_mode, client)
                        context.targets[key] = target
                    elif isinstance(target, BrowserOutput) and client != target.client_id:
                        raise ConflictError("Output handoff command changed client")
                self._configure_output(context, target)
                result = self.coordinator.handoff_output(session_id, owner_key, command_id, expected_revision,
                                                         target_mode, client, target_transport=target,
                                                         source_expired=self._source_expired(session_id, self.store.snapshot(session_id, owner_key)))
                _trace("handoff", session_id, result, command_id=command_id, accepted=not result.get("cancelled"))
            except Exception:
                current = self.store.snapshot(session_id, owner_key)
                intent = runtime.handoff_intent
                if intent and intent["request"] == (command_id, expected_revision, target_mode, client):
                    self._arm_handoff_deadline(session_id, owner_key, context, intent["request"])
                pending = bool(intent and intent["epoch"] == runtime.epoch and
                               intent["request"][2] == "vlc" and intent["instance_id"] == current["instance_id"])
                with self._lock:
                    if self._vlc_session == session_id and current["mode"] != "vlc" and not pending:
                        self._vlc_session = prior_slot
                    if not pending and target is not runtime.transport:
                        context.targets.pop(key, None)
                if not pending and target is not None and target is not runtime.transport and not isinstance(target, BrowserOutput):
                    dispose = getattr(target, "dispose", None)
                    if dispose:
                        dispose()
                context.output = runtime.transport
                raise
            if result.get("awaiting_confirmation"):
                self._arm_handoff_deadline(session_id, owner_key, context,
                                           (command_id, expected_revision, target_mode, client))
            else:
                if context.handoff_timer is not None:
                    context.handoff_timer.cancel()
                    context.handoff_timer = None
                context.output = runtime.transport
                if result["mode"] == "browser":
                    with self._lock:
                        if self._vlc_session == session_id:
                            self._vlc_session = None
                with context.lock:
                    context.progress.clear()
                if previous is not context.output and not isinstance(previous, BrowserOutput):
                    dispose = getattr(previous, "dispose", None)
                    if dispose:
                        dispose()
                if not result.get("awaiting_output"):
                    self._clear_targets(context)
            self._persist(session_id, owner_key)
            return self._public_state(session_id, owner_key, result)

    @staticmethod
    def _configure_output(context, target):
        if isinstance(target, BrowserOutput):
            target._volume, target._eq = context.preferences["volume"], context.preferences["eq"]
        else:
            target.set_volume(context.preferences["volume"])
            target.set_eq_preset(context.preferences["eq"])
            target.set_normalization_enabled(context.preferences["normalization"])
            speech_volume = getattr(target, 'set_talk_volume', None)
            if callable(speech_volume):
                speech_volume(max(0, min(200, context.queue._db.get_setting_int('talk_volume', 100))))

    handoff_output = handoff

    def ack_quiesce(self, session_id, owner_key, instance_id, command_id, position_ms, *, client_id):
        self._position(position_ms)
        context = self.context_for(session_id, owner_key)
        state = self.store.snapshot(session_id, owner_key)
        if not isinstance(context.output, BrowserOutput) or state.get("output_client_id") != client_id:
            raise PermissionError("Current browser output grant required")
        return context.output.ack_quiesce(instance_id, command_id, position_ms)

    def interrupt_output(self, session_id, owner_key, instance_id, client_id, position_ms=None):
        """Disconnect only the matching browser lease, preserving logical work."""
        if position_ms is not None:
            self._position(position_ms)
        context = self.context_for(session_id, owner_key)
        runtime = self.coordinator._runtime(session_id, owner_key)
        with runtime.transport_lock:
            with runtime.lock:
                with context.lock:
                    state = self.store.snapshot(session_id, owner_key)
                    if self.store.producer_lease_remaining(state):
                        return {"accepted": False, "session": state}
                    active = self._active(state)
                    recent = context.progress
                    if (recent.get("instance_id") == instance_id and recent.get("item_id") == active.get("item_id")):
                        position_ms = recent["position_ms"]
                    result = self.store.interrupt_output(session_id, owner_key, instance_id, client_id, position_ms)
                    _trace("interrupt", session_id, result["session"], accepted=result["accepted"])
                    if not result["accepted"]:
                        return result
                    state = result["session"]
                    # Pending preparations and old observations become stale;
                    # the active reservation/attempt survives reactivation.
                    runtime.epoch += 1
                    runtime.instance_id = state["instance_id"]
                    runtime.handoff_intent = None
                    context.progress.clear()
                    context.port.bind_output(context.output, instance_id=state["instance_id"])
                    if isinstance(context.output, BrowserOutput):
                        context.output.update(state)
        self._persist(session_id, owner_key)
        self._publish(session_id, "playback_state", state)
        return result

    def resume_interrupted(self, session_id, owner_key, command_id, expected_revision, client_id=None):
        context = self.context_for(session_id, owner_key)
        state = self.store.snapshot(session_id, owner_key)
        generated = uuid5(NAMESPACE_URL, f"radio:{session_id}:{command_id}:resume").hex
        client = self._client(client_id if client_id is not None else generated) if state["mode"] == "browser" else None
        duplicate = self.store.command_snapshot(session_id, owner_key, command_id, "reactivate", expected_revision)
        if duplicate is not None and (duplicate["status"] != "awaiting_output" or
                self.store.command_instance(session_id, owner_key, command_id) != duplicate["instance_id"]):
            # Validate the complete receipt, including its original client ID,
            # before returning a retry without rebinding the current adapter.
            retried = self.store.reactivate(session_id, owner_key, command_id, expected_revision, client)
            return self._public_state(session_id, owner_key, retried)
        key = (command_id, "reactivate")
        with context.lock:
            target = context.targets.get(key)
            if target is None:
                target = self._output(session_id, owner_key, state["mode"], client)
                context.targets[key] = target
        runtime = self.coordinator._runtime(session_id, owner_key)
        with runtime.transport_lock:
            self._configure_output(context, target)
            previous = context.output
            result = self.coordinator.reactivate_output(session_id, owner_key, command_id, expected_revision,
                                                         client, target_transport=target)
            context.output = runtime.transport
            if previous is not context.output and not isinstance(previous, BrowserOutput):
                dispose = getattr(previous, "dispose", None)
                if dispose:
                    dispose()
            if not result.get("awaiting_output"):
                self._clear_targets(context)
        return self._public_state(session_id, owner_key, result)

    @staticmethod
    def _position(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Position must be finite and nonnegative")

    def pause(self, session_id, owner_key):
        with self._locked_output(session_id, owner_key) as (context, output):
            output.pause()
        state = self.snapshot(session_id, owner_key)
        _trace("pause", session_id, state)
        return state

    def resume(self, session_id, owner_key):
        with self._locked_output(session_id, owner_key) as (context, output):
            output.resume()
        state = self.snapshot(session_id, owner_key)
        _trace("resume", session_id, state)
        return state

    def seek(self, session_id, owner_key, position_ms):
        self._position(position_ms)
        with self._locked_output(session_id, owner_key) as (context, output):
            output.seek(position_ms)
        state = self.snapshot(session_id, owner_key)
        _trace("seek", session_id, state)
        return state

    def set_volume(self, session_id, owner_key, volume):
        if type(volume) is not int or not 0 <= volume <= 100:
            raise ValueError("Volume must be an integer from 0 to 100")
        with self._locked_output(session_id, owner_key) as (context, output):
            output.set_volume(volume)
            context.preferences["volume"] = volume
            self._persist(session_id, owner_key)
        return self.snapshot(session_id, owner_key)

    def set_eq(self, session_id, owner_key, preset):
        if not isinstance(preset, str) or preset not in allowed_presets():
            raise ValueError("A supported EQ preset is required")
        with self._locked_output(session_id, owner_key) as (context, output):
            output.set_eq_preset(preset)
            context.preferences["eq"] = preset
            self._persist(session_id, owner_key)
        return self.snapshot(session_id, owner_key)

    def set_normalization(self, session_id, owner_key, enabled):
        if type(enabled) is not bool:
            raise ValueError("Normalization requires a boolean")
        with self._locked_output(session_id, owner_key) as (context, output):
            output.set_normalization_enabled(enabled)
            context.preferences["normalization"] = enabled
            self._persist(session_id, owner_key)
        return self.snapshot(session_id, owner_key)

    def get_sleep_timer(self, session_id, owner_key):
        context = self.context_for(session_id, owner_key)
        with context.lock:
            timer = deepcopy(context.preferences.get("sleep_timer"))
        if timer:
            timer["active"] = True
            timer["remaining_seconds"] = None if timer.get("end_after_current") else max(0, timer["expires_at"] - self._wall_clock())
        return timer

    def set_sleep_timer(self, session_id, owner_key, minutes, fade_seconds=10, switch_to_chill=True):
        if type(minutes) not in (int, float) or not math.isfinite(minutes) or not 0 <= minutes <= 1440:
            raise ValueError("Timer minutes must be between zero and 1440")
        if type(fade_seconds) not in (int, float) or not math.isfinite(fade_seconds) or not 0 <= fade_seconds <= 30:
            raise ValueError("Timer fade must be between 0 and 30 seconds")
        if type(switch_to_chill) is not bool:
            raise ValueError("Timer chill switch requires a boolean")
        context = self.context_for(session_id, owner_key)
        self._cancel_timer_worker(context)
        with context.lock:
            context.preferences["sleep_timer"] = {"expires_at": self._wall_clock() + minutes * 60 if minutes else None,
                "end_after_current": minutes == 0, "total_seconds": minutes * 60,
                "fade_seconds": min(minutes * 60, fade_seconds),
                "switch_to_chill": switch_to_chill, "chill_switched": False}
        self._persist(session_id, owner_key)
        self._arm_timer(session_id, owner_key, context)
        return self.get_sleep_timer(session_id, owner_key)

    @staticmethod
    def _cancel_timer_worker(context):
        with context.lock:
            if context.timer_cancel is not None:
                context.timer_cancel.set()
            if context.timer is not None:
                context.timer.cancel()
            context.timer, context.timer_cancel = None, None

    def cancel_sleep_timer(self, session_id, owner_key):
        with self._locked_output(session_id, owner_key) as (context, output):
            with context.lock:
                self._cancel_timer_worker(context)
                context.preferences["sleep_timer"] = None
                self._persist(session_id, owner_key)
                self._configure_output(context, output)
                if isinstance(output, BrowserOutput):
                    output.set_volume(context.preferences["volume"])
        return None

    def _arm_timer(self, sid, owner, context):
        with self._locked_output(sid, owner) as (context, output):
            self._cancel_timer_worker(context)
            with context.lock:
                settings = deepcopy(context.preferences["sleep_timer"])
                if settings.get("end_after_current"):
                    return
                remaining = settings["expires_at"] - self._wall_clock()
                if settings.get("switch_to_chill") and not settings.get("chill_switched") and remaining <= 600:
                    self.set_mood(sid, owner, "chill")
                    settings["chill_switched"] = True
                    context.preferences["sleep_timer"]["chill_switched"] = True
                cancel = threading.Event()
                wake_before = settings["fade_seconds"]
                if settings.get("switch_to_chill") and not settings.get("chill_switched"):
                    wake_before = max(wake_before, 600)
                delay = max(0, remaining - wake_before)
                timer = self._timer_factory(delay, self._run_sleep, args=(sid, owner, settings, cancel))
                timer.daemon = True
                context.timer, context.timer_cancel = timer, cancel
        timer.start()

    def _run_sleep(self, sid, owner, settings, cancel):
        try:
            context = self.context_for(sid, owner)
            fade = settings["fade_seconds"]
            while not cancel.is_set():
                remaining = settings["expires_at"] - self._wall_clock()
                with self._locked_output(sid, owner) as (context, output):
                    with context.lock:
                        if context.timer_cancel is not cancel or cancel.is_set():
                            return
                        if settings.get("switch_to_chill") and not settings.get("chill_switched") and remaining <= 600:
                            self.set_mood(sid, owner, "chill")
                            settings["chill_switched"] = True
                            context.preferences["sleep_timer"]["chill_switched"] = True
                        if fade and remaining > 0:
                            generation = context.port.generation
                            volume = round(context.preferences["volume"] * min(1, remaining / fade))
                            context.port.dispatch(generation, "set_volume", volume)
                if remaining <= 0:
                    break
                if cancel.wait(min(0.25, remaining)):
                    return
            if cancel.is_set():
                return
            self._finish_sleep(sid, owner, context, expected_cancel=cancel)
        except (ConflictError, KeyError):
            pass
        except Exception:
            logger.exception("Session sleep timer failed: session=%s", sid)

    def _finish_sleep(self, sid, owner, context, *, expected=None, expected_cancel=None):
        """Stop the whole current output and restore volume without resuming."""
        with self._locked_output(sid, owner) as (context, output):
            with context.lock:
                if ((expected is not None and context.preferences.get("sleep_timer") is not expected)
                        or (expected_cancel is not None and context.timer_cancel is not expected_cancel)):
                    return self.store.snapshot(sid, owner)
                state = self.store.snapshot(sid, owner)
                self.stop(sid, owner, uuid4().hex, state["revision"])
                context.preferences["sleep_timer"] = None
                context.timer, context.timer_cancel = None, None
                self._configure_output(context, output)
                if isinstance(output, BrowserOutput):
                    output.set_volume(context.preferences["volume"])
            self._persist(sid, owner)
            self._publish(sid, "sleep_timer_expired", {})
            return self.store.snapshot(sid, owner)

    def set_mood(self, session_id, owner_key, mood):
        context = self.context_for(session_id, owner_key)
        if not context.mood.set_state(mood):
            raise ValueError("Unknown mood")
        self._persist(session_id, owner_key)
        context.queue.request_rebuild()
        return self.snapshot(session_id, owner_key)

    def set_vibe(self, session_id, owner_key, song_id=None, *, embedding=None, audio_embedding=None):
        context = self.context_for(session_id, owner_key)
        if song_id is None:
            context.mood.deactivate_vibe_lock()
        else:
            if embedding is None:
                from engine import retriever
                result = retriever.get_chroma_collection("songs").get(ids=[str(song_id)], include=["embeddings"])
                vectors = result.get("embeddings")
                if vectors is None or len(vectors) == 0:
                    raise ValueError("Song embedding is unavailable")
                embedding = vectors[0]
            context.mood.activate_vibe_lock(song_id, embedding, audio_embedding)
        self._persist(session_id, owner_key)
        context.queue.request_rebuild()
        return self.snapshot(session_id, owner_key)

    def queue_add(self, session_id, owner_key, song_ids, position="back"):
        if not isinstance(song_ids, list) or len(song_ids) > 100 or any(type(sid) is not int or sid <= 0 for sid in song_ids):
            raise ValueError("A bounded list of positive song IDs is required")
        context = self.context_for(session_id, owner_key)
        context.queue.add_manual(song_ids, position=position)
        self._persist(session_id, owner_key)
        return self.snapshot(session_id, owner_key)

    def queue_remove(self, session_id, owner_key, song_id):
        self.context_for(session_id, owner_key).queue.remove_manual(song_id)
        self._persist(session_id, owner_key)
        return self.snapshot(session_id, owner_key)

    def queue_clear(self, session_id, owner_key):
        self.context_for(session_id, owner_key).queue.clear_queue()
        self._persist(session_id, owner_key)
        return self.snapshot(session_id, owner_key)

    def queue_reorder(self, session_id, owner_key, order):
        self.context_for(session_id, owner_key).queue.reorder_display_queue(order)
        self._persist(session_id, owner_key)
        return self.snapshot(session_id, owner_key)

    def close(self, session_id, owner_key, command_id=None, expected_revision=None):
        context = self.context_for(session_id, owner_key)
        state = self.store.snapshot(session_id, owner_key)
        self._persist(session_id, owner_key)
        result = self.coordinator.close_session(session_id, owner_key, command_id or uuid4().hex,
                                                state["revision"] if expected_revision is None else expected_revision)
        context.closing = True
        if context.handoff_timer is not None:
            context.handoff_timer.cancel()
        self._cancel_timer_worker(context)
        self._clear_targets(context)
        self._release_preparations(session_id, self._preparation_ids(state))
        dispose = getattr(context.output, "dispose", None)
        if dispose:
            dispose()
        with self._lock:
            self._contexts.pop(session_id, None)
            self._owners.pop(session_id, None)
            if self._vlc_session == session_id:
                self._vlc_session = None
            job = self._jobs.pop(session_id, None)
            if job is not None:
                job.cancel()
        return result

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            contexts = list(self._contexts.items())
            owners = {sid: self._owners[sid] for sid, context in contexts}
            for sid, context in contexts:
                context.closing = True
            executor = self._executor
            jobs = list(self._jobs.values())
        # Timer workers may hold a context lock while checking admission. Never
        # wait for them (or run cancellation callbacks) under the registry lock.
        leases = [(sid, self._preparation_ids(self.store.snapshot(sid, owners[sid])))
                  for sid, context in contexts]
        self.coordinator.shutdown()
        retained = {sid: self._preparation_ids(self.store.snapshot(sid, owners[sid]))
                    for sid, context in contexts
                    if self.store.snapshot(sid, owners[sid])["status"] == "interrupted"}
        for sid, context in contexts:
            if context.handoff_timer is not None:
                context.handoff_timer.cancel()
            self._cancel_timer_worker(context)
            self._clear_targets(context)
        for job in jobs:
            job.cancel()
        for sid, context in contexts:
            dispose = getattr(context.output, "dispose", None)
            if dispose:
                dispose()
        for sid, ids in leases:
            self._release_preparations(sid, ids - retained.get(sid, set()))
        # Include completed plans and in-flight preparation results when their
        # operation unwinds; each lease has its own preparation-scoped owner.
        with self._lock:
            prepared = list(self._prepared.items())
        for prep, record in prepared:
            sid = record["lease"].split(":", 1)[0]
            if prep in retained.get(sid, set()):
                with self._lock:
                    self._prepared.pop(prep, None)
            else:
                self._release_preparations(sid, [prep])
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
