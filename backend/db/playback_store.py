"""Transactional playback receipts and session state, independent of audio output.

Schema installation is additive and preceded by a verified SQLite backup.
This service is not enabled by legacy startup until coordinator integration.
"""
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

from db import database

_schema_lock = threading.Lock()
SCHEMA = """
CREATE TABLE playback_sessions (
    id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, mode TEXT NOT NULL,
    history_session_id INTEGER NOT NULL REFERENCES sessions(id),
    revision INTEGER NOT NULL DEFAULT 0, state_json TEXT NOT NULL
);
CREATE TABLE playback_attempts (
    id TEXT PRIMARY KEY, song_id INTEGER NOT NULL REFERENCES songs(id),
    history_session_id INTEGER NOT NULL REFERENCES sessions(id),
    started INTEGER NOT NULL DEFAULT 0, outcome TEXT, flags_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE playback_receipts (
    session_id TEXT NOT NULL REFERENCES playback_sessions(id), event_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL, result_json TEXT NOT NULL,
    PRIMARY KEY(session_id, event_id)
);
"""


class ConflictError(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PlaybackStore:
    def __init__(self):
        self._wall_clock = time.time
        with _schema_lock, database.get_connection_cm() as conn:
            existing = conn.execute("SELECT 1 FROM sqlite_master WHERE name='playback_sessions'").fetchone()
            if existing:
                return
            backup_dir = Path(database.DB_PATH).parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f"radio-before-playback-{uuid4().hex}.db"
            with sqlite3.connect(str(backup)) as target:
                conn.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Playback migration backup failed verification")
            try:
                conn.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
            except Exception:
                conn.rollback()
                raise

    @contextmanager
    def _transaction(self):
        with database.get_connection_cm() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _load(self, conn, session_id, owner):
        row = conn.execute("SELECT * FROM playback_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError("Playback session not found")
        if not isinstance(owner, str) or not hmac.compare_digest(row["owner_hash"], _hash(owner)):
            raise PermissionError("Playback session owner required")
        state = json.loads(row["state_json"])
        state.update(id=row["id"], mode=row["mode"], revision=row["revision"],
                     history_session_id=row["history_session_id"])
        return state

    def _save(self, conn, state):
        state["revision"] += 1
        conn.execute("UPDATE playback_sessions SET revision=?, mode=?, state_json=? WHERE id=?",
                     (state["revision"], state["mode"], _json(state), state["id"]))

    def _replay(self, conn, session_id, event_id, payload):
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
            raise ValueError("An event ID of 1 to 128 characters is required")
        receipt = conn.execute("SELECT * FROM playback_receipts WHERE session_id=? AND event_id=?",
                               (session_id, event_id)).fetchone()
        if receipt:
            if receipt["payload_hash"] != _hash(_json(payload)):
                raise ConflictError("Event ID reused with different content")
            stored = json.loads(receipt["result_json"])
            return stored.get("result", stored)
        return None

    def _receipt(self, conn, session_id, event_id, payload, result):
        conn.execute("INSERT INTO playback_receipts VALUES (?, ?, ?, ?)",
                     (session_id, event_id, _hash(_json(payload)), _json({"payload": payload, "result": result})))

    def command_snapshot(self, session_id, owner, command_id, operation, expected_revision, *, intent=None):
        """Check a retried command before doing preparation or transport work."""
        with database.get_connection_cm() as conn:
            state = self._load(conn, session_id, owner)
            row = conn.execute("SELECT result_json FROM playback_receipts WHERE session_id=? AND event_id=?",
                               (session_id, command_id)).fetchone()
            if row is None:
                return None
            payload = json.loads(row["result_json"])["payload"]
            if (payload.get("kind") != operation or payload.get("revision") != expected_revision
                    or payload.get("intent") != intent):
                raise ConflictError("Command ID reused with different content")
            return {**state, "duplicate": True}

    def command_instance(self, session_id, owner, command_id):
        """Read the immutable output grant produced by an accepted command.

        A duplicate command returns today's session snapshot elsewhere. Output
        retries additionally need its original grant to avoid restarting a newer
        command's awaiting output. Ownership is checked even for missing IDs.
        """
        with database.get_connection_cm() as conn:
            self._load(conn, session_id, owner)
            if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
                raise ValueError("A command ID of 1 to 128 characters is required")
            row = conn.execute("SELECT result_json FROM playback_receipts WHERE session_id=? AND event_id=?",
                               (session_id, command_id)).fetchone()
            if row is None:
                return None
            stored = json.loads(row["result_json"])
            return stored.get("result", stored).get("instance_id")

    @staticmethod
    def _handoff_payload(command_revision, target_mode, target_client_id):
        if target_mode not in ("browser", "vlc"):
            raise ValueError("Valid playback output mode required")
        if target_mode == "browser" and (not isinstance(target_client_id, str) or not target_client_id):
            raise ValueError("Browser output client required")
        if target_client_id is not None and (not isinstance(target_client_id, str) or not target_client_id):
            raise ValueError("Valid output client required")
        return {"kind": "handoff_output", "revision": command_revision,
                "target_mode": target_mode, "target_client_id": target_client_id}

    def handoff_snapshot(self, session_id, owner, command_id, expected_revision,
                         target_mode, target_client_id):
        payload = self._handoff_payload(expected_revision, target_mode, target_client_id)
        with database.get_connection_cm() as conn:
            state = self._load(conn, session_id, owner)
            receipt = self._replay(conn, session_id, command_id, payload)
            if receipt is not None:
                return {**state, "duplicate": True, **({"cancelled": True} if receipt.get("cancelled") else {})}
            return None

    def producer_lease_remaining(self, state):
        return max(0, int((state.get("producer_lease_expires_at", 0) - self._wall_clock()) * 1000))

    def renew_producer_lease(self, session_id, owner, instance_id, client_id):
        """Persist a bounded producer permit without changing command revision."""
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if (state["mode"] != "browser" or state["instance_id"] != instance_id
                    or state.get("output_client_id") != client_id
                    or state["status"] not in ("ready", "playing", "paused")):
                raise ConflictError("Browser output grant changed")
            state["producer_lease_expires_at"] = self._wall_clock() + 12
            conn.execute("UPDATE playback_sessions SET state_json=? WHERE id=?", (_json(state), session_id))
            return {"instance_id": instance_id, "client_id": client_id, "lease_remaining_ms": 12000}

    def cancel_handoff(self, session_id, owner, command_id, expected_revision, target_mode, target_client_id):
        payload = self._handoff_payload(expected_revision, target_mode, target_client_id)
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            receipt = self._replay(conn, session_id, command_id, payload)
            if receipt is not None:
                return {**state, "duplicate": True, **({"cancelled": True} if receipt.get("cancelled") else {})}
            result = {**state, "cancelled": True}
            self._receipt(conn, session_id, command_id, payload, result)
            return result

    def create_session(self, owner, mode, *, output_client_id=None):
        if not isinstance(owner, str) or not owner or mode not in ("browser", "vlc"):
            raise ValueError("Valid owner and playback mode required")
        with self._transaction() as conn:
            history = conn.execute("INSERT INTO sessions(initial_mood) VALUES ('neutral')").lastrowid
            state = {"id": uuid4().hex, "mode": mode, "revision": 0,
                     "history_session_id": history, "status": "idle", "instance_id": None,
                     "items": [], "active_index": 0, "position_ms": 0, "output_client_id": output_client_id}
            conn.execute("INSERT INTO playback_sessions VALUES (?, ?, ?, ?, ?, ?)",
                         (state["id"], _hash(owner), mode, history, 0, _json(state)))
            return state

    def snapshot(self, session_id, owner):
        with database.get_connection_cm() as conn:
            return self._load(conn, session_id, owner)

    def begin_history_session(self, session_id, owner):
        """Rebind stopped listening history while retaining session authority."""
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            history = conn.execute("SELECT ended_at FROM sessions WHERE id=?",
                                   (state["history_session_id"],)).fetchone()
            if history is None:
                raise ConflictError("Listening history session is missing")
            if history["ended_at"] is None:
                return state
            if state["status"] not in ("idle", "stopped"):
                raise ConflictError("Active playback cannot change its listening history")
            state["history_session_id"] = conn.execute(
                "INSERT INTO sessions(initial_mood) VALUES ('neutral')").lastrowid
            conn.execute("UPDATE playback_sessions SET history_session_id=?, state_json=? WHERE id=?",
                         (state["history_session_id"], _json(state), session_id))
            return state

    def save_context(self, session_id, owner, context):
        """Save bounded listening context without advancing playback commands."""
        if not isinstance(context, dict):
            raise ValueError("Listening context must be an object")
        forbidden = {"owner", "owner_hash", "authorization", "password", "secret",
                     "token", "api_key", "apikey", "credential", "credentials", "capability"}
        pending, nodes, text_bytes = [(context, 0)], 0, 0
        while pending:
            value, depth = pending.pop()
            nodes += 1
            if depth > 16 or nodes > 65536:
                raise ValueError("Listening context exceeds structural limits")
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str) or key.lower().replace("-", "_") in forbidden:
                        raise ValueError("Credentials cannot be stored in listening context")
                    text_bytes += len(key.encode("utf-8"))
                    pending.append((child, depth + 1))
            elif isinstance(value, (list, tuple)):
                pending.extend((child, depth + 1) for child in value)
            elif isinstance(value, str):
                text_bytes += len(value.encode("utf-8"))
            if text_bytes > 1048576:
                raise ValueError("Listening context exceeds one MiB")
        try:
            serialized = _json(context)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Listening context must contain finite JSON values") from exc
        if len(serialized.encode("utf-8")) > 1048576:
            raise ValueError("Listening context exceeds one MiB")
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            state["context"] = json.loads(serialized)
            state["context_revision"] = state.get("context_revision", 0) + 1
            conn.execute("UPDATE playback_sessions SET state_json=? WHERE id=?", (_json(state), session_id))
            return state

    def checkpoint_position(self, session_id, owner, instance_id, item_id, client_id, sequence, position_ms):
        """Persist current progress without receipts, history, or command revision."""
        if type(sequence) is not int or sequence < 0:
            raise ValueError("A nonnegative integer progress sequence is required")
        if type(position_ms) not in (int, float) or not math.isfinite(position_ms) or position_ms < 0:
            raise ValueError("Position must be finite and nonnegative")
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            index = state["active_index"]
            current = state["items"][index] if index < len(state["items"]) else None
            accepted = bool(current and current["item_id"] == item_id and current["started"]
                            and not current["terminal"] and state["status"] in ("playing", "paused")
                            and state["instance_id"] == instance_id
                            and state.get("output_client_id") == client_id
                            and sequence > state.get("position_seq", -1))
            if accepted:
                state.update(position_ms=position_ms, position_seq=sequence)
                conn.execute("UPDATE playback_sessions SET state_json=? WHERE id=?", (_json(state), session_id))
            return {"accepted": accepted, "session": state}

    def reactivate(self, session_id, owner, command_id, expected_revision, client_id):
        """Explicitly regrant interrupted media while retaining its logical attempt."""
        payload = {"kind": "reactivate", "revision": expected_revision, "client_id": client_id}
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if self._replay(conn, session_id, command_id, payload) is not None:
                return {**state, "duplicate": True}
            self._handoff_payload(expected_revision, state["mode"], client_id)
            if state["revision"] != expected_revision:
                raise ConflictError("Playback revision changed")
            if state["status"] not in ("interrupted", "awaiting_output"):
                raise ConflictError("Playback session does not need reactivation")
            if self.producer_lease_remaining(state):
                raise ConflictError("Previous browser producer lease has not expired")
            remaining = [{**item, "started": False} for item in state["items"][state["active_index"]:]]
            if not remaining:
                raise ConflictError("No media remains to reactivate")
            state.update(instance_id=uuid4().hex, output_client_id=client_id, items=remaining,
                         active_index=0, status="paused" if state.get("desired_status") == "paused" else "ready",
                         desired_status=state.get("desired_status", "playing"), position_seq=-1,
                         producer_lease_expires_at=0)
            self._save(conn, state)
            self._receipt(conn, session_id, command_id, payload, state)
            return {**state, "duplicate": False}

    def prepare(self, session_id, owner, command_id, expected_revision, items, *, intent=None):
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError("A bounded nonempty media plan is required")
        payload = {"kind": "prepare", "revision": expected_revision, "items": items}
        if intent is not None:
            if (not isinstance(intent, dict) or set(intent) - {"operation", "song_id", "type"}
                    or any(isinstance(value, (dict, list, tuple)) for value in intent.values())):
                raise ValueError("Preparation intent must contain explicit operation/song/type identity")
            try:
                encoded_intent = _json(intent)
            except (TypeError, ValueError) as exc:
                raise ValueError("Preparation intent must contain finite JSON values") from exc
            if len(encoded_intent.encode("utf-8")) > 4096:
                raise ValueError("Preparation intent is too large")
            payload["intent"] = json.loads(encoded_intent)
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            replay = self._replay(conn, session_id, command_id, payload)
            if replay is not None:
                return {**state, "duplicate": True}
            if state["revision"] != expected_revision:
                raise ConflictError("Playback revision changed")
            prepared = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Media items must be objects")
                if item.get("kind") not in ("song", "speech"):
                    raise ValueError("Unknown media item kind")
                duration = item.get("duration_ms", 0)
                if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                    raise ValueError("Media duration must be finite and nonnegative")
                entry = {**item, "item_id": uuid4().hex, "started": False, "terminal": False}
                if item["kind"] == "song":
                    song_id = item.get("song_id")
                    if type(song_id) is not int or not conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone():
                        raise ValueError("Unknown song")
                    attempt = uuid4().hex
                    conn.execute("INSERT INTO playback_attempts(id,song_id,history_session_id) VALUES (?,?,?)",
                                 (attempt, song_id, state["history_session_id"]))
                    entry["attempt_id"] = attempt
                elif not isinstance(item.get("resource_id"), str) or not item["resource_id"]:
                    raise ValueError("Prepared speech resource required")
                prepared.append(entry)
            for previous in state["items"]:
                if previous.get("attempt_id"):
                    conn.execute("UPDATE playback_attempts SET outcome='interrupted' WHERE id=? AND outcome IS NULL",
                                 (previous["attempt_id"],))
            state.update(instance_id=uuid4().hex, items=prepared, active_index=0, position_ms=0,
                         position_seq=-1, status="ready", desired_status="playing")
            self._save(conn, state)
            self._receipt(conn, session_id, command_id, payload, state)
            return {**state, "duplicate": False}

    def handoff_output(self, session_id, owner, command_id, expected_revision,
                       target_mode, target_client_id, *, position_ms, observed_revision=None, desired_status=None,
                       source_expired=False):
        """Change only a listening session's output, retaining logical history."""
        if type(position_ms) not in (int, float) or not math.isfinite(position_ms) or position_ms < 0:
            raise ValueError("Position must be finite and nonnegative")
        payload = self._handoff_payload(expected_revision, target_mode, target_client_id)
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if self._replay(conn, session_id, command_id, payload) is not None:
                return {**state, "duplicate": True}
            if state["revision"] != (expected_revision if observed_revision is None else observed_revision):
                raise ConflictError("Playback revision changed")
            if source_expired and self.producer_lease_remaining(state):
                raise ConflictError("Previous browser producer lease has not expired")
            desired = desired_status or ("paused" if state["status"] == "paused" else state.get("desired_status", "playing"))
            if desired not in ("paused", "playing"):
                raise ValueError("Output intent must be playing or paused")
            remaining = [{**item, "started": False} for item in state["items"][state["active_index"]:]]
            state.update(mode=target_mode, output_client_id=target_client_id,
                         instance_id=uuid4().hex, items=remaining, active_index=0,
                         status="awaiting_output" if remaining else "idle",
                         desired_status=desired,
                         producer_lease_expires_at=0,
                         position_ms=position_ms if remaining else 0, position_seq=-1)
            self._save(conn, state)
            self._receipt(conn, session_id, command_id, payload, state)
            return {**state, "duplicate": False}

    def output_ready(self, session_id, owner, instance_id):
        """An adapter accepted the already durable output plan."""
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if state["instance_id"] == instance_id and state["status"] == "awaiting_output":
                state["status"] = "paused" if state.get("desired_status") == "paused" else "ready"
                self._save(conn, state)
            return state

    def output_failed(self, session_id, owner, instance_id):
        """Retain an admitted plan after its physical output fails to start."""
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if state["instance_id"] == instance_id and state["items"] and state["status"] != "awaiting_output":
                state["status"] = "awaiting_output"
                current = state["items"][state["active_index"]:]
                if current:
                    current[0]["started"] = False
                self._save(conn, state)
            return state

    def interrupt_output(self, session_id, owner, instance_id, client_id, position_ms=None):
        """Retire one vanished browser grant without terminalizing its attempt."""
        if position_ms is not None and (type(position_ms) not in (int, float) or not math.isfinite(position_ms) or position_ms < 0):
            raise ValueError("Position must be finite and nonnegative")
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            index = state["active_index"]
            current = state["items"][index] if index < len(state["items"]) else None
            accepted = bool(current and not current["terminal"] and state["mode"] == "browser"
                            and state["status"] in ("ready", "playing", "paused")
                            and state["instance_id"] == instance_id and state.get("output_client_id") == client_id)
            if accepted:
                desired = "paused" if state["status"] == "paused" else state.get("desired_status", "playing")
                state.update(status="interrupted", desired_status=desired, instance_id=uuid4().hex, position_seq=-1)
                if position_ms is not None:
                    state["position_ms"] = position_ms
                for item in state["items"][index:]:
                    item["started"] = False
                self._save(conn, state)
            return {"accepted": accepted, "session": state}

    def interrupt_session(self, session_id, owner, expected_instance=None, *, position_ms=None):
        """Retain a logical attempt while retiring its output during shutdown."""
        if position_ms is not None and (type(position_ms) not in (int, float)
                or not math.isfinite(position_ms) or position_ms < 0):
            raise ValueError("Position must be finite and nonnegative")
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            index = state["active_index"]
            current = state["items"][index] if index < len(state["items"]) else None
            accepted = bool(current and not current["terminal"] and
                state["status"] in ("ready", "playing", "paused", "awaiting_output") and
                (expected_instance is None or state["instance_id"] == expected_instance))
            if accepted:
                desired = "paused" if state["status"] == "paused" else state.get("desired_status", "playing")
                state.update(status="interrupted", desired_status=desired, instance_id=uuid4().hex, position_seq=-1)
                if position_ms is not None:
                    state["position_ms"] = position_ms
                for item in state["items"][index:]:
                    item["started"] = False
                self._save(conn, state)
            return {"accepted": accepted, "session": state}

    def recover_interrupted(self):
        """Retire pre-restart identities; never manufacture a completion or skip."""
        affected = []
        with self._transaction() as conn:
            for row in conn.execute("SELECT * FROM playback_sessions").fetchall():
                state = json.loads(row["state_json"])
                if state["status"] not in ("ready", "playing", "paused"):
                    continue
                state["status"] = "interrupted"
                state["instance_id"] = uuid4().hex
                state["position_seq"] = -1
                for item in state["items"][state["active_index"]:]:
                    item["started"] = False
                self._save(conn, state)
                affected.append(state["id"])
        return affected

    def stop(self, session_id, owner, command_id, expected_revision):
        payload = {"kind": "stop", "revision": expected_revision}
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if self._replay(conn, session_id, command_id, payload) is not None:
                return {**state, "duplicate": True}
            if state["revision"] != expected_revision:
                raise ConflictError("Playback revision changed")
            for item in state["items"]:
                if item.get("attempt_id"):
                    conn.execute("UPDATE playback_attempts SET outcome='interrupted' WHERE id=? AND outcome IS NULL",
                                 (item["attempt_id"],))
            state.update(status="stopped", instance_id=None, items=[], active_index=0, position_ms=0)
            self._save(conn, state)
            self._receipt(conn, session_id, command_id, payload, state)
            return {**state, "duplicate": False}

    def replay(self, session_id, owner, command_id, expected_revision):
        """Record one explicit replay while retaining the current attempt and plan."""
        payload = {"kind": "replay", "revision": expected_revision}
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if self._replay(conn, session_id, command_id, payload) is not None:
                return {**state, "duplicate": True}
            if state["revision"] != expected_revision:
                raise ConflictError("Playback revision changed")
            index = state.get("active_index", 0)
            items = state.get("items", [])
            item = items[index] if index < len(items) else None
            if (state.get("status") not in ("playing", "paused") or not item or
                    item.get("kind") != "song" or not item.get("started") or item.get("terminal") or
                    not item.get("attempt_id")):
                raise ConflictError("There is no started song to replay")
            attempt = conn.execute("SELECT * FROM playback_attempts WHERE id=?", (item["attempt_id"],)).fetchone()
            if attempt is None or attempt["outcome"] is not None:
                raise ConflictError("The current playback attempt cannot be replayed")
            database.insert_play_event(conn, item["song_id"], attempt["history_session_id"], "replayed")
            feedback_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE songs SET replay_count=replay_count+1 WHERE id=?", (item["song_id"],))
            flags = json.loads(attempt["flags_json"])
            flags.append({"kind": "replayed", "event_id": feedback_id, "command_id": command_id})
            conn.execute("UPDATE playback_attempts SET flags_json=? WHERE id=?",
                         (_json(flags), attempt["id"]))
            state["position_ms"] = 0
            self._save(conn, state)
            self._receipt(conn, session_id, command_id, payload, state)
            return {**state, "duplicate": False}

    def observe(self, session_id, owner, event_id, instance_id, item_id, kind, *, position_ms=0, client_id=None):
        if kind not in ("started", "paused", "resumed", "completed", "skipped", "failed", "interrupted", "hearted", "thumbed_down", "replayed"):
            raise ValueError("Unknown playback observation")
        if type(position_ms) not in (int, float) or not math.isfinite(position_ms) or position_ms < 0:
            raise ValueError("Position must be finite and nonnegative")
        payload = {"kind": kind, "instance": instance_id, "item": item_id, "position": position_ms, "client_id": client_id}
        with self._transaction() as conn:
            state = self._load(conn, session_id, owner)
            if (state["instance_id"] != instance_id or
                    state.get("output_client_id") is not None and state["output_client_id"] != client_id):
                return {"accepted": False, "duplicate": False, "logical_start": False, "session": state}
            replay = self._replay(conn, session_id, event_id, payload)
            if replay is not None:
                return {**replay, "session": state, "duplicate": True}
            index = state["active_index"]
            current = state["items"][index] if index < len(state["items"]) else None
            # A synchronous output adapter can report actual start before its
            # start() call returns. That proof may activate an awaiting plan.
            accepted = bool(current and (state["status"] in ("ready", "playing", "paused") or
                                         state["status"] == "awaiting_output" and kind == "started") and
                            state["instance_id"] == instance_id and current["item_id"] == item_id and not current["terminal"])
            logical_start = False
            target_status = {'paused': 'paused', 'resumed': 'playing'}.get(kind)
            if accepted and current.get('started') and target_status == state['status']:
                # Browsers can emit playing again after buffering or seeking.
                # A repeated physical state is a successful no-op, not a lost
                # grant or a reason for the output to interrupt itself.
                result = {'accepted': True, 'duplicate': True, 'logical_start': False, 'session': state}
                self._receipt(conn, session_id, event_id, payload, result)
                return result
            if accepted and kind == "started" and current.get("attempt_id"):
                attempt = conn.execute("SELECT started FROM playback_attempts WHERE id=?", (current["attempt_id"],)).fetchone()
                logical_start = bool(attempt and not attempt["started"])
            if accepted:
                accepted = self._apply_observation(conn, state, current, kind, position_ms)
            if accepted:
                self._save(conn, state)
            result = {"accepted": accepted, "duplicate": False,
                      "logical_start": bool(accepted and logical_start), "session": state}
            self._receipt(conn, session_id, event_id, payload, result)
            return result

    def _apply_observation(self, conn, state, item, kind, position):
        attempt = conn.execute("SELECT * FROM playback_attempts WHERE id=?", (item["attempt_id"],)).fetchone() if item.get("attempt_id") else None
        if kind == "started":
            if item["started"]:
                return False
            item["started"] = True
            state["status"] = "playing"
            state["desired_status"] = "playing"
            if attempt and not attempt["started"]:
                database.insert_play_event(conn, item["song_id"], attempt["history_session_id"], "playing")
                conn.execute("UPDATE playback_attempts SET started=1 WHERE id=?", (attempt["id"],))
        elif kind in ("paused", "resumed"):
            required, target = ("playing", "paused") if kind == "paused" else ("paused", "playing")
            if not item["started"] or state["status"] != required:
                return False
            state["status"] = target
            state["desired_status"] = target
        elif kind in ("hearted", "thumbed_down", "replayed"):
            if not item["started"] or attempt is None:
                return False
            flags = json.loads(attempt["flags_json"])
            if any(flag == kind or isinstance(flag, dict) and flag.get("kind") == kind for flag in flags):
                return False
            database.insert_play_event(conn, item["song_id"], attempt["history_session_id"], kind)
            feedback_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            if kind == "replayed":
                conn.execute("UPDATE songs SET replay_count=replay_count+1 WHERE id=?", (item["song_id"],))
            conn.execute("UPDATE playback_attempts SET flags_json=? WHERE id=?",
                         (_json([*flags, {"kind": kind, "event_id": feedback_id}]), attempt["id"]))
        else:
            if kind == "completed" and not item["started"]:
                return False
            item["terminal"] = True
            if attempt and attempt["outcome"] is None:
                if item["started"] and kind in ("completed", "skipped"):
                    duration = item.get("duration_ms") or 0
                    percent = min(100.0, 100.0 * position / duration) if duration > 0 else 0
                    signal = "completed" if kind == "completed" else ("skip_early" if position < 30000 else "skip_late")
                    database.insert_play_event(conn, item["song_id"], attempt["history_session_id"], signal,
                                               completion_pct=100.0 if kind == "completed" else percent,
                                               skip_at_secs=position / 1000 if kind == "skipped" else None)
                conn.execute("UPDATE playback_attempts SET outcome=? WHERE id=?", (kind, attempt["id"]))
            state["active_index"] += 1
            state["status"] = "ready" if state["active_index"] < len(state["items"]) else "idle"
        state["position_ms"] = 0 if item["terminal"] else position
        return True
