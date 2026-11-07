"""Pure recommendation orchestration shared by the desktop queue and mobile requests.

``recommend`` runs retrieval -> transition merge -> filters -> slots -> reranker
for one anchor song. Every input is explicit: it reads no player, session,
broadcast or queue state, and it never writes history. ``QueueManager`` builds the
inputs from its own state (and keeps generation checks, broadcasts, talk
scheduling and recently-recommended bookkeeping); the connected-recommendation
service builds them from a paired device's context.

When ``allowed_ids`` is given, no stage can return a song outside it: the
retriever restricts every search, transition candidates are filtered, and the
reranker output and every fallback are filtered again.
"""

import heapq
import itertools
import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from db import database
from engine import reranker, retriever, slots

logger = logging.getLogger(__name__)

SLOT_ORDER = ("exploit", "explore", "wildcard", "context")
MAX_REASON = 200


def song_effective_energy(song):
    """Effective energy from a song dict (avoids an extra DB query)."""
    base = song.get("energy", 0.5)
    user = song.get("user_energy")
    n = song.get("user_energy_count", 0) or 0
    return round(0.7 * base + 0.3 * user, 4) if n > 0 and user is not None else base


class GateTimeout(Exception):
    """The LLM gate could not be taken within the caller's budget."""


class LlmPriorityGate:
    """One LLM call at a time; desktop rebuilds are admitted before mobile ones.

    Waiters are ordered by (priority, arrival). A running call is never
    preempted. A mobile waiter that cannot be admitted within its budget gives
    up and the caller falls back to deterministic ranking.
    """
    DESKTOP = 0
    MOBILE = 1

    def __init__(self):
        self._cond = threading.Condition()
        self._busy = False
        self._waiting = []
        self._seq = itertools.count()

    def acquire(self, priority, timeout=None):
        ticket = (priority, next(self._seq))
        end = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._cond:
            heapq.heappush(self._waiting, ticket)
            while self._busy or self._waiting[0] != ticket:
                remaining = None if end is None else end - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._waiting.remove(ticket)
                    heapq.heapify(self._waiting)
                    self._cond.notify_all()
                    return False
                self._cond.wait(remaining)
            heapq.heappop(self._waiting)
            self._busy = True
            return True

    def release(self):
        with self._cond:
            self._busy = False
            self._cond.notify_all()

    @contextmanager
    def slot(self, priority, timeout=None):
        if not self.acquire(priority, timeout):
            raise GateTimeout()
        try:
            yield
        finally:
            self.release()

    def snapshot(self):
        with self._cond:
            return {"busy": self._busy, "waiting": sorted(p for p, _ in self._waiting)}


DEFAULT_LLM_GATE = LlmPriorityGate()


@dataclass
class RecommendationInputs:
    db: Any
    anchor_song_id: int | None
    taste_vector: list | None = None
    audio_taste_vector: list | None = None
    text_repulsion_vector: list | None = None
    audio_repulsion_vector: list | None = None
    exclude_ids: Sequence[int] = ()
    mood_state: str = "neutral"
    mood_energy_range: tuple | None = None
    mood_valence_range: tuple | None = None
    energy_direction: str = "stable"
    skip_streak: int = 0
    skip_feedback: Sequence[dict] | None = None
    exploration_level: float = 0.3
    vibe_lock: dict | None = None
    subfolder_filter: list | None = None
    mtime_min: int | None = None
    mtime_max: int | None = None
    artist_mode: str | None = None
    artists: list | None = None
    genre_filter: str | None = None
    artist_filter: str | None = None
    reranking_enabled: bool = True
    mode: str = "radio"
    play_mode: str = "ai"
    archetype: str | None = None
    slot_bias: dict | None = None
    constellations: Any = None
    constellations_enabled: bool = False
    allowed_ids: Any = None            # frozenset[int] | None; None = whole library
    filler_current_song_id: int | None = None
    strict_exclusions: bool = False    # fillers also honour exclude_ids
    hour: int | None = None
    llm_budget_seconds: Any = None   # None = unbounded (desktop); seconds, or a callable returning seconds
    llm_gate: LlmPriorityGate | None = None
    gate_priority: int = LlmPriorityGate.DESKTOP
    step: Callable[[str], None] | None = None


@dataclass
class RecommendationResult:
    status: str                       # "ok" | "insufficient"
    ranked_by: str                    # "llm" | "fallback" | "deterministic"
    picks: list = field(default_factory=list)    # [{song_id, slot, reason}] in slot order
    entries: list = field(default_factory=list)  # desktop queue entries, reranker order
    candidates: list = field(default_factory=list)
    hop_pocket: Any = None


def _call_ranker(inputs, call):
    """Run the LLM call under the priority gate and the optional time budget."""
    gate, budget = inputs.llm_gate, inputs.llm_budget_seconds
    if callable(budget):            # evaluated now, after retrieval has used its share
        budget = budget()
    if budget is None:
        if gate is None:
            return call()
        with gate.slot(inputs.gate_priority):
            return call()
    box, done = {}, threading.Event()

    def run():
        try:
            if gate is not None and not gate.acquire(inputs.gate_priority, timeout=budget):
                return
            try:
                box["value"] = call()
            finally:
                if gate is not None:
                    gate.release()
        except BaseException as exc:  # noqa: BLE001 - reported as an LLM failure
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name="recommendation-llm").start()
    if not done.wait(max(0.0, budget)):
        logger.info("Reranker budget of %.1fs elapsed; using deterministic fallback", budget)
        return None
    if "error" in box:
        logger.warning("Reranker failed: %s", box["error"])
        return None
    return box.get("value")


def _clean_reason(value):
    return " ".join(str(value or "").split())[:MAX_REASON]


def recommend(inputs: RecommendationInputs) -> RecommendationResult:
    db = inputs.db
    allowed = None if inputs.allowed_ids is None else frozenset(inputs.allowed_ids)
    step = inputs.step or (lambda message: None)
    context_id = inputs.anchor_song_id
    if allowed is not None and not allowed:
        return RecommendationResult("insufficient", "deterministic")

    current_song = db.get_song(context_id) if context_id else None
    exclude_ids = list(inputs.exclude_ids)
    subfolder_filter, mtime_min, mtime_max = inputs.subfolder_filter, inputs.mtime_min, inputs.mtime_max
    artist_mode, artists = inputs.artist_mode, inputs.artists
    exploration_level = inputs.exploration_level
    vibe_lock = inputs.vibe_lock
    taste_vector = inputs.taste_vector

    step(f"Building candidate list (exclude={len(exclude_ids)})")
    extra = {} if allowed is None else {"allowed_ids": allowed}
    candidates = retriever.build_candidate_list(
        text_taste_vector=taste_vector,
        audio_taste_vector=inputs.audio_taste_vector,
        current_song_id=context_id,
        exploration_level=exploration_level,
        exclude_ids=exclude_ids,
        mood_energy_range=inputs.mood_energy_range,
        mood_valence_range=inputs.mood_valence_range,
        text_repulsion_vector=inputs.text_repulsion_vector,
        audio_repulsion_vector=inputs.audio_repulsion_vector,
        skip_feedback=inputs.skip_feedback,
        subfolder_filter=subfolder_filter,
        vibe_lock=vibe_lock,
        mood_state=inputs.mood_state,
        mtime_min=mtime_min, mtime_max=mtime_max,
        artist_mode=artist_mode, artists=artists,
        **extra,
    )
    if allowed is not None:
        candidates = [c for c in candidates if c["song_id"] in allowed]
    step(f"Candidates: {len(candidates)}")

    # Merge transition candidates into the pool
    if context_id:
        from engine import transitions
        trans_candidates = transitions.get_transition_candidates(context_id, n=15)
        seen_ids = {c["song_id"] for c in candidates}
        for tc in trans_candidates:
            if allowed is not None and tc["song_id"] not in allowed:
                continue
            if tc["song_id"] not in seen_ids and tc["song_id"] not in exclude_ids:
                song = db.get_song(tc["song_id"])
                if song:
                    candidates.append({
                        "song_id": tc["song_id"],
                        "score": tc["score"] * 0.8,
                        "energy": song_effective_energy(song),
                        "valence": song.get("valence", 0.5),
                        "cluster_id": song.get("cluster_id", -1),
                        "artist": song.get("artist", ""),
                        "title": song.get("title", ""),
                        "source": "transition",
                    })
                    seen_ids.add(tc["song_id"])
        step(f"Transition candidates merged: {len(candidates)} total")

    # Apply recommendation filters to transition candidates added post-hoc
    now_ts = time.time()
    filtered_candidates = []
    for c in candidates:
        cid = c["song_id"]
        if subfolder_filter:
            folder_ids = set(database.get_song_ids_in_subfolders(subfolder_filter))
            if cid not in folder_ids:
                continue
        if mtime_min is not None or mtime_max is not None:
            song = database.get_song(cid)
            if song:
                m = song.get("file_mtime")
                if m is None:
                    # Fallback: derive from subfolder
                    subf = song.get("subfolder", "")
                    all_subfolders = database.get_subfolders()
                    if all_subfolders and len(all_subfolders) > 1:
                        rank = all_subfolders.index(subf) if subf in all_subfolders else 0
                        known_mtimes = database.get_file_mtime_range()
                        min_known, max_known = known_mtimes if known_mtimes[0] else (0, 1)
                        if max_known > min_known:
                            frac = rank / (len(all_subfolders) - 1)
                            m = min_known + frac * (max_known - min_known)
                if m is not None:
                    age_days = (now_ts - m) / 86400.0
                    if mtime_min is not None and age_days < mtime_min:
                        continue
                    if mtime_max is not None and age_days > mtime_max:
                        continue
        if artist_mode and artists:
            lower_artists = [a.lower() for a in artists if a.strip()]
            if lower_artists:
                c_artist = c.get("artist", "").lower()
                if artist_mode == "include" and not any(a in c_artist for a in lower_artists):
                    continue
                if artist_mode == "exclude" and any(a in c_artist for a in lower_artists):
                    continue
        filtered_candidates.append(c)
    candidates = filtered_candidates
    step(f"Recommendation filters applied: {len(candidates)} remaining")

    # Apply transition boost to candidates (batched)
    if context_id and candidates:
        from engine import transitions
        boost_map = transitions.get_transition_boosts(context_id, [c["song_id"] for c in candidates])
        for c in candidates:
            boost = boost_map.get(c["song_id"], 0.0)
            if boost > 0:
                c["score"] = min(1.0, c.get("score", 0.5) + boost)

    if not candidates:
        step("No eligible recommendation candidates")

    # --- Taste Constellations: reweight candidates + taste hopping ---
    hop_pocket = None
    constellations = inputs.constellations
    if constellations is not None and inputs.constellations_enabled:
        try:
            constellations.build()
            if constellations.is_active:
                active_pocket = constellations.select_active_pocket(
                    current_song=current_song,
                    mood_state=inputs.mood_state,
                    hour=datetime.now().hour,
                )
                if active_pocket:
                    candidates = constellations.reweight_candidates(candidates, active_pocket)
                    step(f"Constellation reweighted: active_pocket={active_pocket.constellation_id} "
                         f"(mood={active_pocket.primary_mood}, weight={active_pocket.weight:.2f})")
                    # Taste hopping: occasionally inject candidates from neighboring pocket
                    hop_pocket = constellations.should_hop(active_pocket, exploration_level)
                    if hop_pocket:
                        hop_emb = hop_pocket.centroid_text
                        if hop_emb:
                            hop_candidates = retriever.search_by_vector(
                                taste_vector=hop_emb,
                                exploration_level=exploration_level,
                                n=10,
                                exclude_ids=exclude_ids + [c["song_id"] for c in candidates],
                                subfolder_filter=subfolder_filter,
                                mtime_min=mtime_min, mtime_max=mtime_max,
                                artist_mode=artist_mode, artists=artists,
                                **extra,
                            )
                            if allowed is not None:
                                hop_candidates = [c for c in hop_candidates if c["song_id"] in allowed]
                            for hc in hop_candidates:
                                hc["source"] = "constellation_hop"
                            candidates.extend(hop_candidates)
                            step(f"Taste hop: {len(hop_candidates)} candidates from pocket {hop_pocket.constellation_id}")
        except Exception as e:
            logger.debug("Constellation reweighting skipped: %s", e)

    # Defensive: coerce None artist/title to "" - SQLite returns NULL for
    # missing ID3 tags, and dict.get("artist","") returns None (not the
    # default) when the key exists with value None.
    for c in candidates:
        if c.get("artist") is None:
            c["artist"] = ""
        if c.get("title") is None:
            c["title"] = ""

    # Apply genre filter if set (batched)
    genre_filter = inputs.genre_filter
    if genre_filter and genre_filter.strip().lower() not in ("none", ""):
        step(f"Applying genre filter: {genre_filter}")
        song_map = database.get_songs_by_ids([c["song_id"] for c in candidates])
        filtered = []
        for c in candidates:
            song = song_map.get(c["song_id"])
            if song:
                genres = json.loads(song.get("genre_tags") or "[]")
                if genre_filter.lower() in [g.lower() for g in genres]:
                    filtered.append(c)
        if filtered:
            candidates = filtered
            step(f"Genre filter: {len(candidates)} remaining")

    # Apply artist filter if set
    artist_filter = inputs.artist_filter
    if artist_filter and artist_filter.strip().lower() not in ("none", ""):
        step(f"Applying artist filter: {artist_filter}")
        filtered = [c for c in candidates if artist_filter.lower() in c.get("artist", "").lower()]
        if filtered:
            candidates = filtered
            step(f"Artist filter: {len(candidates)} remaining")

    # Allocate slots (with optional guest persona slot bias)
    active_pocket_for_slots = constellations.active_pocket if constellations is not None and constellations.is_active else None
    step(f"Allocating slots (archetype={inputs.archetype}, exploration={exploration_level})")
    slots_result = slots.allocate_slots(
        candidates, current_song, vibe_lock=vibe_lock,
        exploration_level=exploration_level, archetype=inputs.archetype,
        slot_bias=inputs.slot_bias,
        active_pocket=active_pocket_for_slots,
        hop_pocket=hop_pocket,
    )
    step(f"Slots allocated: {slots_result}")

    # Hydrate song text in one batch; retrieval diagnostics stay attached.
    step("Enriching candidates with lyrics and tags")
    candidate_songs = db.get_songs_by_ids([c["song_id"] for c in candidates])
    for c in candidates:
        cs = candidate_songs.get(c["song_id"])
        if cs:
            for key in ("lyrics", "genre_tags", "emotional_tags", "instrument_tags"):
                c[key] = cs.get(key)

    step(f"Mode: {inputs.mode}, Play mode: {inputs.play_mode}")
    ranked_by = "llm"
    picks = None
    if inputs.play_mode in ("shuffle", "sequential") and allowed is None:
        step(f"Play mode '{inputs.play_mode}' - building queue directly from library")
        conn = db.get_connection()
        try:
            exclude_sql = ""
            if inputs.filler_current_song_id:
                exclude_sql = f"AND s.id != {int(inputs.filler_current_song_id)}"
            eff_expr = "(0.7 * s.energy + 0.3 * COALESCE(s.user_energy, s.energy))"
            order_by = "RANDOM()" if inputs.play_mode == "shuffle" else f"{eff_expr} DESC"
            reason = "Random pick" if inputs.play_mode == "shuffle" else "Sequential order"
            rows = conn.execute(f"""
                SELECT s.id, s.artist, s.title, {eff_expr} as energy, s.valence, s.bpm
                FROM songs s LEFT JOIN blacklist b ON s.id = b.song_id
                WHERE s.ingest_phase = 'clustered' AND b.song_id IS NULL {exclude_sql}
                ORDER BY {order_by} LIMIT 5
            """).fetchall()
        finally:
            conn.close()
        picks = [{"id": r["id"], "slot": "manual", "reason": reason} for r in rows]
        ranked_by = "deterministic"
        step(f"Built {len(picks)} picks from {inputs.play_mode} mode")
    elif inputs.mode == "normal" or not inputs.reranking_enabled:
        step("Deterministic song assignment")
        picks = reranker.deterministic_assignment(candidates, slots_result)
        ranked_by = "deterministic"
    else:
        step("Calling reranker.rerank()...")
        picks = _call_ranker(inputs, lambda: reranker.rerank(
            candidates=candidates,
            current_song=current_song,
            mood_state=inputs.mood_state,
            energy_trajectory=inputs.energy_direction,
            hour=datetime.now().hour if inputs.hour is None else inputs.hour,
            skip_streak=inputs.skip_streak,
            vibe_lock=vibe_lock,
            exploration_level=exploration_level,
        ))
        step(f"Reranker returned: {len(picks) if picks else None} picks")
        if picks is None:
            step("Reranker returned None, using fallback")
            picks = reranker.fallback_assignment(candidates, slots_result)
            ranked_by = "fallback"

    if picks is None:
        step("Picks still None, using fallback_assignment")
        picks = reranker.fallback_assignment(candidates, slots_result)
        ranked_by = "fallback"

    if allowed is not None:
        picks = [p for p in picks if p.get("id") in allowed]

    # Build queue entries from picks
    step(f"Building queue from {len(picks)} picks")
    vibe_active = vibe_lock and vibe_lock.get("active")
    diagnostics = {c["song_id"]: c.get("diagnostics", {}) for c in reversed(candidates)}
    entries = []
    for pick in picks:
        song = db.get_song(pick["id"])
        if song:
            entries.append({
                "song_id": pick["id"],
                "slot": pick.get("slot", "context"),
                "reason": pick.get("reason", ""),
                "artist": song.get("artist", ""),
                "title": song.get("title", ""),
                "energy": song_effective_energy(song),
                "valence": song.get("valence", 0.5),
                "bpm": song.get("bpm", 0),
                "vibe_affected": bool(vibe_active and pick.get("slot") in ("exploit", "explore")),
                "recommendation": diagnostics.get(pick["id"], {}),
            })

    # Ensure we have at least 4 items
    if len(entries) < 4:
        step(f"Queue only {len(entries)}, adding fillers")
        filler_exclude = [p["song_id"] for p in entries]
        if inputs.strict_exclusions:
            filler_exclude = list(dict.fromkeys(filler_exclude + exclude_ids))
        additional = retriever.build_candidate_list(
            text_taste_vector=taste_vector,
            audio_taste_vector=inputs.audio_taste_vector,
            current_song_id=inputs.filler_current_song_id,
            exploration_level=exploration_level,
            exclude_ids=filler_exclude,
            subfolder_filter=subfolder_filter,
            **extra,
        )
        for c in additional:
            if allowed is not None and c["song_id"] not in allowed:
                continue
            song = db.get_song(c["song_id"])
            if song and len(entries) < 4:
                entries.append({
                    "song_id": c["song_id"],
                    "slot": "context",
                    "reason": "Recommended for you.",
                    "artist": song.get("artist", ""),
                    "title": song.get("title", ""),
                    "energy": song_effective_energy(song),
                    "valence": song.get("valence", 0.5),
                    "bpm": song.get("bpm", 0),
                })

    excluded = set(exclude_ids)
    out, seen = [], set()
    for entry in entries:
        sid = entry["song_id"]
        if sid in seen or (allowed is not None and sid not in allowed):
            continue
        if inputs.strict_exclusions and sid in excluded:
            continue
        seen.add(sid)
        slot = entry["slot"] if entry["slot"] in SLOT_ORDER else "context"
        out.append({"song_id": sid, "slot": slot, "reason": _clean_reason(entry.get("reason"))})
    out.sort(key=lambda p: SLOT_ORDER.index(p["slot"]))
    return RecommendationResult(
        status="ok" if out else "insufficient",
        ranked_by=ranked_by, picks=out, entries=entries,
        candidates=candidates, hop_pocket=hop_pocket,
    )
