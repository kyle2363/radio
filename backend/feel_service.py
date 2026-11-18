"""Desktop feels (checkpoints 9D-9E): the server authors feel events like a device.

Desktop edits are ``feel_*`` events with ``device_id="server"`` in the shared
portable ledger, so phones receive them on their next sync exactly like events
from another phone. Scores always come from ``portable.feel`` over the whole
append-only evidence index, never from a second desktop-only store.
"""
import hashlib
import random
import time
import uuid

import numpy as np

from db import database
from portable import feel as rules
from portable_sync import canonical

# Mirrors android FeelCatalog.starters (name, colour, meaning, hint energy range).
STARTERS = [
    ("hype", "Hype", "#FB923C", "Pump-up songs you turn all the way up", (0.7, 1.0)),
    ("wind-down", "Wind down", "#5EEAD4", "Calm songs for the end of the day", (0.0, 0.35)),
    ("sad-good", "Sad-good", "#C4B5FD", "Sad songs that hurt in a good way", None),
    ("nostalgic", "Nostalgic", "#CBD5E1", "Songs that take you back to a time or memory", None),
    ("focus", "Focus", "#A5B4FC", "Background songs to work or study to", None),
    ("drive", "Drive", "#FCD34D", "Steady songs for the road or commute", (0.5, 0.9)),
    ("main-character", "Main character", "#F9A8D4", "Makes you feel like you're in a film", None),
    ("late-night", "Late night", "#93C5FD", "Moody, quiet songs for after midnight", None),
    ("butterflies", "Butterflies", "#FDA4AF", "Crushes, new love and being in love", None),
    ("heartache", "Heartache", "#D8B4FE", "Breakups and missing someone", None),
    ("euphoria", "Euphoria", "#FDE047", "Big builds and drops that make you feel huge", None),
    ("floating", "Floating", "#BAE6FD", "Dreamy, weightless songs to drift to", None),
    ("epic", "Epic", "#FCA5A5", "Huge, dramatic, boss-fight songs", None),
    ("second-wind", "Second wind", "#86EFAC", "Lifts you back up when you're feeling low", None),
]
ENERGY = {"id": rules.ENERGY_FEEL_ID, "name": "Energy", "color": "#FDBA74", "scale": True, "archived": False,
          "meaning": "How much a song lifts you, from Cold to Hot"}
SUGGESTION_MODES = ("off", "song_page", "after_session")
REVIEW_DAYS = 182.5
SUGGEST_THRESHOLD = 0.6
SUGGEST_MIN_SIMILARITY = 0.75  # nearest rated song must be this close
STATION_LIMIT = 300


def starter_id(key):
    """``java.util.UUID.nameUUIDFromBytes("radio-feel:<key>")`` so starters match the phone."""
    if key == "energy":
        return rules.ENERGY_FEEL_ID
    return str(uuid.UUID(bytes=hashlib.md5(f"radio-feel:{key}".encode()).digest(), version=3))


def starter_feels():
    return [{"key": key, "id": starter_id(key), "name": name, "color": color, "meaning": meaning,
             "hint_energy": hint} for key, name, color, meaning, hint in STARTERS]


def _chroma_vectors():
    from db.chroma import get_chroma_collection
    got = get_chroma_collection("songs").get(include=["embeddings"])
    return {str(i): v for i, v in zip(got.get("ids") or [], got.get("embeddings") or []) if v is not None}


def _song_int(key):
    return int(key) if key.isdecimal() else None


class FeelService:
    def __init__(self, store, vectors=_chroma_vectors, now=time.time):
        self._store = store
        self._vectors = vectors
        self._now = now

    # ── definitions ────────────────────────────────────────────────────
    def _definitions(self):
        return {fid: value for fid, value in self._store.profile()["feels"].items()}

    def list_feels(self, include_archived=False):
        definitions = self._definitions()
        starters = {s["id"]: s for s in starter_feels()}
        energy = dict(ENERGY, **{k: v for k, v in definitions.get(rules.ENERGY_FEEL_ID, {}).items() if k in ("name", "color")})
        feels = [dict(energy, builtin=True)]
        for fid, value in sorted(definitions.items(), key=lambda kv: kv[1]["name"].lower()):
            if fid == rules.ENERGY_FEEL_ID or (value["archived"] and not include_archived):
                continue
            feels.append({"id": fid, **value, "builtin": fid in starters,
                          "meaning": starters.get(fid, {}).get("meaning")})
        for feel in feels:
            states = self._states(feel["id"])
            feel["members"] = sum(1 for s in states.values() if s["member"])
            feel["settled"] = round(rules.settled_percent(states), 1)
        return feels

    def feel(self, feel_id):
        for feel in self.list_feels(include_archived=True):
            if feel["id"] == feel_id:
                return feel
        raise KeyError("Unknown feel")

    def define(self, feel_id, *, name=None, color=None, archived=None):
        current = ENERGY if feel_id == rules.ENERGY_FEEL_ID else self._definitions().get(feel_id) if feel_id else None
        if feel_id and current is None:
            raise KeyError("Unknown feel")
        feel_id = feel_id or str(uuid.uuid4())
        value = {"name": (name if name is not None else (current or {}).get("name", "")).strip(),
                 "color": (color or (current or {}).get("color", "")).upper(),
                 "scale": bool((current or {}).get("scale", False)),
                 "archived": bool(archived if archived is not None else (current or {}).get("archived", False))}
        if not rules.valid_definition(feel_id, value):
            raise ValueError("Invalid feel: name 1-40 characters, colour #RRGGBB; Energy cannot be archived")
        self._author("feel_defined", feel_id, value=value)
        return feel_id

    def adopt_starter(self, key):
        starter = next((s for s in starter_feels() if s["key"] == key), None)
        if starter is None:
            raise KeyError("Unknown starter feel")
        if starter["id"] not in self._definitions():
            self._author("feel_defined", starter["id"],
                         value={"name": starter["name"], "color": starter["color"], "scale": False, "archived": False})
        return starter["id"]

    # ── evidence ───────────────────────────────────────────────────────
    def _require_feel(self, feel_id):
        if feel_id != rules.ENERGY_FEEL_ID and feel_id not in self._definitions():
            raise KeyError("Unknown feel")

    @staticmethod
    def _require_song(song_id):
        if type(song_id) is not int or database.get_song(song_id) is None:
            raise ValueError("Unknown song")

    def rate(self, feel_id, song_id, value, at=None):
        self._require_feel(feel_id)
        self._require_song(song_id)
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError("Rating must be within [0,1]")
        self._author("feel_rated", feel_id, song_id=str(song_id), value=float(value), at=at)
        if feel_id == rules.ENERGY_FEEL_ID:
            self._sync_user_energy(song_id)

    def compare(self, feel_id, a, b, outcome):
        """outcome: "a", "b", "tie", or "doesnt_fit_a"/"doesnt_fit_b" (rates that song out)."""
        self._require_feel(feel_id)
        self._require_song(a)
        self._require_song(b)
        if a == b:
            raise ValueError("A song cannot be compared with itself")
        if outcome in ("doesnt_fit_a", "doesnt_fit_b"):
            return self.rate(feel_id, a if outcome.endswith("a") else b, 0.0)
        if outcome not in ("a", "b", "tie"):
            raise ValueError("Outcome must be a, b, tie or doesnt_fit_a/b")
        winner, loser = (b, a) if outcome == "b" else (a, b)
        self._author("feel_compared", feel_id, song_id=str(winner), other_song_id=str(loser),
                     value="tie" if outcome == "tie" else "win")
        if feel_id == rules.ENERGY_FEEL_ID:
            self._sync_user_energy(a)
            self._sync_user_energy(b)

    def _author(self, kind, feel_id, at=None, **fields):
        catalog_id = self._store.catalog_id
        with self._store.transaction() as conn:
            clock = conn.execute("SELECT COALESCE(MAX(clock),0) FROM portable_events").fetchone()[0] + 1
            event = {"protocol_version": 1, "device_id": "server", "event_id": str(uuid.uuid4()),
                     "logical_clock": clock, "kind": kind, "feel_id": feel_id,
                     "occurred_at": float(self._now() if at is None else at), **fields}
            if "song_id" in fields:
                event["catalog_id"] = catalog_id
            self._store.validate(event, "server", catalog_id)
            conn.execute("INSERT INTO portable_events(device_id,event_id,clock,payload) VALUES ('server',?,?,?)",
                         (event["event_id"], clock, canonical(event)))
            self._store._project_feel(conn, event)
        return event

    def _sync_user_energy(self, song_id):
        state = self._states(rules.ENERGY_FEEL_ID).get(str(song_id))
        if state is None:
            return
        with database.get_connection_cm() as conn:
            row = conn.execute("SELECT user_energy FROM songs WHERE id=?", (song_id,)).fetchone()
            if row and rules.energy_changed(row[0], state["score"]):
                conn.execute("UPDATE songs SET user_energy=?, user_energy_count=MAX(1,COALESCE(user_energy_count,0)),"
                             " user_energy_updated_at=? WHERE id=?", (round(state["score"], 4), self._now(), song_id))
                conn.commit()

    def _evidence(self, feel_id):
        with database.get_connection_cm() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT kind,song_key,other_key,value,tie,weight,occurred_at FROM portable_feel_evidence "
                "WHERE feel_id=? ORDER BY occurred_at,event_id", (feel_id,))]

    def _states(self, feel_id, evidence=None):
        evidence = self._evidence(feel_id) if evidence is None else evidence
        ratings = [{"song": e["song_key"], "value": e["value"], "weight": e["weight"], "at": e["occurred_at"]}
                   for e in evidence if e["kind"] == "rated"]
        comparisons = [{"winner": e["song_key"], "loser": e["other_key"], "tie": bool(e["tie"]), "at": e["occurred_at"]}
                       for e in evidence if e["kind"] == "compared"]
        return rules.score_feel(ratings, comparisons, self._now())

    # ── views ──────────────────────────────────────────────────────────
    @staticmethod
    def _songs(ids):
        if not ids:
            return {}
        with database.get_connection_cm() as conn:
            marks = ",".join("?" * len(ids))
            return {r["id"]: dict(r) for r in conn.execute(
                f"SELECT id,artist,title,filepath,energy,bpm FROM songs WHERE id IN ({marks})", list(ids))}

    def _rows(self, entries):
        """entries: [(song_id, extra dict)] -> song rows with title/artist, unknown songs dropped."""
        songs = self._songs([sid for sid, _ in entries])
        return [{"song_id": sid, "artist": songs[sid]["artist"] or "", "title": songs[sid]["title"] or "",
                 "energy": songs[sid]["energy"], **extra} for sid, extra in entries if sid in songs]

    def ranking(self, feel_id):
        self._require_feel(feel_id)
        states = self._states(feel_id)
        ordered = sorted(((s, st) for s, st in states.items() if _song_int(s) is not None),
                         key=lambda t: (-t[1]["score"], t[0]))
        rows = self._rows([(_song_int(s), {"score": st["score"], "confidence": st["confidence"], "member": st["member"]})
                           for s, st in ordered])
        return {"feel": self.feel(feel_id), "songs": rows, "settled": round(rules.settled_percent(states), 1)}

    def pairs(self, feel_id, count=10):
        self._require_feel(feel_id)
        states = {s: st for s, st in self._states(feel_id).items() if _song_int(s) is not None}
        played = self._played_ids()
        pairs = rules.uncertain_pairs(states, played, max(count * 2, count))
        random.shuffle(pairs)
        rows = {r["song_id"]: r for r in self._rows([(_song_int(s), {}) for s in states])}
        out = []
        for a, b in pairs:
            a, b = _song_int(a), _song_int(b)
            if a in rows and b in rows:
                out.append({"a": rows[a], "b": rows[b]})
            if len(out) == count:
                break
        return out

    @staticmethod
    def _played_ids():
        with database.get_connection_cm() as conn:
            return {str(r[0]) for r in conn.execute(
                "SELECT DISTINCT song_id FROM play_events WHERE signal='playing' "
                "AND julianday(played_at) > julianday('now') - 90")}

    def _neighbours(self, states):
        """numpy port of ``rules.neighbour_scores`` (same formula, library-sized)."""
        vectors = self._vectors()
        rated = sorted(s for s in states if s in vectors)
        if not rated:
            return {}
        others = sorted(s for s in vectors if s not in states)
        if not others:
            return {}
        r = np.asarray([vectors[s] for s in rated], dtype=float)
        o = np.asarray([vectors[s] for s in others], dtype=float)
        r /= np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-12)
        o /= np.maximum(np.linalg.norm(o, axis=1, keepdims=True), 1e-12)
        sims = o @ r.T
        score = np.asarray([states[s]["score"] for s in rated])
        conf = np.asarray([states[s]["confidence"] for s in rated])
        k = min(rules.NEIGHBOURS, len(rated))
        result = {}
        for row, song in enumerate(others):
            idx = np.argsort(-sims[row], kind="stable")[:k]
            idx = idx[sims[row, idx] > 0]
            if not len(idx):
                continue
            sim = sims[row, idx]
            result[song] = {"score": float((sim * score[idx]).sum() / sim.sum()),
                            "confidence": float(sim[0] * conf[idx].mean() * rules.NEIGHBOUR_CONFIDENCE),
                            "member": False, "similarity": float(sim[0])}
        return result

    def sprint(self, feel_id, count=20, randomness=0.2):
        """A deck of unrated candidates: neighbours of members first, cold-start hint, then random."""
        self._require_feel(feel_id)
        states = self._states(feel_id)
        borrowed = self._neighbours(states)
        ranked = sorted((s for s, st in borrowed.items() if _song_int(s) is not None),
                        key=lambda s: (-borrowed[s]["score"] * (1 + borrowed[s]["confidence"]), s))
        with database.get_connection_cm() as conn:
            pool = [r[0] for r in conn.execute("SELECT id FROM songs WHERE ingest_phase='clustered' ORDER BY id")]
            hint = next((s["hint_energy"] for s in starter_feels() if s["id"] == feel_id), None)
            hinted = [r[0] for r in conn.execute(
                "SELECT id FROM songs WHERE ingest_phase='clustered' AND energy BETWEEN ? AND ? ORDER BY RANDOM()",
                hint)] if hint else []
        rated = {_song_int(s) for s in states}
        n_random = int(round(count * randomness))
        deck, seen = [], set(rated)
        for source in ([_song_int(s) for s in ranked][:count - n_random], hinted, random.sample(pool, len(pool))):
            for sid in source:
                if sid not in seen and len(deck) < count:
                    deck.append(sid)
                    seen.add(sid)
        return self._rows([(sid, {"guess": borrowed.get(str(sid), {}).get("score")}) for sid in deck])

    def station_ids(self, feel_id, limit=STATION_LIMIT):
        """Members by rank, then neighbours that lean in, by score x confidence."""
        states = self._states(feel_id)
        members = sorted((s for s, st in states.items() if st["member"] and _song_int(s) is not None),
                         key=lambda s: (-states[s]["score"], s))
        borrowed = self._neighbours(states)
        leaning = sorted((s for s, st in borrowed.items() if st["score"] > 0.5 and _song_int(s) is not None),
                         key=lambda s: (-borrowed[s]["score"] * borrowed[s]["confidence"], s))
        return [_song_int(s) for s in members + leaning][:limit]

    # ── station and settings ───────────────────────────────────────────
    def set_station(self, feel_id):
        if feel_id is not None:
            self._require_feel(feel_id)
        database.set_setting("feel_station", feel_id or "")

    def station(self):
        return database.get_setting("feel_station") or None

    def active_station_ids(self):
        feel_id = self.station()
        if not feel_id:
            return None
        try:
            self._require_feel(feel_id)
        except KeyError:
            return None
        return self.station_ids(feel_id)

    @staticmethod
    def suggestion_mode():
        mode = database.get_setting("feel_suggestions") or "song_page"
        return mode if mode in SUGGESTION_MODES else "song_page"

    @staticmethod
    def set_suggestion_mode(mode):
        if mode not in SUGGESTION_MODES:
            raise ValueError("Suggestions must be off, song_page or after_session")
        database.set_setting("feel_suggestions", mode)

    # ── 9E: suggestions, review, history ───────────────────────────────
    def suggestions(self, song_id, limit=3):
        """Feels this unrated song probably belongs to, from its rated neighbours."""
        key, out = str(song_id), []
        for feel in self.list_feels():
            states = self._states(feel["id"])
            if key in states or feel["scale"]:
                continue
            guess = self._neighbours(states).get(key)
            if guess and guess["score"] >= SUGGEST_THRESHOLD and guess["similarity"] >= SUGGEST_MIN_SIMILARITY:
                out.append({"feel": feel, "score": guess["score"], "confidence": guess["confidence"]})
        out.sort(key=lambda s: -s["score"])
        return out[:limit]

    def session_suggestions(self, limit=10):
        """After a session: songs just played that look like a feel but aren't rated in it."""
        with database.get_connection_cm() as conn:
            row = conn.execute("SELECT session_id FROM play_events WHERE session_id IS NOT NULL "
                               "ORDER BY played_at DESC LIMIT 1").fetchone()
            if not row:
                return []
            ids = [r[0] for r in conn.execute(
                "SELECT song_id FROM play_events WHERE session_id=? AND signal IN ('playing','completed') "
                "GROUP BY song_id ORDER BY MAX(played_at) DESC", (row[0],))]
        out = []
        for sid in ids:
            found = self.suggestions(sid, limit=1)
            if found:
                out.extend(self._rows([(sid, {"feel": found[0]["feel"], "score": found[0]["score"]})]))
            if len(out) == limit:
                break
        return out

    def review(self, feel_id, days=REVIEW_DAYS):
        """Members whose newest evidence is older than ``days``: Keep / Remove / Re-rank."""
        self._require_feel(feel_id)
        evidence = self._evidence(feel_id)
        states = self._states(feel_id, evidence)
        latest = {}
        for e in evidence:
            for key in (e["song_key"], e["other_key"]):
                if key:
                    latest[key] = max(latest.get(key, 0.0), e["occurred_at"])
        cutoff = self._now() - days * rules.DAY_SECONDS
        stale = sorted((s for s, st in states.items()
                        if st["member"] and latest.get(s, 0) < cutoff and _song_int(s) is not None),
                       key=lambda s: latest[s])
        return self._rows([(_song_int(s), {"score": states[s]["score"], "confirmed_at": latest[s]}) for s in stale])

    def history(self, song_id):
        """Per feel with evidence: member now?, since when (earliest positive evidence), last touched."""
        key, out = str(song_id), []
        for feel in self.list_feels(include_archived=True):
            evidence = [e for e in self._evidence(feel["id"]) if key in (e["song_key"], e["other_key"])]
            if not evidence:
                continue
            state = self._states(feel["id"]).get(key, {})
            positive = [e["occurred_at"] for e in evidence
                        if (e["kind"] == "rated" and e["value"] >= 0.5)
                        or (e["kind"] == "compared" and e["song_key"] == key)]
            out.append({"feel": feel, "member": bool(state.get("member")), "score": state.get("score"),
                        "since": min(positive) if positive else None,
                        "last": max(e["occurred_at"] for e in evidence)})
        out.sort(key=lambda h: (not h["member"], h["since"] or h["last"]))
        return out
