"""Pure recommendation service and availability-restricted retrieval.

A fake Chroma collection implements the ``$in``/``$and`` metadata filters the
production client supports, and records every query so tests can prove that no
search ran without the allow-list.
"""
import math
import random
import threading
import time

import pytest

import config
from db import database
from engine import recommendation_service as service
from engine import reranker, retriever, transitions

DIM = 8


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def matches(where, meta):
    if where is None:
        return True
    for key, cond in where.items():
        if key == "$and":
            if not all(matches(c, meta) for c in cond):
                return False
        elif key == "$or":
            if not any(matches(c, meta) for c in cond):
                return False
        elif isinstance(cond, dict):
            value = meta.get(key)
            for op, arg in cond.items():
                if value is None:
                    return False
                if op == "$in" and value not in arg:
                    return False
                if op == "$gte" and not value >= arg:
                    return False
                if op == "$lte" and not value <= arg:
                    return False
        elif meta.get(key) != cond:
            return False
    return True


class FakeCollection:
    def __init__(self, name, vectors, energies):
        self.name, self.vectors, self.queries = name, vectors, []
        self.meta = {sid: {"song_id": sid, "energy": energies[sid], "valence": .5, "subfolder": ""}
                     for sid in vectors}

    def _rank(self, vector, where):
        def sim(sid):
            v = self.vectors[sid]
            return sum(a * b for a, b in zip(unit(vector), unit(v)))
        rows = [sid for sid in self.vectors if matches(where, self.meta[sid])]
        return sorted(rows, key=lambda sid: (-sim(sid), sid)), sim

    def query(self, query_embeddings, n_results, where=None, include=None, **kw):
        self.queries.append(where)
        rows, sim = self._rank(query_embeddings[0], where)
        rows = rows[:n_results]
        return {"ids": [[str(s) for s in rows]], "distances": [[1 - sim(s) for s in rows]],
                "metadatas": [[self.meta[s] for s in rows]],
                "embeddings": [[self.vectors[s] for s in rows]]}

    def get(self, ids=None, where=None, limit=None, include=None):
        if ids is not None:
            ids = [i for i in ids if int(i) in self.vectors]
            return {"ids": ids, "embeddings": [self.vectors[int(i)] for i in ids],
                    "metadatas": [self.meta[int(i)] for i in ids]}
        rows = [s for s in self.vectors if matches(where, self.meta[s])][:limit]
        return {"ids": [str(s) for s in rows], "metadatas": [self.meta[s] for s in rows],
                "embeddings": [self.vectors[s] for s in rows]}


TOTAL = 300


@pytest.fixture
def library(monkeypatch):
    """300 clustered songs, one fake text collection and one fake audio collection."""
    rng = random.Random(7)
    energies = {sid: .3 + .4 * rng.random() for sid in range(1, TOTAL + 1)}
    with database.get_connection_cm() as conn:
        conn.execute("DELETE FROM songs")
        conn.executemany(
            "INSERT INTO songs(id, filepath, file_hash, title, artist, energy, valence, cluster_id, ingest_phase) "
            "VALUES (?,?,?,?,?,?,?,?,'clustered')",
            [(sid, f"/synthetic/{sid}.mp3", f"hash-{sid}", f"Song {sid}", f"Artist {sid % 9}",
              energies[sid], .5, sid % 7) for sid in energies])
        conn.commit()
    collections = {
        "songs": FakeCollection("songs", {s: unit([rng.gauss(0, 1) for _ in range(DIM)]) for s in energies}, energies),
        "songs_audio": FakeCollection("songs_audio", {s: unit([rng.gauss(0, 1) for _ in range(DIM)]) for s in energies}, energies),
    }
    monkeypatch.setattr(retriever, "get_chroma_collection", lambda name="songs": collections[name])
    monkeypatch.setattr(config, "MUQ_ENABLED", True)
    monkeypatch.setattr(config, "MUQ_AUDIO_COLLECTION", "songs_audio")
    monkeypatch.setattr(retriever, "get_embedding_dimension", lambda name="songs": DIM)
    monkeypatch.setattr(transitions, "get_transition_candidates", lambda *a, **k: [])
    monkeypatch.setattr(transitions, "get_transition_boosts", lambda *a, **k: {})
    database.set_setting("recency_exclude_hours", "0")
    return collections


def inputs(**overrides):
    values = dict(db=database, anchor_song_id=1, taste_vector=unit([1] * DIM), audio_taste_vector=unit([1, 0] * (DIM // 2)),
                  exploration_level=.3, mood_energy_range=(0, 1), mood_valence_range=(0, 1),
                  reranking_enabled=False, archetype="mood_listener", strict_exclusions=True)
    values.update(overrides)
    return service.RecommendationInputs(**values)


def allowed_tenth():
    return frozenset(range(10, TOTAL + 1, 10))   # exactly 10% of the library


def test_ten_percent_availability_fills_every_slot(library):
    allowed = allowed_tenth()
    assert len(allowed) == TOTAL // 10
    result = service.recommend(inputs(allowed_ids=allowed, exclude_ids=[10]))
    assert result.status == "ok"
    assert len(result.picks) == 5
    assert {p["song_id"] for p in result.picks} <= allowed - {10}
    counts = {slot: sum(p["slot"] == slot for p in result.picks) for slot in service.SLOT_ORDER}
    assert counts == {"exploit": 3, "explore": 1, "wildcard": 0, "context": 1}
    assert [p["slot"] for p in result.picks] == sorted((p["slot"] for p in result.picks), key=service.SLOT_ORDER.index)


def test_vibe_lock_slots_fill_from_a_ten_percent_library(library):
    vibe = {"active": True, "embedding": unit([1] * DIM), "audio_embedding": unit([0, 1] * (DIM // 2))}
    result = service.recommend(inputs(allowed_ids=allowed_tenth(), vibe_lock=vibe))
    counts = {slot: sum(p["slot"] == slot for p in result.picks) for slot in service.SLOT_ORDER}
    assert counts == {"exploit": 4, "explore": 1, "wildcard": 0, "context": 0}
    assert {p["song_id"] for p in result.picks} <= allowed_tenth()


def test_every_search_is_restricted_and_no_unavailable_id_appears_at_any_stage(library, monkeypatch):
    allowed = allowed_tenth()
    vibe = {"active": True, "embedding": unit([1] * DIM), "audio_embedding": unit([0, 1] * (DIM // 2))}
    seen_candidates = []
    outside = next(i for i in range(1, TOTAL) if i not in allowed)

    def hallucinating(candidates, **kw):
        seen_candidates.extend(c["song_id"] for c in candidates)
        return [{"id": outside, "slot": "exploit", "reason": "not available"},
                {"id": 20, "slot": "exploit", "reason": "ok"}]
    monkeypatch.setattr(reranker, "rerank", hallucinating)
    monkeypatch.setattr(transitions, "get_transition_candidates",
                        lambda *a, **k: [{"song_id": outside, "score": 1.0}, {"song_id": 30, "score": .9}])
    result = service.recommend(inputs(allowed_ids=allowed, vibe_lock=vibe, reranking_enabled=True))
    assert set(seen_candidates) <= allowed
    assert outside not in {p["song_id"] for p in result.picks}
    assert {p["song_id"] for p in result.picks} <= allowed
    assert {e["song_id"] for e in result.entries} <= allowed
    for collection in library.values():
        assert collection.queries, f"{collection.name} never searched"
        assert all(w is not None and "song_id" in repr(w) for w in collection.queries), collection.queries


def test_fillers_top_up_inside_the_allow_list_and_respect_exclusions(library, monkeypatch):
    allowed = frozenset(range(100, 106))
    exclude = [100, 101]
    # A deterministic pool smaller than the slots forces the top-up search.
    monkeypatch.setattr(reranker, "deterministic_assignment",
                        lambda candidates, allocation: [{"id": 102, "slot": "exploit", "reason": "one"}])
    result = service.recommend(inputs(allowed_ids=allowed, exclude_ids=exclude, filler_current_song_id=1))
    ids = [p["song_id"] for p in result.picks]
    assert set(ids) <= allowed - set(exclude)
    assert len(ids) == len(set(ids)) == 4
    assert result.picks[0]["slot"] == "exploit"


def test_empty_allow_list_is_explicitly_insufficient_and_searches_nothing(library):
    result = service.recommend(inputs(allowed_ids=frozenset()))
    assert (result.status, result.picks, result.entries) == ("insufficient", [], [])
    assert all(not c.queries for c in library.values())


def test_all_allowed_songs_excluded_is_insufficient(library):
    result = service.recommend(inputs(allowed_ids=frozenset({5}), exclude_ids=[5]))
    assert result.status == "insufficient" and result.picks == []


def test_desktop_path_passes_no_allow_list(library, monkeypatch):
    calls = []
    real = retriever.build_candidate_list
    monkeypatch.setattr(retriever, "build_candidate_list", lambda **kw: calls.append(kw) or real(**kw))
    service.recommend(inputs(strict_exclusions=False))
    assert calls and all("allowed_ids" not in kw for kw in calls)


def test_widening_fallback_when_backend_rejects_the_filter(library):
    collection = library["songs"]
    original = collection.query
    def rejecting(query_embeddings, n_results, where=None, include=None, **kw):
        if where is not None and "song_id" in repr(where):
            raise ValueError("unsupported filter")
        return original(query_embeddings, n_results, where, include)
    collection.query = rejecting
    rows = retriever.search_by_vector(unit([1] * DIM), n=60, allowed_ids=allowed_tenth(), exploration_level=.3)
    assert rows and {r["song_id"] for r in rows} <= allowed_tenth()


def test_search_by_centroid_and_neutral_fallback_honor_allow_list(library):
    allowed = allowed_tenth()
    rows = retriever.search_by_centroid(.5, .5, n=60, allowed_ids=allowed)
    assert rows and {r["song_id"] for r in rows} <= allowed
    assert retriever.search_by_centroid(.5, .5, allowed_ids=frozenset()) == []
    # No taste vector at all -> labelled metadata fallback, still restricted.
    fallback = retriever.build_candidate_list(current_song_id=None, allowed_ids=allowed, exploration_level=.3)
    assert fallback and {r["song_id"] for r in fallback} <= allowed
    assert all(r["diagnostics"]["fallback"] for r in fallback)


def test_neutral_centroid_sql_restriction():
    rows = database.get_neutral_centroid_songs(50, allowed_ids={3, 4, 5})
    assert {r["id"] for r in rows} <= {3, 4, 5}
    assert database.get_neutral_centroid_songs(50, allowed_ids=set()) == []


def test_ranked_by_reports_llm_fallback_and_deterministic(library, monkeypatch):
    allowed = allowed_tenth()
    picks = [{"id": s, "slot": "exploit", "reason": "r"} for s in (10, 20, 30, 40, 50)]
    monkeypatch.setattr(reranker, "rerank", lambda **kw: picks)
    assert service.recommend(inputs(allowed_ids=allowed, reranking_enabled=True)).ranked_by == "llm"
    monkeypatch.setattr(reranker, "rerank", lambda **kw: None)
    result = service.recommend(inputs(allowed_ids=allowed, reranking_enabled=True))
    assert result.ranked_by == "fallback" and len(result.picks) == 5
    assert service.recommend(inputs(allowed_ids=allowed, reranking_enabled=False)).ranked_by == "deterministic"


def test_reasons_are_bounded_and_picks_unique(library, monkeypatch):
    monkeypatch.setattr(reranker, "rerank", lambda **kw: [
        {"id": 10, "slot": "exploit", "reason": "x" * 500}, {"id": 10, "slot": "explore", "reason": "dup"},
        {"id": 20, "slot": "wildcard", "reason": "  spaced   out "}])
    result = service.recommend(inputs(allowed_ids=allowed_tenth(), reranking_enabled=True))
    ids = [p["song_id"] for p in result.picks]
    assert len(ids) == len(set(ids))
    assert all(len(p["reason"]) <= 200 for p in result.picks)
    assert next(p for p in result.picks if p["song_id"] == 20)["reason"] == "spaced out"


def test_slow_llm_past_budget_degrades_to_fallback_promptly(library, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(reranker, "rerank", lambda **kw: release.wait(10) and [])
    gate = service.LlmPriorityGate()
    started = time.monotonic()
    try:
        result = service.recommend(inputs(allowed_ids=allowed_tenth(), reranking_enabled=True,
                                          llm_budget_seconds=.2, llm_gate=gate,
                                          gate_priority=service.LlmPriorityGate.MOBILE))
        assert time.monotonic() - started < 3
        assert result.ranked_by == "fallback" and len(result.picks) == 5
        assert gate.snapshot()["busy"]       # the orphaned call still holds the model
    finally:
        release.set()


def test_gate_admits_desktop_before_waiting_mobile():
    gate = service.LlmPriorityGate()
    order = []
    assert gate.acquire(gate.MOBILE)           # a running call, never preempted

    def waiter(name, priority):
        with gate.slot(priority):
            order.append(name)

    mobile = threading.Thread(target=waiter, args=("mobile", gate.MOBILE))
    mobile.start()
    while gate.snapshot()["waiting"] != [gate.MOBILE]:
        time.sleep(.005)
    desktop = threading.Thread(target=waiter, args=("desktop", gate.DESKTOP))
    desktop.start()
    while gate.snapshot()["waiting"] != [gate.DESKTOP, gate.MOBILE]:
        time.sleep(.005)
    gate.release()
    mobile.join(3)
    desktop.join(3)
    assert order == ["desktop", "mobile"]


def test_gate_waiter_times_out_without_leaking_its_place():
    gate = service.LlmPriorityGate()
    assert gate.acquire(gate.DESKTOP)
    assert gate.acquire(gate.MOBILE, timeout=.05) is False
    assert gate.snapshot() == {"busy": True, "waiting": []}
    gate.release()
    assert gate.acquire(gate.MOBILE, timeout=.05)
    gate.release()
