"""Pure deterministic offline reference; no application globals, services or paths.

All timestamps are caller-supplied Unix seconds. See OFFLINE_RECOMMENDATIONS.md
for the language-neutral scoring contract and intentional desktop differences.
"""

from copy import deepcopy
import hashlib
import math

import numpy as np

from .catalog import Catalog, canonical_json

VERSION = 1
LAYERS = ((10, .40, .85), (50, .25, .92), (200, .20, .96), (2000, .15, .99))
MOODS = {"chill": (.25, .55), "neutral": (.5, .5), "energetic": (.8, .65),
         "focus": (.4, .5), "melancholy": (.3, .25), "discovery": (.55, .55)}
DEFAULTS = {"audio_blend": .65, "exploration_level": .3, "mood": "neutral",
            "genres": [], "folders": [], "collections": [], "favorites_only": False,
            "artist_penalty": .12, "recent_window": 1800, "skip_window": 86400,
            "vibe_lock": None, "energy_target": None, "energy_direction": "stable"}


def _unit(vector):
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else None


def _number(value, default=.5):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _labels(value):
    if isinstance(value, list):
        return {str(v).casefold() for v in value}
    if isinstance(value, str):
        return {v.strip().casefold() for v in value.split(",") if v.strip()}
    return set()


def allocate_portable_slots(candidates, current_song=None, *, vibe_lock=False, exploration_level=.3):
    """Portable copy of desktop slot semantics (including existing fallback quirks).

    Inputs already ranked; no application imports. With no constellation/persona/
    archetype and effective current energy supplied, this matches allocate_slots.
    Selection diversity and seeded tie breaks happen before this function.
    """
    result = {name: [] for name in ("exploit", "explore", "wildcard", "context")}
    ordered = sorted(candidates, key=lambda c: c.get("score", .5), reverse=True)
    used = set()
    def take(slot, count=1, predicate=lambda c: True):
        for candidate in ordered:
            sid = candidate["song_id"]
            if sid not in used and predicate(candidate):
                result[slot].append(candidate)
                used.add(sid)
                if len(result[slot]) >= count:
                    break
    if vibe_lock:
        take("exploit", 4)
        take("explore")
        return result
    take("exploit", max(1, int(2 * (1 - exploration_level * .5))))
    cluster = current_song.get("cluster_id") if current_song else None
    different = lambda c: c.get("cluster_id") != cluster
    take("explore", 1 + int(exploration_level > .6), different)
    if not result["explore"]:
        if cluster is None:
            remaining = [c for c in ordered if c["song_id"] not in used]
            counts = {}
            for c in remaining:
                cid = c.get("cluster_id", -1)
                counts[cid] = counts.get(cid, 0) + 1
            if counts:
                rarest = min(counts, key=counts.get)
                take("explore", predicate=lambda c: c.get("cluster_id") == rarest)
        if not result["explore"]:
            take("explore")
    # Desktop currently chooses one wildcard even when its nominal target is zero.
    take("wildcard", predicate=different)
    if not result["wildcard"]:
        if cluster is None:
            remaining = [c for c in ordered if c["song_id"] not in used]
            counts = {}
            for c in remaining:
                cid = c.get("cluster_id", -1)
                counts[cid] = counts.get(cid, 0) + 1
            if counts:
                rarest = min(counts, key=counts.get)
                take("wildcard", predicate=lambda c: c.get("cluster_id") == rarest)
        if not result["wildcard"]:
            take("wildcard")
    if current_song:
        target = _number(current_song.get("energy"))
        take("context", predicate=lambda c: abs(_number(c.get("energy")) - target) < .2)
    if not result["context"]:
        take("context")
    return result


class OfflineRecommender:
    """Device-local reference engine. Inputs/state are copied, never mutated.

    catalog: validated Catalog. local_tracks: iterable of {local_id, song_id?,
    playable?, metadata...}. mappings optionally maps local_id -> catalog song_id
    or is a match_catalog report (only confirmed/high_confidence rows are used).
    Unmapped tracks get local:<local_id> identities and no invented vectors.
    settings/state are portable JSON. rank/recommend require explicit now.
    """

    def __init__(self, catalog: Catalog, local_tracks, mappings=None, state=None, settings=None, seed=0):
        self.catalog = catalog
        self.seed = int(seed)
        mappings = mappings or {}
        if "results" in mappings:
            mappings = {r["local_id"]: r["song_id"] for r in mappings["results"]
                        if r["status"] in ("confirmed", "high_confidence") and r.get("song_id")}
        self.tracks = {}
        seen_local = set()
        for local in sorted(local_tracks, key=lambda row: str(row["local_id"])):
            lid = str(local["local_id"])
            if lid in seen_local:
                raise ValueError("Duplicate local identity")
            seen_local.add(lid)
            if not local.get("playable", True):
                continue
            sid = str(mappings.get(lid) or local.get("song_id") or "local:" + lid)
            row = {**deepcopy(catalog.songs.get(sid, {})), **deepcopy(local),
                   "id": sid, "song_id": sid, "local_id": lid}
            self.tracks.setdefault(sid, row)
        self.state = deepcopy(state) if state is not None else {}
        explicit_mood = self.state.get("mood")
        if self.state.get("version", VERSION) != VERSION:
            raise ValueError("Unsupported offline state version")
        for key, value in {"version": VERSION, "events": [], "hearts": [], "blacklist": [],
                           "user_energy": {}, "settings": {}, "energy_history": [],
                           "mood": "neutral", "skip_streak": 0, "current": None,
                           "queue": [], "upcoming": []}.items():
            self.state.setdefault(key, deepcopy(value))
        self.settings = {**DEFAULTS, **self.state["settings"], **(settings or {})}
        if explicit_mood is None:
            self.state["mood"] = self.settings["mood"]
        self.state["settings"] = deepcopy(self.settings)
        self._event_ids = {str(e["event_id"]) for e in self.state["events"] if e.get("event_id")}
        self._groups = {}
        for key, desc in catalog.descriptors.items():
            # Matching dimensions alone never establishes model compatibility.
            group = (desc["space"], desc["model"], desc["model_version"], desc["dimension"])
            self._groups.setdefault(group, {})[key[0]] = _unit(catalog.vectors[key].astype(np.float32))
        self._taste_cache = None

    def export_state(self):
        """Copy JSON state for durable device storage; has no server/session objects."""
        result = deepcopy(self.state)
        result["settings"] = deepcopy(self.settings)
        return result

    def _energy(self, sid):
        row = self.tracks.get(sid, self.catalog.songs.get(sid, {}))
        base = _number(row.get("energy"))
        user = self.state["user_energy"].get(sid, row.get("user_energy"))
        effective = base if user is None else base * .7 + _number(user) * .3
        return min(1., max(0., effective))

    def _target_energy(self):
        explicit = self.settings.get("energy_target")
        mood = self.state.get("mood") or self.settings["mood"]
        target = _number(explicit, MOODS.get(mood, MOODS["neutral"])[0])
        direction = {"rising": .15, "falling": -.15}.get(self.settings["energy_direction"], 0.)
        target += direction
        history = self.state["energy_history"][-5:]
        if history:
            last = _number(history[-1])
            target = min(last + .25, max(last - .25, target))
        return min(1., max(0., target))

    def _taste(self):
        if self._taste_cache is not None:
            return self._taste_cache
        signals = {"completed": 1., "skipped": -.6, "replayed": 1.3,
                   "hearted": 1.5, "unhearted": -.3, "disliked": -1.}
        events = sorted(self.state["events"], key=lambda e: (e["timestamp"], str(e["event_id"])))
        relevant = [e for e in events if e["type"] in signals][-2000:]
        taste = {}
        weights = (.15, .2, .25, .4) if self.state["skip_streak"] >= 3 else tuple(layer[1] for layer in LAYERS)
        for group, vectors in self._groups.items():
            blended = np.zeros(group[3], dtype=np.float32)
            for (window, _, decay), layer_weight in zip(LAYERS, weights):
                total = np.zeros(group[3], dtype=np.float32)
                denominator = 0.
                for position, event in enumerate(reversed(relevant[-window:])):
                    vector = vectors.get(str(event["song_id"]))
                    if vector is not None:
                        weight = signals[event["type"]] * decay ** position
                        total += vector * weight
                        denominator += abs(weight)
                if denominator:
                    blended += total / denominator * layer_weight
            # Favorites remain useful after the event ages out of the 2,000-signal window.
            favorite_vectors = [vectors[sid] for sid in self.state["hearts"] if sid in vectors]
            if favorite_vectors:
                blended += np.mean(favorite_vectors, axis=0) * .15
            taste[group] = _unit(blended)
        self._taste_cache = taste
        return taste

    def _tie(self, sid):
        # Portable PRNG substitute: reproducible in Kotlin and across Python versions.
        return hashlib.sha256(f"{self.seed}:{sid}".encode("utf-8")).hexdigest()

    def rank(self, *, now):
        """Return eligible score breakdowns in deterministic descending order.

        Hard local availability/blacklist/filter rules are never relaxed. Recency,
        skips and current-song exclusion are soft; a one-song library cannot stall.
        """
        now = _number(now, 0.)
        taste = self._taste()
        similarities = {}
        vibe = str(self.settings.get("vibe_lock") or "")
        for group, vectors in self._groups.items():
            query = taste[group]
            anchor = vectors.get(vibe)
            if anchor is not None:
                query = _unit(anchor * .8 + (query * .2 if query is not None else 0))
            if query is None:
                continue
            ids = [sid for sid in self.tracks if sid in vectors]
            if ids:
                matrix = np.stack([vectors[sid] for sid in ids])
                scores = matrix @ query
                for sid, score in zip(ids, scores):
                    similarities.setdefault(sid, {})[group[0]] = float(score)
        recent, skipped = {}, {}
        for event in self.state["events"]:
            sid = str(event.get("song_id", ""))
            timestamp = _number(event.get("timestamp"), 0.)
            if event["type"] == "started":
                recent[sid] = max(recent.get(sid, -math.inf), timestamp)
            elif event["type"] in ("skipped", "disliked"):
                skipped[sid] = max(skipped.get(sid, -math.inf), timestamp)
        blacklist = set(self.state["blacklist"])
        hearts = set(self.state["hearts"])
        target = self._target_energy()
        mood = self.state.get("mood") or self.settings["mood"]
        valence = MOODS.get(mood, MOODS["neutral"])[1]
        current = self.tracks.get(str(self.state["current"]), {})
        current_artist = str(current.get("artist") or "").casefold()
        ranked = []
        for sid, row in self.tracks.items():
            if sid in blacklist or (self.settings["favorites_only"] and sid not in hearts):
                continue
            if self.settings["genres"] and not _labels(self.settings["genres"]) & \
                    (_labels(row.get("genres")) | _labels(row.get("genre_tags"))):
                continue
            if self.settings["folders"] and str(row.get("folder") or "").casefold() not in _labels(self.settings["folders"]):
                continue
            if self.settings["collections"] and not _labels(self.settings["collections"]) & _labels(row.get("collections")):
                continue
            sims = similarities.get(sid, {})
            blend = min(1., max(0., _number(self.settings["audio_blend"], .65)))
            if "text" in sims and "audio" in sims:
                similarity = sims["text"] * (1 - blend) + sims["audio"] * blend
            elif sims:
                similarity = next(iter(sims.values()))
            else:
                similarity = 0.  # Explicit metadata fallback, never a fabricated vector.
            energy_match = 1 - abs(self._energy(sid) - target)
            mood_match = 1 - abs(_number(row.get("valence")) - valence)
            recent_penalty = .35 * max(0., 1 - max(0., now - recent.get(sid, -math.inf)) /
                                       max(1., _number(self.settings["recent_window"], 1800)))
            skip_penalty = .55 * max(0., 1 - max(0., now - skipped.get(sid, -math.inf)) /
                                     max(1., _number(self.settings["skip_window"], 86400)))
            artist_penalty = _number(self.settings["artist_penalty"], .12) if current_artist and \
                str(row.get("artist") or "").casefold() == current_artist else 0.
            current_penalty = .5 if sid == str(self.state["current"]) and len(self.tracks) > 1 else 0.
            score = (.60 * similarity + .20 * energy_match + .10 * mood_match +
                     (.10 if sid in hearts or row.get("favorite") else 0.) - recent_penalty -
                     skip_penalty - artist_penalty - current_penalty)
            ranked.append({"song_id": sid, "local_id": row["local_id"], "score": round(score, 8),
                           "text_similarity": sims.get("text"), "audio_similarity": sims.get("audio"),
                           "energy": self._energy(sid), "energy_match": energy_match, "mood_match": mood_match,
                           "recent_penalty": recent_penalty, "skip_penalty": skip_penalty,
                           "artist_penalty": artist_penalty, "cluster_id": row.get("cluster_id"),
                           "vector_fallback": not bool(sims)})
        ranked.sort(key=lambda row: (-row["score"], self._tie(row["song_id"]), row["song_id"]))
        return ranked

    def recommend(self, count=5, *, now):
        """Pure selection, including slot explanations; does not record a play."""
        if type(count) is not int or count < 0 or count > 100:
            raise ValueError("count must be an integer between 0 and 100")
        ranked = self.rank(now=now)
        current = deepcopy(self.tracks.get(str(self.state["current"])))
        if current:
            current["energy"] = self._energy(current["id"])
        slots = allocate_portable_slots(ranked, current,
                                       vibe_lock=bool(self.settings.get("vibe_lock")),
                                       exploration_level=_number(self.settings["exploration_level"], .3))
        picks = []
        artists = {}
        for slot, rows in slots.items():
            for row in rows:
                picks.append({**row, "slot": slot, "reason": f"Portable {slot}; deterministic blended ranking"})
        # Slot stage mirrors desktop; subsequent greedy artist diversity is explicit.
        remaining = [row for row in ranked if row["song_id"] not in {p["song_id"] for p in picks}]
        picks.extend({**row, "slot": "fallback", "reason": "Playable fallback"} for row in remaining)
        for pick in picks[:count]:
            artist = str(self.tracks[pick["song_id"]].get("artist") or "").casefold()
            artists[artist] = artists.get(artist, 0) + 1
        # Preserve slots but swap repeated artists for comparably scored unselected tracks.
        selected = picks[:min(count, len(picks))]
        used = {pick["song_id"] for pick in selected}
        seen_artists = set()
        for index, pick in enumerate(selected):
            artist = str(self.tracks[pick["song_id"]].get("artist") or "").casefold()
            if artist and artist in seen_artists:
                replacement = next((row for row in ranked if row["song_id"] not in used and
                                    str(self.tracks[row["song_id"]].get("artist") or "").casefold() not in seen_artists and
                                    row["score"] >= pick["score"] - .12), None)
                if replacement:
                    used.remove(pick["song_id"])
                    used.add(replacement["song_id"])
                    selected[index] = {**replacement, "slot": pick["slot"], "reason": "Artist diversity substitution"}
                    artist = str(self.tracks[replacement["song_id"]].get("artist") or "").casefold()
            if artist:
                seen_artists.add(artist)
        return selected

    def feedback(self, event):
        """Apply actual local feedback immediately; duplicate event_ids return False.

        event={event_id?, type, song_id?, timestamp, value?, reason?}. Preparing or
        prefetching is deliberately not an event type. `started` means actual audio.
        Mutable shared state should be materialized by the sync conflict resolver
        before restoring it here; this local method applies device action order.
        """
        event = deepcopy(event)
        kind = event.get("type")
        allowed = {"started", "completed", "skipped", "replayed", "hearted", "unhearted",
                   "disliked", "blacklisted", "unblacklisted", "energy", "preference", "mood"}
        if kind not in allowed or "timestamp" not in event or not math.isfinite(float(event["timestamp"])):
            raise ValueError("Unsupported feedback or missing finite timestamp")
        if kind not in ("preference", "mood") and "song_id" not in event:
            raise ValueError("Feedback requires song_id")
        event["timestamp"] = float(event["timestamp"])
        if "song_id" in event:
            event["song_id"] = str(event["song_id"])
        event.setdefault("event_id", "local:" + hashlib.sha256(canonical_json([len(self.state["events"]), event])).hexdigest())
        eid = str(event["event_id"])
        if eid in self._event_ids:
            return False
        sid = event.get("song_id")
        if kind == "energy":
            value = float(event["value"])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Energy must be between zero and one")
            self.state["user_energy"][sid] = value
        elif kind == "preference":
            changes = event.get("value")
            if not isinstance(changes, dict) or not set(changes) <= set(DEFAULTS):
                raise ValueError("Unknown preference")
            self.settings.update(deepcopy(changes))
            self.state["settings"] = deepcopy(self.settings)
            if "mood" in changes:
                self.state["mood"] = changes["mood"]
        elif kind == "mood":
            if event.get("value") not in MOODS:
                raise ValueError("Unknown mood")
            self.state["mood"] = event["value"]
        elif kind == "hearted":
            self.state["hearts"] = sorted(set(self.state["hearts"]) | {sid})
        elif kind == "unhearted":
            self.state["hearts"] = [value for value in self.state["hearts"] if value != sid]
        elif kind in ("disliked", "blacklisted"):
            self.state["blacklist"] = sorted(set(self.state["blacklist"]) | {sid})
        elif kind == "unblacklisted":
            self.state["blacklist"] = [value for value in self.state["blacklist"] if value != sid]
        elif kind == "started":
            self.state["current"] = sid
            self.state["energy_history"] = (self.state["energy_history"] + [self._energy(sid)])[-5:]
        elif kind == "completed":
            self.state["skip_streak"] = 0
        elif kind == "skipped":
            self.state["skip_streak"] += 1
            if self.state["skip_streak"] >= 3:
                self.state["mood"] = {"too_intense": "chill", "too_slow": "energetic"}.get(event.get("reason"), "discovery")
        self._event_ids.add(eid)
        self.state["events"].append(event)
        self._taste_cache = None
        return True

    def refresh_upcoming(self, count=5, *, now):
        """Replace recommendation preview only; committed queue/current survive."""
        self.state["upcoming"] = [p["song_id"] for p in self.recommend(count, now=now)]
        return list(self.state["upcoming"])

    def enqueue(self, song_id):
        """Append a playable track to the committed queue; no listening event."""
        sid = str(song_id)
        if sid not in self.tracks or sid in self.state["blacklist"]:
            raise ValueError("Track is unavailable or blacklisted")
        self.state["queue"].append(sid)

    def advance(self):
        """Choose next local track; caller records started only on actual playback."""
        for name in ("queue", "upcoming"):
            while self.state[name]:
                sid = self.state[name].pop(0)
                if sid in self.tracks and sid not in self.state["blacklist"]:
                    self.state["current"] = sid
                    return sid
        return None

    def replay(self):
        """Return current playable song; preserve both queues and record no play."""
        sid = self.state["current"]
        return sid if sid in self.tracks and sid not in self.state["blacklist"] else None

    def search(self, query):
        """Local metadata search includes phone-only songs without vectors."""
        words = str(query).casefold().split()
        return [deepcopy(row) for _, row in sorted(self.tracks.items())
                if all(word in " ".join(str(row.get(k) or "") for k in ("title", "artist", "album", "filename")).casefold()
                       for word in words)]
