"""One-time bootstrap extends the existing portable digest authority atomically."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import UUID, uuid4

from db import database
from portable_sync import SyncConflict, canonical, _schema_lock, _text


def secret_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("Invalid secret encoding")
    raw = base64.urlsafe_b64decode(value + "=")
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
        raise ValueError("Invalid secret encoding")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class PairingStore:
    def __init__(self, store, server_id):
        self.store, self.server_id, self.clock = store, server_id, time.time
        with _schema_lock, database.get_connection_cm() as conn:
            version = conn.execute("SELECT value FROM portable_meta WHERE key='pairing_schema_version'").fetchone()
            if version:
                if version[0] != "1":
                    raise RuntimeError("Unsupported pairing schema version")
                return
            backups = Path(database.DB_PATH).parent / "backups"
            backups.mkdir(exist_ok=True)
            with sqlite3.connect(str(backups / f"radio-before-pairing-{uuid4().hex}.db")) as backup:
                conn.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Pairing migration backup failed verification")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS portable_pairing_tickets(
                id TEXT PRIMARY KEY,secret_hash TEXT NOT NULL,name TEXT NOT NULL,
                scopes TEXT NOT NULL,expires REAL NOT NULL,receipt TEXT,
                request_hash TEXT,recover_until REAL)""")
            conn.execute("INSERT INTO portable_meta VALUES ('pairing_schema_version','1')")
            conn.commit()

    def create_ticket(self, name):
        _text(name, "device name", 80)
        if name != name.strip():
            raise ValueError("Device name must not have surrounding whitespace")
        now = int(self.clock())
        secret, ticket = secrets.token_urlsafe(32), str(uuid4())
        expires = now + 300
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM portable_pairing_tickets WHERE (receipt IS NULL AND expires<=?) OR recover_until<=?", (now, now))
            if conn.execute("SELECT COUNT(*) FROM portable_pairing_tickets WHERE receipt IS NULL").fetchone()[0] >= 5:
                raise ValueError("Too many outstanding pairing tickets")
            if conn.execute("SELECT COUNT(*) FROM portable_pairing_tickets").fetchone()[0] >= 100:
                raise ValueError("Pairing recovery receipt limit reached")
            conn.execute("INSERT INTO portable_pairing_tickets(id,secret_hash,name,scopes,expires) VALUES (?,?,?,?,?)",
                         (ticket, secret_digest(secret), name, canonical(["catalog:read"]), expires))
        return {"ticket_id": ticket, "ticket_secret": secret, "expires_at": expires}

    def cancel_unused(self):
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM portable_pairing_tickets WHERE receipt IS NULL")

    def exchange(self, body):
        fields = {"protocol_version", "ticket_id", "ticket_secret", "exchange_id", "device_name", "candidate_bearer"}
        if not isinstance(body, dict) or set(body) != fields or type(body["protocol_version"]) is not int or body["protocol_version"] != 1:
            raise ValueError("Invalid pairing exchange")
        for key in ("ticket_id", "exchange_id"):
            if not isinstance(body[key], str) or str(UUID(body[key])) != body[key]:
                raise ValueError("Invalid pairing identity")
        _text(body["device_name"], "device name", 80)
        ticket_hash = secret_digest(body["ticket_secret"])
        bearer_hash = secret_digest(body["candidate_bearer"])
        # Only digests enter persistent storage, including the retry binding.
        binding = dict(body, ticket_secret=ticket_hash, candidate_bearer=bearer_hash)
        binding_hash = hashlib.sha256(canonical(binding).encode()).hexdigest()
        now = int(self.clock())
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM portable_pairing_tickets WHERE id=?", (body["ticket_id"],)).fetchone()
            if not row or not hmac.compare_digest(row["secret_hash"], ticket_hash):
                raise ValueError("Invalid or expired pairing ticket")
            if row["receipt"]:
                if row["recover_until"] <= now:
                    raise ValueError("Pairing recovery expired")
                if not hmac.compare_digest(row["request_hash"], binding_hash):
                    raise SyncConflict("Pairing ticket already consumed with different exchange")
                result = json.loads(row["receipt"])
                device = conn.execute("SELECT revoked,expires FROM portable_devices WHERE id=?", (result["device_id"],)).fetchone()
                if not device or device["revoked"] or device["expires"] <= now:
                    raise ValueError("Paired device is revoked or expired")
                return result
            if row["expires"] <= now or row["name"] != body["device_name"]:
                raise ValueError("Invalid or expired pairing ticket")
            device_id, expires = str(uuid4()), now + 365 * 86400
            result = {"protocol_version": 1, "server_id": self.server_id, "device_id": device_id,
                      "scopes": json.loads(row["scopes"]), "expires_at": expires}
            try:
                conn.execute("INSERT INTO portable_devices VALUES (?,?,?,?,?,0)",
                             (device_id, row["name"], bearer_hash, row["scopes"], expires))
            except sqlite3.IntegrityError:
                raise SyncConflict("Candidate credential already registered") from None
            conn.execute("UPDATE portable_pairing_tickets SET receipt=?,request_hash=?,recover_until=? WHERE id=?",
                         (canonical(result), binding_hash, now + 86400, row["id"]))
            return result

    @staticmethod
    def devices():
        with database.get_connection_cm() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='portable_devices'").fetchone():
                return []
            rows = conn.execute("SELECT id,name,scopes,expires,revoked FROM portable_devices ORDER BY rowid DESC LIMIT 100").fetchall()
        return [{"device_id": row["id"], "name": row["name"], "scopes": json.loads(row["scopes"]),
                 "expires_at": row["expires"], "revoked": bool(row["revoked"])} for row in rows]
