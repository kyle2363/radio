"""Test-only independent client. Uses HTTP + public portable package exclusively."""
from copy import deepcopy
import json
from pathlib import Path
import time

from portable.catalog import canonical_json, read_catalog
from portable.matching import match_catalog
from portable.recommender import OfflineRecommender

PREFIX = "/api/portable/v1"


class ClientSimulator:
    def __init__(self, http, directory, device_id, local_tracks, *, decisions=()):
        self.http, self.root, self.device = http, Path(directory), device_id
        self.local_tracks, self.decisions = local_tracks, decisions
        self.root.mkdir(parents=True, exist_ok=True)
        self.cursor, self.clock, self.sequence = 0, 0, 0
        self.outbox, self.engine, self.catalog = [], None, None

    async def bootstrap(self):
        response = await self.http.get(PREFIX + "/capabilities")
        response.raise_for_status()
        manifest = (await self.http.get(PREFIX + "/catalog")).json()
        target = self.root / str(manifest["revision"])
        (target / "vectors").mkdir(parents=True, exist_ok=True)
        (target / "manifest.json").write_bytes(canonical_json(manifest))
        for digest in manifest["included_blobs"]:
            response = await self.http.get(PREFIX + "/vectors/" + digest)
            response.raise_for_status()
            (target / "vectors" / (digest + ".bin")).write_bytes(response.content)
        self.catalog = read_catalog(target)
        matches = match_catalog(self.catalog.songs, self.local_tracks, self.decisions)
        self.engine = OfflineRecommender(self.catalog, self.local_tracks, mappings=matches, seed=17)
        await self.pull()
        return matches

    def record(self, kind, song_id, now, *, attempt_id="local-attempt", value=None, reason=None):
        self.clock += 1
        self.sequence += 1
        eid = f"event-{self.sequence}"
        immutable = {"started": "song_started", "completed": "song_completed", "skipped": "song_skipped", "replayed": "song_replayed"}
        mutable = {"hearted": "favorite_changed", "unhearted": "favorite_changed", "blacklisted": "blacklist_changed",
                   "unblacklisted": "blacklist_changed", "disliked": "dislike_changed", "energy": "energy_changed"}
        event = {"protocol_version": 1, "event_id": eid, "device_id": self.device,
                 "logical_clock": self.clock, "occurred_at": now,
                 "kind": immutable.get(kind, mutable.get(kind)), "catalog_id": self.catalog.manifest["catalog_id"],
                 "song_id": song_id if song_id in self.catalog.songs else None}
        if event["song_id"] is None:
            event["local_id"] = song_id.removeprefix("local:")
        if kind in immutable:
            event["attempt_id"] = attempt_id
        else:
            event["value"] = value if kind == "energy" else kind not in ("unhearted", "unblacklisted")
        if reason:
            event["reason"] = reason
        local = {"event_id": self.device + ":" + eid, "type": kind, "song_id": song_id, "timestamp": now}
        if value is not None:
            local["value"] = value
        if reason:
            local["reason"] = reason
        self.engine.feedback(local)
        self.outbox.append(event)
        self.save()

    async def pull(self):
        while True:
            response = await self.http.get(PREFIX + "/events", params={"cursor": self.cursor, "limit": 2})
            response.raise_for_status()
            page = response.json()
            self.clock = max(self.clock, page["logical_clock"])
            for row in page["events"]:
                event = row["event"]
                if event.get("song_id") is None and event["kind"] != "preference_changed" and event["device_id"] != self.device:
                    continue  # opaque file IDs belong to their origin device
                kind = {"song_started": "started", "song_completed": "completed", "song_skipped": "skipped", "song_replayed": "replayed"}.get(event["kind"])
                if kind:
                    # Shared learning never transfers another device's output,
                    # current song, queue, mood, trajectory or skip streak.
                    operational = {key: deepcopy(self.engine.state[key]) for key in ("current", "queue", "upcoming", "mood", "energy_history", "skip_streak")}
                    self.engine.feedback({"event_id": event["device_id"] + ":" + event["event_id"], "type": kind,
                                          "song_id": event.get("song_id") or "local:" + event["local_id"], "timestamp": event["occurred_at"]})
                    self.engine.state.update(operational)
            self.cursor = page["cursor"]
            if not page["has_more"]:
                break
        profile = (await self.http.get(PREFIX + "/profile")).json()
        def device_key(key):
            if not key.startswith("local:"):
                return key
            origin, local_id = json.loads(key[6:])
            return "local:" + local_id if origin == self.device else None
        self.engine.state["hearts"] = sorted(device_key(sid) for sid, value in profile["favorites"].items() if value and device_key(sid))
        self.engine.state["blacklist"] = sorted(device_key(sid) for sid in set(profile["blacklist"]) | set(profile["dislikes"])
                                                if device_key(sid) and (profile["blacklist"].get(sid) or profile["dislikes"].get(sid)))
        self.engine.state["user_energy"] = {device_key(sid): value for sid, value in profile["energy"].items() if value is not None and device_key(sid)}
        self.engine._taste_cache = None

    async def synchronize(self, *, lose_ack=False, lose_ack_batch=1):
        batch_number = 0
        while self.outbox:
            batch_number += 1
            response = await self.http.post(PREFIX + "/events", json={"protocol_version": 1, "events": self.outbox[:500]})
            response.raise_for_status()
            if lose_ack and batch_number == lose_ack_batch:
                # Server committed; client never records acknowledgement.
                raise ConnectionError("simulated lost import acknowledgement")
            accepted = set(response.json()["accepted"] + response.json()["duplicates"])
            self.outbox = [event for event in self.outbox if event["event_id"] not in accepted]
            self.save()
        await self.pull()
        self.save()

    def save(self):
        target = self.root / "client-state.json"
        staging = self.root / "client-state.tmp"
        staging.write_bytes(canonical_json({"cursor": self.cursor, "clock": self.clock, "sequence": self.sequence,
                                           "outbox": self.outbox, "recommendation_state": self.engine.export_state()}))
        for attempt in range(8):
            try:
                staging.replace(target)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                # Windows antivirus may briefly share-lock a newly saved file.
                # Retry the atomic replacement; never expose a partial state.
                time.sleep(.01 * (attempt + 1))
