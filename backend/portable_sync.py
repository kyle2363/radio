"""Versioned, retry-safe profile exchange; never controls an active player.

All projections and acknowledgements share a SQLite transaction. Device clocks
are diagnostic: mutable winners use (Lamport clock, device id, event id).
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from uuid import uuid4

from db import database
from portable import feel as feel_rules
from portable import playlists as playlist_rules

SCOPES = frozenset({"catalog:read", "profile:read", "profile:write", "dj:read", "recommend:read"})
KINDS = frozenset({"song_started", "song_completed", "song_skipped", "song_replayed",
                   "favorite_changed", "dislike_changed", "blacklist_changed",
                   "energy_changed", "preference_changed",
                   "playlist_renamed", "playlist_deleted", "playlist_member_changed",
                   "feel_defined", "feel_rated", "feel_compared"})
FEEL_KINDS = frozenset({"feel_defined", "feel_rated", "feel_compared"})
PLAYLIST_KINDS = frozenset({"playlist_renamed", "playlist_deleted", "playlist_member_changed"})
# Additive (checkpoint 7A); checked on every start so existing stores upgrade.
_PLAYLIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS portable_playlists(playlist_id TEXT PRIMARY KEY,collection_id INTEGER,
 deleted INTEGER NOT NULL DEFAULT 0);
"""
# Additive (checkpoint 9A). Append-only evidence index over feel events; the
# events themselves stay in portable_events. song_key/other_key are a server
# song id or "local:<id>".
_FEEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS portable_feel_evidence(device_id TEXT NOT NULL,event_id TEXT NOT NULL,
 feel_id TEXT NOT NULL,kind TEXT NOT NULL,song_key TEXT NOT NULL,other_key TEXT,value REAL,
 tie INTEGER NOT NULL DEFAULT 0,weight REAL NOT NULL DEFAULT 1,occurred_at REAL NOT NULL,
 PRIMARY KEY(device_id,event_id));
CREATE INDEX IF NOT EXISTS idx_portable_feel_evidence_feel ON portable_feel_evidence(feel_id,occurred_at);
"""
_schema_lock = threading.Lock()
_SCHEMA = """
CREATE TABLE portable_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE portable_devices(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,
 scopes TEXT NOT NULL,expires REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE portable_events(revision INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,
 event_id TEXT NOT NULL,clock INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(device_id,event_id));
CREATE TABLE portable_attempts(device_id TEXT NOT NULL,attempt_id TEXT NOT NULL,phase TEXT NOT NULL,
 song_id TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY KEY(device_id,attempt_id,phase));
CREATE TABLE portable_projection(play_id INTEGER PRIMARY KEY,device_id TEXT NOT NULL,event_id TEXT NOT NULL);
CREATE TABLE portable_registers(key TEXT PRIMARY KEY,clock INTEGER NOT NULL,device_id TEXT NOT NULL,
 event_id TEXT NOT NULL,value TEXT NOT NULL);
CREATE TABLE portable_desktop_snapshot(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


class SyncConflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value, field, limit=128):
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {field}")
    return value


class PortableProfileStore:
    def __init__(self, *, initialize=True):
        if not initialize:
            return
        with _schema_lock, database.get_connection_cm() as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='portable_meta'").fetchone():
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='portable_playlists'").fetchone():
                    conn.executescript(_PLAYLIST_SCHEMA)
                    conn.commit()
                if not conn.execute("SELECT 1 FROM portable_meta WHERE key='feel_schema'").fetchone():
                    self._backup(conn, "feel")
                    conn.executescript("BEGIN IMMEDIATE;" + _FEEL_SCHEMA)
                    self._migrate_legacy_energy(conn)
                    conn.commit()
                return
            self._backup(conn, "portable")
            conn.executescript("BEGIN IMMEDIATE;" + _SCHEMA + _PLAYLIST_SCHEMA + _FEEL_SCHEMA)
            conn.executemany("INSERT INTO portable_meta VALUES (?,?)",
                             [("catalog_id", str(uuid4())), ("schema_version", "1")])
            self._migrate_legacy_energy(conn)
            conn.commit()

    @staticmethod
    def _backup(conn, label):
        backup_dir = Path(database.DB_PATH).parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        with sqlite3.connect(str(backup_dir / f"radio-before-{label}-{uuid4().hex}.db")) as backup:
            conn.backup(backup)
            if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Portable migration backup failed verification")

    @staticmethod
    def _migrate_legacy_energy(conn):
        """Desktop running-mean ``user_energy`` becomes one weighted Energy rating per song (once)."""
        catalog_id = conn.execute("SELECT value FROM portable_meta WHERE key='catalog_id'").fetchone()[0]
        clock = conn.execute("SELECT COALESCE(MAX(clock),0) FROM portable_events").fetchone()[0]
        rows = conn.execute("SELECT id,user_energy,user_energy_count,user_energy_updated_at FROM songs "
                            "WHERE user_energy IS NOT NULL AND COALESCE(user_energy_count,0)>0 ORDER BY id").fetchall()
        for sid, energy, count, updated in rows:
            rating = feel_rules.legacy_energy_rating(energy, count, updated)
            weight = min(rating["weight"], feel_rules.MAX_WEIGHT)
            value = min(1.0, max(0.0, rating["value"]))
            clock += 1
            event = {"protocol_version": 1, "device_id": "server", "event_id": f"legacy-energy:{sid}",
                     "logical_clock": clock, "kind": "feel_rated", "feel_id": feel_rules.ENERGY_FEEL_ID,
                     "song_id": str(sid), "catalog_id": catalog_id, "value": value, "weight": weight,
                     "occurred_at": rating["at"]}
            conn.execute("INSERT INTO portable_events(device_id,event_id,clock,payload) VALUES ('server',?,?,?)",
                         (event["event_id"], clock, canonical(event)))
            conn.execute("INSERT INTO portable_feel_evidence VALUES ('server',?,?,'rated',?,NULL,?,0,?,?)",
                         (event["event_id"], feel_rules.ENERGY_FEEL_ID, str(sid), value, weight, rating["at"]))
        conn.execute("INSERT OR REPLACE INTO portable_meta VALUES ('feel_schema','1')")

    @contextmanager
    def transaction(self):
        with database.get_connection_cm() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @property
    def catalog_id(self):
        with database.get_connection_cm() as conn:
            return conn.execute("SELECT value FROM portable_meta WHERE key='catalog_id'").fetchone()[0]

    def issue_device(self, name, scopes, *, lifetime=365 * 86400):
        _text(name, "device name", 80)
        if not isinstance(scopes, list) or not scopes or not set(scopes) <= SCOPES:
            raise ValueError("Unsupported device scopes")
        token, device_id = secrets.token_urlsafe(32), str(uuid4())
        expires = time.time() + lifetime
        with self.transaction() as conn:
            conn.execute("INSERT INTO portable_devices VALUES (?,?,?,?,?,0)",
                         (device_id, name, hashlib.sha256(token.encode()).hexdigest(),
                          canonical(sorted(set(scopes))), expires))
        return {"protocol_version": 1, "device_id": device_id, "token": token,
                "scopes": sorted(set(scopes)), "expires_at": expires}

    def revoke_device(self, device_id):
        with self.transaction() as conn:
            conn.execute("UPDATE portable_devices SET revoked=1 WHERE id=?", (device_id,))

    def grant_profile_sync(self, device_id):
        """Add profile read/write to an existing active device without rotating identity."""
        with self.transaction() as conn:
            row = conn.execute("SELECT scopes,expires,revoked FROM portable_devices WHERE id=?", (device_id,)).fetchone()
            if not row or row[2] or row[1] <= time.time():
                raise ValueError("Device is missing, revoked or expired")
            scopes = sorted(set(json.loads(row[0])) | {"profile:read", "profile:write"})
            conn.execute("UPDATE portable_devices SET scopes=? WHERE id=?", (canonical(scopes), device_id))
        return {"device_id": device_id, "scopes": scopes}

    def grant_connected_radio(self, device_id):
        """Add ``recommend:read`` to an existing active device without rotating identity."""
        with self.transaction() as conn:
            row = conn.execute("SELECT scopes,expires,revoked FROM portable_devices WHERE id=?", (device_id,)).fetchone()
            if not row or row[2] or row[1] <= time.time():
                raise ValueError("Device is missing, revoked or expired")
            scopes = sorted(set(json.loads(row[0])) | {"recommend:read"})
            conn.execute("UPDATE portable_devices SET scopes=? WHERE id=?", (canonical(scopes), device_id))
        return {"device_id": device_id, "scopes": scopes}

    def revoke_connected_radio(self, device_id):
        """Remove ``recommend:read``; the credential and every other scope are untouched."""
        with self.transaction() as conn:
            row = conn.execute("SELECT scopes FROM portable_devices WHERE id=?", (device_id,)).fetchone()
            if not row:
                raise ValueError("Device is missing")
            scopes = sorted(set(json.loads(row[0])) - {"recommend:read"})
            conn.execute("UPDATE portable_devices SET scopes=? WHERE id=?", (canonical(scopes), device_id))
        return {"device_id": device_id, "scopes": scopes}

    def authorize(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            return None
        with database.get_connection_cm() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='portable_devices'").fetchone():
                return None
            row = conn.execute("SELECT * FROM portable_devices WHERE token_hash=? AND revoked=0 AND expires>?",
                               (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return {"device_id": row["id"], "scopes": json.loads(row["scopes"])} if row else None

    @staticmethod
    def validate(event, device_id, catalog_id):
        if not isinstance(event, dict) or event.get("protocol_version") != 1:
            raise ValueError("Unsupported event protocol")
        allowed = {"protocol_version", "event_id", "device_id", "logical_clock", "kind", "song_id",
                   "attempt_id", "occurred_at", "value", "key", "local_id", "reason", "completion", "catalog_id",
                   "playlist_id", "position", "feel_id", "other_song_id", "other_local_id", "weight"}
        if set(event) - allowed:
            raise ValueError("Unknown event fields")
        _text(event.get("event_id"), "event id")
        if event.get("device_id") != device_id:
            raise ValueError("Event origin does not match authenticated device")
        clock = event.get("logical_clock")
        if type(clock) is not int or not 0 <= clock <= 2**53 - 1:
            raise ValueError("Invalid logical clock")
        kind = event.get("kind")
        if kind not in KINDS:
            raise ValueError("Unsupported event kind (preparation is not playback)")
        occurred = event.get("occurred_at")
        if type(occurred) not in (int, float) or not math.isfinite(occurred) or not 0 <= occurred <= 253402300799:
            raise ValueError("Invalid diagnostic event time")
        song_id = event.get("song_id")
        if song_id is not None and (not isinstance(song_id, str) or not song_id.isascii() or not song_id.isdecimal()):
            raise ValueError("song_id must be a server row ID or null")
        other_id = event.get("other_song_id")
        if other_id is not None and (not isinstance(other_id, str) or not other_id.isascii() or not other_id.isdecimal()):
            raise ValueError("other_song_id must be a server row ID or null")
        if (song_id is not None or other_id is not None) and event.get("catalog_id") != catalog_id:
            raise ValueError("Song belongs to a different or unspecified source catalog")
        if kind in FEEL_KINDS:
            PortableProfileStore._validate_feel(event, kind)
        elif set(event) & {"feel_id", "other_song_id", "other_local_id", "weight"}:
            raise ValueError("Feel fields on a non-feel event")
        if kind in PLAYLIST_KINDS:
            if not playlist_rules.valid_playlist_id(event.get("playlist_id")):
                raise ValueError("Invalid playlist id")
            value = event.get("value")
            if kind == "playlist_renamed" and not playlist_rules.valid_name(value):
                raise ValueError("Invalid playlist name")
            if kind != "playlist_renamed" and type(value) is not bool:
                raise ValueError("Boolean playlist value required")
            if (kind == "playlist_member_changed") != ("position" in event):
                raise ValueError("Position belongs only to playlist members")
            if kind == "playlist_member_changed" and not playlist_rules.valid_key(event["position"]):
                raise ValueError("Invalid playlist position")
            if kind != "playlist_member_changed" and (song_id is not None or "local_id" in event):
                raise ValueError("Song fields belong only to playlist members")
        elif "playlist_id" in event or "position" in event:
            raise ValueError("Playlist fields on a non-playlist event")
        if kind not in ("preference_changed", "playlist_renamed", "playlist_deleted", "feel_defined") and song_id is None:
            _text(event.get("local_id"), "local-only song id")
        if kind.startswith("song_"):
            _text(event.get("attempt_id"), "attempt id")
        if kind in ("favorite_changed", "blacklist_changed", "dislike_changed") and type(event.get("value")) is not bool:
            raise ValueError("Boolean preference required")
        if kind == "energy_changed":
            value = event.get("value")
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError("Energy must be null or within [0,1]")
        if kind == "preference_changed":
            key, value = event.get("key"), event.get("value")
            valid = (key == "exploration_level" and type(value) in (int, float) and 0 <= value <= 1)
            valid |= key == "llm_reranking_enabled" and type(value) is bool
            if not valid:
                raise ValueError("Unsupported shared recommendation preference")
        if "reason" in event and event["reason"] not in ("too_intense", "too_slow", "too_samey", "wrong_mood", "just_skip"):
            raise ValueError("Invalid skip reason")
        if "completion" in event and (type(event["completion"]) not in (int, float) or not 0 <= event["completion"] <= 1):
            raise ValueError("Invalid completion ratio")
        if len(canonical(event)) > 4096:
            raise ValueError("Event too large")

    @staticmethod
    def _validate_feel(event, kind):
        if not feel_rules.valid_feel_id(event.get("feel_id")):
            raise ValueError("Invalid feel id")
        value = event.get("value")
        has_other = "other_song_id" in event or "other_local_id" in event
        if kind == "feel_defined":
            if not feel_rules.valid_definition(event["feel_id"], value):
                raise ValueError("Invalid feel definition")
            if event.get("song_id") is not None or "local_id" in event or has_other or "weight" in event:
                raise ValueError("Song fields belong only to feel ratings and comparisons")
            return
        if kind == "feel_rated":
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Feel rating must be within [0,1]")
            if has_other:
                raise ValueError("A rating names one song")
            weight = event.get("weight", 1.0)
            if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 < weight <= feel_rules.MAX_WEIGHT:
                raise ValueError("Invalid rating weight")
            return
        if value not in ("win", "tie") or "weight" in event:
            raise ValueError("Comparison value must be win or tie")
        other = event.get("other_song_id")
        if other is None:
            _text(event.get("other_local_id"), "other local-only song id")
        elif "other_local_id" in event:
            raise ValueError("Comparison names one other song")
        first = event.get("song_id") or "local:" + str(event.get("local_id"))
        second = other or "local:" + str(event.get("other_local_id"))
        if first == second:
            raise ValueError("A song cannot be compared with itself")

    @staticmethod
    def _project_feel(conn, event):
        kind = event["kind"]
        if kind == "feel_defined":
            key = "feel:" + event["feel_id"]
            order = (event["logical_clock"], event["device_id"], event["event_id"])
            old = conn.execute("SELECT clock,device_id,event_id FROM portable_registers WHERE key=?", (key,)).fetchone()
            if not old or tuple(old) < order:
                conn.execute("INSERT OR REPLACE INTO portable_registers VALUES (?,?,?,?,?)",
                             (key, *order, canonical(event["value"])))
            return
        song = event.get("song_id") or "local:" + event["local_id"]
        if kind == "feel_rated":
            other, value, tie, weight = None, float(event["value"]), 0, float(event.get("weight", 1.0))
        else:
            other = event.get("other_song_id") or "local:" + event["other_local_id"]
            value, tie, weight = None, int(event["value"] == "tie"), 1.0
        conn.execute("INSERT INTO portable_feel_evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (event["device_id"], event["event_id"], event["feel_id"], kind[5:], song, other, value, tie,
                      weight, float(event["occurred_at"])))

    def import_events(self, device_id, events):
        _text(device_id, "device id")
        if not isinstance(events, list) or not 1 <= len(events) <= 500:
            raise ValueError("Import requires 1 to 500 events")
        catalog_id = self.catalog_id
        for event in events:
            self.validate(event, device_id, catalog_id)
        accepted, duplicates = [], []
        with self.transaction() as conn:
            for event in events:
                payload = canonical(event)
                old = conn.execute("SELECT payload FROM portable_events WHERE device_id=? AND event_id=?",
                                   (device_id, event["event_id"])).fetchone()
                if old:
                    if old[0] != payload:
                        raise SyncConflict("Event identity reused with different payload")
                    duplicates.append(event["event_id"])
                    continue
                conn.execute("INSERT INTO portable_events(device_id,event_id,clock,payload) VALUES (?,?,?,?)",
                             (device_id, event["event_id"], event["logical_clock"], payload))
                self._project(conn, event)
                accepted.append(event["event_id"])
            cursor = conn.execute("SELECT COALESCE(MAX(revision),0) FROM portable_events").fetchone()[0]
        with database._settings_cache_lock:
            database._settings_cache.clear()
        if accepted:
            database._shared_profile_generation = getattr(database, "_shared_profile_generation", 0) + 1
        return {"protocol_version": 1, "accepted": accepted, "duplicates": duplicates, "cursor": cursor}

    def _play(self, conn, event, signal):
        sid = event.get("song_id")
        if sid is None or not conn.execute("SELECT 1 FROM songs WHERE id=?", (sid,)).fetchone():
            return
        database.insert_play_event(conn, int(sid), None, signal,
                                   completion_pct=100 * event.get("completion", 1 if signal == "completed" else 0))
        play_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Existing history stores local naive ISO times. Preserve that format;
        # protocol timestamps remain absolute epoch seconds.
        observed = datetime.fromtimestamp(event["occurred_at"]).isoformat()
        conn.execute("UPDATE play_events SET played_at=? WHERE id=?", (observed, play_id))
        conn.execute("INSERT INTO portable_projection VALUES (?,?,?)",
                     (play_id, event["device_id"], event["event_id"]))
        if signal == "playing":
            latest = conn.execute("SELECT MAX(played_at) FROM play_events WHERE song_id=? AND signal='playing'", (sid,)).fetchone()[0]
            conn.execute("UPDATE songs SET last_played=? WHERE id=?", (latest, sid))
        if signal == "replayed":
            conn.execute("UPDATE songs SET replay_count=replay_count+1 WHERE id=?", (sid,))

    def _project(self, conn, event):
        kind, sid = event["kind"], event.get("song_id")
        if kind.startswith("song_"):
            phase = "start" if kind == "song_started" else "finish" if kind in ("song_completed", "song_skipped") else event["event_id"]
            identity = (event["device_id"], event["attempt_id"], phase)
            old = conn.execute("SELECT song_id,kind FROM portable_attempts WHERE device_id=? AND attempt_id=? AND phase=?", identity).fetchone()
            song_key = sid or "local:" + event.get("local_id", "")
            if old:
                if tuple(old) != (song_key, kind):
                    raise SyncConflict("Attempt outcome or song changed")
                return
            prior = conn.execute("SELECT song_id FROM portable_attempts WHERE device_id=? AND attempt_id=? LIMIT 1", identity[:2]).fetchone()
            if prior and prior[0] != song_key:
                raise SyncConflict("Attempt reused for another song")
            conn.execute("INSERT INTO portable_attempts VALUES (?,?,?,?,?)", (*identity, song_key, kind))
            signal = {"song_started": "playing", "song_completed": "completed", "song_skipped": "skip_early", "song_replayed": "replayed"}[kind]
            self._play(conn, event, signal)
            if sid and kind == "song_skipped" and event.get("reason"):
                conn.execute("INSERT INTO skip_feedback(session_id,skip_streak,reason,duration) VALUES (NULL,0,?,NULL)", (event["reason"],))
            return
        if kind in PLAYLIST_KINDS:
            return self._project_playlist(conn, event)
        if kind in FEEL_KINDS:
            return self._project_feel(conn, event)
        local_key = "local:" + canonical([event["device_id"], event.get("local_id")])
        key = ("preference:" + event["key"] if kind == "preference_changed" else kind + ":" + (sid or local_key))
        order = (event["logical_clock"], event["device_id"], event["event_id"])
        old = conn.execute("SELECT clock,device_id,event_id FROM portable_registers WHERE key=?", (key,)).fetchone()
        if old and tuple(old) >= order:
            return
        conn.execute("INSERT OR REPLACE INTO portable_registers VALUES (?,?,?,?,?)", (key, *order, canonical(event.get("value"))))
        value = event.get("value")
        # A later desktop edit must compare with the last projected value, even
        # when it returns to the value captured before this remote update.
        conn.execute("INSERT OR REPLACE INTO portable_desktop_snapshot VALUES (?,?)", (key, canonical(value)))
        if kind == "preference_changed":
            encoded = str(value).lower() if type(value) is bool else str(value)
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (event["key"], encoded))
        elif sid and conn.execute("SELECT 1 FROM songs WHERE id=?", (sid,)).fetchone():
            if kind in ("favorite_changed", "dislike_changed"):
                signal = "hearted" if kind == "favorite_changed" else "thumbed_down"
                if signal == "thumbed_down":
                    active = conn.execute("SELECT COUNT(*) FROM play_events WHERE song_id=? AND signal=? AND COALESCE(undone,0)=0", (sid, signal)).fetchone()[0]
                    conn.execute("UPDATE songs SET skip_count=MAX(0,skip_count-?) WHERE id=?", (active, sid))
                conn.execute("UPDATE play_events SET undone=1 WHERE song_id=? AND signal=?", (sid, signal))
                if value:
                    self._play(conn, event, signal)
            elif kind == "blacklist_changed":
                if value:
                    conn.execute("INSERT OR REPLACE INTO blacklist(song_id,level,reason) VALUES (?,'never_play','synced_user')", (sid,))
                else:
                    conn.execute("DELETE FROM blacklist WHERE song_id=?", (sid,))
            elif kind == "energy_changed":
                conn.execute("UPDATE songs SET user_energy=?,user_energy_count=?,user_energy_updated_at=? WHERE id=?",
                             (value, int(value is not None), event["occurred_at"], sid))

    # ---- Playlists (checkpoint 7A) -------------------------------------------------
    # Registers: playlist_name:<pid>, playlist_deleted:<pid>,
    # playlist_member:<pid>:<song id | local:<id>> = {"present", "position"}.

    @staticmethod
    def _playlist_keys(pid):
        return ("playlist_name:" + pid, "playlist_deleted:" + pid, f"playlist_member:{pid}:%")

    @staticmethod
    def _playlist_ids(conn):
        ids = {key.split(":")[1] for (key,) in conn.execute("SELECT key FROM portable_registers WHERE key LIKE 'playlist%'")}
        return sorted(ids)

    def _project_playlist(self, conn, event):
        pid, kind = event["playlist_id"], event["kind"]
        if kind == "playlist_member_changed":
            sid = event.get("song_id")
            member = sid if sid is not None else "local:" + event["local_id"]
            key, value = f"playlist_member:{pid}:{member}", {"present": event["value"], "position": event["position"]}
        else:
            key = ("playlist_name:" if kind == "playlist_renamed" else "playlist_deleted:") + pid
            value = event["value"]
        order = (event["logical_clock"], event["device_id"], event["event_id"])
        old = conn.execute("SELECT clock,device_id,event_id FROM portable_registers WHERE key=?", (key,)).fetchone()
        if old and tuple(old) >= order:
            return
        conn.execute("INSERT OR REPLACE INTO portable_registers VALUES (?,?,?,?,?)", (key, *order, canonical(value)))
        self._materialize(conn, pid)

    def _playlist_state(self, conn, pid):
        """Winning registers for one playlist: name, deleted flag and ordered present members."""
        state = {"name": None, "deleted": False, "members": []}
        prefix = f"playlist_member:{pid}:"
        for key, value in conn.execute("SELECT key,value FROM portable_registers WHERE key IN (?,?) OR key LIKE ?",
                                       self._playlist_keys(pid)):
            value = json.loads(value)
            if key.startswith("playlist_name:"):
                state["name"] = value
            elif key.startswith("playlist_deleted:"):
                state["deleted"] = value
            elif value["present"]:
                state["members"].append({"key": key[len(prefix):], "position": value["position"]})
        state["members"] = playlist_rules.ordered_members(state["members"])
        return state

    def _materialize(self, conn, pid):
        """Rebuild the linked desktop collection from winning registers.

        Desktop edits are captured before each import, so the registers already
        hold them. A delete unlinks and keeps the desktop collection.
        """
        state = self._playlist_state(conn, pid)
        if state["deleted"]:
            conn.execute("INSERT OR REPLACE INTO portable_playlists VALUES (?,NULL,1)", (pid,))
            return
        link = conn.execute("SELECT collection_id FROM portable_playlists WHERE playlist_id=?", (pid,)).fetchone()
        cid = link[0] if link else None
        if cid is not None and not conn.execute("SELECT 1 FROM collections WHERE id=?", (cid,)).fetchone():
            cid = None
        if cid is None:
            if state["name"] is None:
                return
            cid = conn.execute("INSERT INTO collections(name) VALUES (?)", (state["name"],)).lastrowid
        elif state["name"] is not None:
            conn.execute("UPDATE collections SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND name!=?",
                         (state["name"], cid, state["name"]))
        conn.execute("INSERT OR REPLACE INTO portable_playlists VALUES (?,?,0)", (pid, cid))
        conn.execute("DELETE FROM collection_songs WHERE collection_id=?", (cid,))
        position = 0
        for member in state["members"]:
            if member["key"].isdecimal() and conn.execute("SELECT 1 FROM songs WHERE id=?", (member["key"],)).fetchone():
                conn.execute("INSERT INTO collection_songs(collection_id,song_id,position) VALUES (?,?,?)",
                             (cid, int(member["key"]), position))
                position += 1
        # The projected collection is the desktop baseline: no echo on next capture.
        conn.executemany("INSERT OR REPLACE INTO portable_desktop_snapshot VALUES (?,?)",
                         [(k, canonical(v)) for k, v in self._collection_snapshot(conn, pid, cid).items()])

    def _collection_snapshot(self, conn, pid, cid):
        """Desktop view of a linked collection as register values, reusing current positions."""
        state = self._playlist_state(conn, pid)
        row = conn.execute("SELECT name FROM collections WHERE id=?", (cid,)).fetchone() if cid is not None else None
        if not row:
            return {"playlist_deleted:" + pid: True}
        current = {m["key"]: m["position"] for m in state["members"]}
        desired = [str(r[0]) for r in conn.execute(
            "SELECT song_id FROM collection_songs WHERE collection_id=? ORDER BY position,added_at,song_id", (cid,))]
        positions = playlist_rules.reposition(desired, {k: v for k, v in current.items() if k in desired})
        name = " ".join(row[0].split())[:playlist_rules.NAME_LIMIT].strip() or "Playlist"
        snapshot = {"playlist_name:" + pid: name}
        for member in desired:
            snapshot[f"playlist_member:{pid}:{member}"] = {"present": True, "position": positions[member]}
        # Desktop only decides catalog songs; phone-only members are left alone.
        for member, position in current.items():
            if member.isdecimal() and member not in positions:
                snapshot[f"playlist_member:{pid}:{member}"] = {"present": False, "position": position}
        return snapshot

    def share_collection(self, collection_id):
        """Opt a desktop collection into playlist sync; idempotent. Returns its playlist id."""
        with self.transaction() as conn:
            if not conn.execute("SELECT 1 FROM collections WHERE id=?", (collection_id,)).fetchone():
                raise ValueError("Collection is missing")
            row = conn.execute("SELECT playlist_id FROM portable_playlists WHERE collection_id=? AND deleted=0",
                               (collection_id,)).fetchone()
            if row:
                return row[0]
            pid = str(uuid4())
            conn.execute("INSERT INTO portable_playlists VALUES (?,?,0)", (pid, collection_id))
            return pid

    def _capture_playlists(self, conn, clock):
        catalog_id = None
        for pid, cid in conn.execute("SELECT playlist_id,collection_id FROM portable_playlists WHERE deleted=0").fetchall():
            snapshot = self._collection_snapshot(conn, pid, cid)
            keys = self._playlist_keys(pid)
            query = "SELECT key,value FROM {} WHERE key IN (?,?) OR key LIKE ?"
            baseline = dict(conn.execute(query.format("portable_desktop_snapshot"), keys))
            registers = dict(conn.execute(query.format("portable_registers"), keys))
            for key, value in sorted(snapshot.items()):
                encoded = canonical(value)
                if baseline.get(key) == encoded or registers.get(key) == encoded:
                    if baseline.get(key) != encoded:
                        conn.execute("INSERT OR REPLACE INTO portable_desktop_snapshot VALUES (?,?)", (key, encoded))
                    continue
                clock += 1
                kind, _, rest = key.partition(":")
                event = {"protocol_version": 1, "device_id": "server", "event_id": str(uuid4()),
                         "logical_clock": clock, "playlist_id": pid, "occurred_at": time.time()}
                if kind == "playlist_member":
                    member = rest.split(":", 1)[1]
                    event.update(kind="playlist_member_changed", value=value["present"], position=value["position"])
                    catalog_id = catalog_id or conn.execute("SELECT value FROM portable_meta WHERE key='catalog_id'").fetchone()[0]
                    event.update(song_id=member, catalog_id=catalog_id)
                else:
                    event.update(kind="playlist_renamed" if kind == "playlist_name" else "playlist_deleted", value=value)
                conn.execute("INSERT INTO portable_events(device_id,event_id,clock,payload) VALUES ('server',?,?,?)",
                             (event["event_id"], clock, canonical(event)))
                conn.execute("INSERT OR REPLACE INTO portable_registers VALUES (?,?,?,?,?)",
                             (key, clock, "server", event["event_id"], encoded))
                conn.execute("INSERT OR REPLACE INTO portable_desktop_snapshot VALUES (?,?)", (key, encoded))
            if snapshot.get("playlist_deleted:" + pid) is True:
                conn.execute("UPDATE portable_playlists SET collection_id=NULL,deleted=1 WHERE playlist_id=?", (pid,))
        return clock

    def events(self, cursor=0, limit=100):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Invalid cursor or page size")
        with database.get_connection_cm() as conn:
            rows = conn.execute("SELECT revision,payload FROM portable_events WHERE revision>? ORDER BY revision LIMIT ?", (cursor, limit + 1)).fetchall()
            high = conn.execute("SELECT COALESCE(MAX(clock),0) FROM portable_events").fetchone()[0]
        page = rows[:limit]
        def wire_event(payload):
            event = json.loads(payload)
            completion = event.get("completion")
            # Compatibility for invalid desktop exports made before percentage
            # conversion. Keep the immutable ledger and valid wire events intact.
            if (event.get("device_id") == "server" and event.get("event_id", "").startswith("desktop:")
                    and type(completion) in (int, float) and completion > 1):
                event["completion"] = min(completion / 100, 1.0)
            return event
        return {"protocol_version": 1, "events": [{"revision": row[0], "event": wire_event(row[1])} for row in page],
                "cursor": page[-1][0] if page else cursor, "has_more": len(rows) > limit, "logical_clock": high}

    def profile(self):
        result = {"protocol_version": 1, "favorites": {}, "dislikes": {}, "blacklist": {}, "energy": {}, "preferences": {}, "feels": {}}
        mapping = {"favorite_changed": "favorites", "dislike_changed": "dislikes", "blacklist_changed": "blacklist", "energy_changed": "energy", "preference": "preferences", "feel": "feels"}
        with database.get_connection_cm() as conn:
            result["playlists"] = {pid: self._playlist_state(conn, pid) for pid in self._playlist_ids(conn)}
            for row in conn.execute("SELECT key,value FROM portable_registers WHERE key NOT LIKE 'playlist%'"):
                kind, _, sid = row[0].partition(":")
                result[mapping[kind]][sid] = json.loads(row[1])
        return result

    def capture_desktop(self):
        """Capture accepted history and mutable profile changes at manual sync time.

        Existing rows are exported, never replayed into counters. Local snapshots
        prevent an imported projection from becoming a second desktop event.
        """
        signal_map = {"playing": "song_started", "completed": "song_completed", "skip_early": "song_skipped",
                      "skip_late": "song_skipped", "replayed": "song_replayed"}
        catalog_id = self.catalog_id
        with self.transaction() as conn:
            clock = conn.execute("SELECT COALESCE(MAX(clock),0) FROM portable_events").fetchone()[0]
            rows = conn.execute("SELECT p.* FROM play_events p LEFT JOIN portable_projection x ON x.play_id=p.id WHERE x.play_id IS NULL AND COALESCE(p.undone,0)=0 AND NOT EXISTS (SELECT 1 FROM portable_events e WHERE e.device_id='server' AND e.event_id='desktop:'||p.id) ORDER BY p.id").fetchall()
            for row in rows:
                kind = signal_map.get(row["signal"])
                if not kind:
                    continue
                identity = f"desktop:{row['id']}"
                clock += 1
                event = {"protocol_version": 1, "device_id": "server", "event_id": identity,
                         "logical_clock": clock, "kind": kind, "song_id": str(row["song_id"]),
                         "catalog_id": catalog_id,
                         "attempt_id": f"legacy:{row['id']}", "occurred_at": datetime.fromisoformat(row["played_at"]).timestamp(),
                         "completion": min(1.0, max(0.0, (row["completion_pct"] or 0) / 100))}
                conn.execute("INSERT INTO portable_events(device_id,event_id,clock,payload) VALUES ('server',?,?,?)", (identity, clock, canonical(event)))
            snapshot = {}
            positives = {signal: {str(row[0]) for row in conn.execute("SELECT DISTINCT song_id FROM play_events WHERE signal=? AND COALESCE(undone,0)=0", (signal,))}
                         for signal in ("hearted", "thumbed_down")}
            blocked = {str(row[0]) for row in conn.execute("SELECT song_id FROM blacklist WHERE expires_at IS NULL OR expires_at>datetime('now')")}
            for row in conn.execute("SELECT id,user_energy FROM songs"):
                sid = str(row[0])
                for signal, name in (("hearted", "favorite_changed"), ("thumbed_down", "dislike_changed")):
                    value = sid in positives[signal]
                    snapshot[f"{name}:{sid}"] = value
                snapshot[f"energy_changed:{sid}"] = row[1]
                snapshot[f"blacklist_changed:{sid}"] = sid in blocked
            for key in ("exploration_level", "llm_reranking_enabled"):
                row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                if row:
                    snapshot["preference:" + key] = row[0].lower() == "true" if key == "llm_reranking_enabled" else float(row[0])
            old_values = dict(conn.execute("SELECT key,value FROM portable_desktop_snapshot"))
            registers = dict(conn.execute("SELECT key,value FROM portable_registers"))
            for key, value in snapshot.items():
                old, register = old_values.get(key), registers.get(key)
                encoded = canonical(value)
                if old == encoded or register == encoded:
                    if old != encoded:
                        conn.execute("INSERT OR REPLACE INTO portable_desktop_snapshot VALUES (?,?)", (key, encoded))
                    continue
                # Initial false/null values carry no change; true/ratings/defaults
                # seed the shared profile without inventing listening history.
                if not old and (value is False or value is None) and not key.startswith("preference:"):
                    conn.execute("INSERT OR REPLACE INTO portable_desktop_snapshot VALUES (?,?)", (key, encoded))
                    continue
                clock += 1
                kind, _, target = key.partition(":")
                event = {"protocol_version": 1, "device_id": "server", "event_id": str(uuid4()), "logical_clock": clock,
                         "kind": "preference_changed" if kind == "preference" else kind,
                         "song_id": None if kind == "preference" else target, "occurred_at": time.time(), "value": value}
                if kind != "preference":
                    event["catalog_id"] = catalog_id
                if kind == "preference":
                    event["key"] = target
                conn.execute("INSERT INTO portable_events(device_id,event_id,clock,payload) VALUES ('server',?,?,?)", (event["event_id"], clock, canonical(event)))
                conn.execute("INSERT OR REPLACE INTO portable_registers VALUES (?,?,?,?,?)", (key, clock, "server", event["event_id"], encoded))
                conn.execute("INSERT OR REPLACE INTO portable_desktop_snapshot VALUES (?,?)", (key, encoded))
            self._capture_playlists(conn, clock)
