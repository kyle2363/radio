"""Connected-recommendation service: fences, contexts, admission, deadline, isolation."""
import ast
import hashlib
import threading
import time
from pathlib import Path

import pytest

from db import database
from engine import recommendation_service as service
from engine import reranker, taste
from mobile_recommendations import MobileRecommendations, Rejected, availability_digest
from portable_sync import PortableProfileStore
from tests.test_recommendation_service import DIM, TOTAL, library, unit  # noqa: F401  (fixture)
from tests.test_queue_lifecycle import manager, queue_module  # noqa: F401  (fixtures)

DEVICE, OTHER = "device-a", "device-b"
AVAILABLE = [str(i) for i in range(10, TOTAL + 1, 10)]


@pytest.fixture
def svc(library, monkeypatch):
    database.set_setting("llm_reranking_enabled", "false")
    memory = taste.MemorySystem()

    def batch(ids, collection="songs"):
        from tests.test_recommendation_service import FakeCollection  # noqa: F401
        source = library[collection]
        return {str(i): source.vectors[int(i)] for i in ids if int(i) in source.vectors}
    monkeypatch.setattr(memory, "_batch_get_chroma_embeddings", batch)
    monkeypatch.setattr(memory, "_get_chroma_embedding",
                        lambda sid, collection="songs": library[collection].vectors.get(int(sid)))
    clock = [1000.0]
    instance = MobileRecommendations(PortableProfileStore(), clock=lambda: clock[0], deadline_seconds=5.0,
                                     memory=memory)
    instance.test_clock = clock
    return instance


def availability(svc, ids=AVAILABLE, revision=12, device=DEVICE):
    return svc.put_availability(device, {
        "protocol_version": 1, "catalog_id": svc.store.catalog_id, "catalog_revision": revision,
        "song_ids": ids, "digest": availability_digest(ids)})


def context(svc, device=DEVICE, **overrides):
    body = {"protocol_version": 1, "mood": "neutral", "exploration": .3, "vibe_lock_song_id": None, "recent": []}
    body.update(overrides)
    return svc.create_context(device, body)


def update(svc, context_id, device=DEVICE, **overrides):
    body = {"protocol_version": 1, "mood": "neutral", "exploration": .3, "vibe_lock_song_id": None, "recent": []}
    body.update(overrides)
    return svc.update_context(device, context_id, body)


def ask(svc, created, snapshot, device=DEVICE, request_id="req-1", anchor="5", exclude=(), **overrides):
    body = {"protocol_version": 1, "request_id": request_id, "epoch": created["epoch"],
            "availability_digest": snapshot["digest"], "catalog_revision": snapshot["catalog_revision"],
            "anchor_song_id": anchor, "exclude_song_ids": list(exclude)}
    body.update(overrides)
    return svc.recommend(device, created["context_id"], body)


def rejected(call, status, detail=None):
    with pytest.raises(Rejected) as caught:
        call()
    assert caught.value.status == status
    if detail is not None:
        assert caught.value.detail == detail
    return caught.value


# --- availability ---------------------------------------------------------

def test_digest_sorts_by_utf8_bytes_and_joins_with_newlines():
    assert availability_digest(["9", "10", "100", "9"]) == hashlib.sha256(b"10\n100\n9").hexdigest()


def test_availability_accepts_known_ids_and_is_idempotent_by_digest(svc):
    first = availability(svc)
    assert first == {"digest": availability_digest(AVAILABLE), "count": len(AVAILABLE),
                     "catalog_id": svc.store.catalog_id, "catalog_revision": 12}
    snapshot = svc._snapshots[DEVICE]
    assert availability(svc) == first
    assert svc._snapshots[DEVICE] is snapshot                       # no-op
    assert availability(svc, revision=13)["catalog_revision"] == 13  # same set follows a revision bump
    assert svc._snapshots[DEVICE] is snapshot


def test_one_snapshot_per_device_and_replacement(svc):
    availability(svc)
    smaller = availability(svc, ids=["10", "20"])
    assert smaller["count"] == 2 and svc._snapshots[DEVICE].ids == frozenset({10, 20})
    availability(svc, device=OTHER)
    assert svc._snapshots[OTHER].ids != svc._snapshots[DEVICE].ids


@pytest.mark.parametrize("mutate,status,detail", [
    (lambda b: b.update(song_ids=["10", "20", "99999"], digest=availability_digest(["10", "20", "99999"])), 400, "Unknown song IDs"),
    (lambda b: b.update(digest="0" * 64), 400, "Availability digest does not match song_ids"),
    (lambda b: b.update(song_ids=["10", "10"], digest=availability_digest(["10"])), 400, "Duplicate song IDs"),
    (lambda b: b.update(catalog_id="someone-else"), 409, {"reason": "catalog_mismatch"}),
    (lambda b: b.update(protocol_version=2), 400, "Unsupported protocol"),
    (lambda b: b.update(extra=1), 422, "Invalid request"),
    (lambda b: b.update(song_ids=[]), 422, "Invalid request"),
    (lambda b: b.update(song_ids=["010"]), 422, "Invalid request"),
    (lambda b: b.update(song_ids=[10]), 422, "Invalid request"),
    (lambda b: b.update(catalog_revision=True), 422, "Invalid request"),
    (lambda b: b.update(digest="A" * 64), 422, "Invalid request"),
])
def test_availability_validation(svc, mutate, status, detail):
    body = {"protocol_version": 1, "catalog_id": svc.store.catalog_id, "catalog_revision": 1,
            "song_ids": ["10", "20"], "digest": availability_digest(["10", "20"])}
    mutate(body)
    rejected(lambda: svc.put_availability(DEVICE, body), status, detail)
    assert DEVICE not in svc._snapshots


def test_availability_size_cap(svc):
    ids = [str(i) for i in range(1, 20002)]
    rejected(lambda: svc.put_availability(DEVICE, {
        "protocol_version": 1, "catalog_id": svc.store.catalog_id, "catalog_revision": 1,
        "song_ids": ids, "digest": availability_digest(ids)}), 422)


# --- contexts -------------------------------------------------------------

def test_context_epochs_are_monotonic_and_validated(svc):
    created = context(svc)
    assert created["epoch"] == 1 and created["expires_in"] == 1800 and len(created["context_id"]) >= 16
    assert update(svc, created["context_id"], mood="chill")["epoch"] == 2
    assert update(svc, created["context_id"], mood="focus", recent=[{"song_id": "3", "outcome": "skipped"}])["epoch"] == 3
    for bad in ({"mood": "angry"}, {"exploration": 1.5}, {"exploration": True}, {"vibe_lock_song_id": "99999"},
                {"recent": [{"song_id": "3", "outcome": "liked"}]},
                {"recent": [{"song_id": "3", "outcome": "completed"}] * 21},
                {"recent": [{"song_id": "99999", "outcome": "completed"}]}):
        rejected(lambda bad=bad: update(svc, created["context_id"], **bad), 422 if "99999" not in str(bad) else 400)
    assert svc._contexts[created["context_id"]].epoch == 3


def test_third_context_evicts_the_least_recently_used(svc):
    first, second = context(svc), context(svc)
    update(svc, first["context_id"], mood="chill")                      # first is now more recent
    third = context(svc)
    assert first["context_id"] in svc._contexts and third["context_id"] in svc._contexts
    assert second["context_id"] not in svc._contexts
    rejected(lambda: update(svc, second["context_id"]), 404, "context_not_found")
    assert len([c for c in svc._contexts.values() if c.device_id == DEVICE]) == 2


def test_contexts_expire_after_idle_and_use_keeps_them_alive(svc):
    created = context(svc)
    svc.test_clock[0] += 1700
    update(svc, created["context_id"])                                  # touch
    svc.test_clock[0] += 1700
    update(svc, created["context_id"])
    svc.test_clock[0] += 1801
    rejected(lambda: update(svc, created["context_id"]), 404, "context_not_found")


def test_another_devices_context_is_not_found(svc):
    created = context(svc)
    snapshot = availability(svc, device=OTHER)
    rejected(lambda: update(svc, created["context_id"], device=OTHER), 404, "context_not_found")
    rejected(lambda: ask(svc, created, snapshot, device=OTHER), 404, "context_not_found")
    rejected(lambda: svc.recommend(DEVICE, "x" * 24, {}), 404, "context_not_found")


# --- recommendations ------------------------------------------------------

def test_recommendation_reply_echoes_every_fence_and_stays_inside_availability(svc):
    snapshot = availability(svc)
    created = context(svc)
    exclude = ["20", "30"]
    reply = ask(svc, created, snapshot, exclude=exclude)
    assert reply["status"] == "ok" and reply["ranked_by"] == "deterministic" and reply["vibe_locked"] is False
    assert {k: reply[k] for k in ("request_id", "context_id", "epoch", "availability_digest",
                                  "catalog_revision", "anchor_song_id")} == {
        "request_id": "req-1", "context_id": created["context_id"], "epoch": 1,
        "availability_digest": snapshot["digest"], "catalog_revision": 12, "anchor_song_id": "5"}
    assert len(reply["result_id"]) >= 16
    picks = reply["picks"]
    assert len(picks) == 5
    assert all(p["song_id"] in AVAILABLE and p["song_id"] not in exclude for p in picks)
    assert len({p["song_id"] for p in picks}) == 5
    assert [p["slot"] for p in picks] == sorted((p["slot"] for p in picks), key=service.SLOT_ORDER.index)
    assert all(set(p) == {"song_id", "slot", "reason"} and len(p["reason"]) <= 200 for p in picks)
    assert svc._work == {}                                                # admission released


def test_vibe_lock_reply(svc):
    snapshot = availability(svc)
    created = context(svc, vibe_lock_song_id="40")
    reply = ask(svc, created, snapshot)
    assert reply["vibe_locked"] is True
    assert [p["slot"] for p in reply["picks"]].count("exploit") == 4


@pytest.mark.parametrize("override,reason", [
    ({"epoch": 2}, "epoch_stale"),
    ({"availability_digest": "0" * 64}, "availability_stale"),
    ({"catalog_revision": 11}, "availability_stale"),
])
def test_stale_fences_return_the_specific_reason(svc, override, reason):
    snapshot = availability(svc)
    created = context(svc)
    rejected(lambda: ask(svc, created, snapshot, **override), 409, {"reason": reason})


def test_no_availability_and_context_update_fence(svc):
    created = context(svc)
    fake = {"digest": "0" * 64, "catalog_revision": 1}
    rejected(lambda: ask(svc, created, fake), 409, {"reason": "no_availability"})
    snapshot = availability(svc)
    update(svc, created["context_id"], mood="chill")
    rejected(lambda: ask(svc, created, snapshot), 409, {"reason": "epoch_stale"})
    availability(svc, ids=["10", "20"])
    fresh = {"context_id": created["context_id"], "epoch": 2}
    rejected(lambda: ask(svc, fresh, snapshot), 409, {"reason": "availability_stale"})


def test_unknown_anchor_and_bad_body(svc):
    snapshot = availability(svc)
    created = context(svc)
    rejected(lambda: ask(svc, created, snapshot, anchor="99999"), 400, "Unknown anchor song")
    rejected(lambda: ask(svc, created, snapshot, exclude=[str(i) for i in range(1, 202)]), 422)
    rejected(lambda: ask(svc, created, snapshot, surprise=1), 422)


def test_empty_result_is_insufficient_not_an_error(svc):
    snapshot = availability(svc, ids=["10"])
    created = context(svc)
    reply = ask(svc, created, snapshot, exclude=["10"])
    assert reply["status"] == "insufficient" and reply["picks"] == []


def test_recent_session_shapes_this_request_only(svc):
    snapshot = availability(svc)
    seen = []
    real = svc.recommend_fn
    svc.recommend_fn = lambda inputs: seen.append(inputs) or real(inputs)
    with database.get_connection_cm() as conn:
        conn.executemany("INSERT INTO play_events(song_id, signal) VALUES (?, 'completed')", [(i,) for i in (1, 2, 3)])
        conn.commit()
        before = conn.execute("SELECT COUNT(*) FROM play_events").fetchone()[0]
    plain = context(svc)
    ask(svc, plain, snapshot, request_id="a")
    recent = [{"song_id": "100", "outcome": "completed"}, {"song_id": "110", "outcome": "skipped"},
              {"song_id": "120", "outcome": "skipped"}, {"song_id": "130", "outcome": "skipped"}]
    shaped = context(svc, recent=recent)
    reply = ask(svc, shaped, snapshot, request_id="b")
    assert seen[0].taste_vector is not None, "the shared profile supplies the long-term/daily taste"
    assert seen[1].taste_vector != seen[0].taste_vector, "the session list shifts immediate/session taste"
    assert seen[0].skip_streak == 0 and seen[1].skip_streak == 3
    assert {100, 110, 120, 130} <= set(seen[1].exclude_ids) | {5}
    assert not {"100", "110", "120", "130"} & {p["song_id"] for p in reply["picks"]}
    with database.get_connection_cm() as conn:
        assert conn.execute("SELECT COUNT(*) FROM play_events").fetchone()[0] == before


def test_reranking_follows_the_shared_setting(svc, monkeypatch):
    snapshot, created = availability(svc), context(svc)
    calls = []
    monkeypatch.setattr(reranker, "rerank", lambda **kw: calls.append(kw) or [
        {"id": int(i), "slot": "exploit", "reason": "llm"} for i in AVAILABLE[:5]])
    assert ask(svc, created, snapshot, request_id="a")["ranked_by"] == "deterministic" and not calls
    database.set_setting("llm_reranking_enabled", "true")
    reply = ask(svc, created, snapshot, request_id="b")
    assert calls and reply["ranked_by"] == "llm"


def test_slow_llm_returns_deterministic_fallback_at_the_server_deadline(svc, monkeypatch):
    database.set_setting("llm_reranking_enabled", "true")
    svc.deadline_seconds = .3
    svc.clock = time.monotonic
    release = threading.Event()
    monkeypatch.setattr(reranker, "rerank", lambda **kw: release.wait(10) and [])
    snapshot, created = availability(svc), context(svc)
    started = time.monotonic()
    try:
        reply = ask(svc, created, snapshot)
        assert time.monotonic() - started < 3
        assert reply["ranked_by"] == "fallback" and len(reply["picks"]) == 5
    finally:
        release.set()


def test_busy_llm_gate_degrades_to_fallback_and_desktop_waiters_go_first(svc, monkeypatch):
    database.set_setting("llm_reranking_enabled", "true")
    svc.deadline_seconds = .3
    svc.clock = time.monotonic
    gate = svc.gate = service.LlmPriorityGate()
    monkeypatch.setattr(reranker, "rerank", lambda **kw: pytest.fail("Gate was held; the LLM must not run"))
    assert gate.acquire(gate.DESKTOP)               # a desktop rebuild is using the model
    try:
        snapshot, created = availability(svc), context(svc)
        reply = ask(svc, created, snapshot)
        assert reply["ranked_by"] == "fallback" and len(reply["picks"]) == 5
        assert gate.snapshot() == {"busy": True, "waiting": []}
    finally:
        gate.release()


# --- admission ------------------------------------------------------------

def test_pending_request_is_superseded_and_a_run_overtaken_by_a_newer_arrival_is_too(svc):
    snapshot, created = availability(svc), context(svc)
    real = svc.recommend_fn
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocking(inputs):
        calls.append(inputs.anchor_song_id)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return real(inputs)
    svc.recommend_fn = blocking
    outcomes = {}

    def run(name, anchor):
        try:
            outcomes[name] = ask(svc, created, snapshot, request_id=name, anchor=anchor)
        except Rejected as exc:
            outcomes[name] = exc

    def start(name, anchor):
        thread = threading.Thread(target=run, args=(name, anchor))
        thread.start()
        return thread

    first = start("one", "5")
    assert entered.wait(5)
    second = start("two", "6")
    while svc._work[DEVICE].pending is None:
        time.sleep(.005)
    third = start("three", "7")
    second.join(5)                                                        # replaced while pending
    assert outcomes["two"].status == 409 and outcomes["two"].detail == {"reason": "superseded"}
    release.set()
    first.join(10)
    third.join(10)
    assert outcomes["one"].status == 409 and outcomes["one"].detail == {"reason": "superseded"}
    assert outcomes["three"]["anchor_song_id"] == "7" and outcomes["three"]["status"] == "ok"
    assert calls == [5, 7], "the superseded pending request never reached the ranker"
    assert svc._work == {}


def test_other_devices_are_admitted_independently(svc):
    snapshot_a, snapshot_b = availability(svc), availability(svc, device=OTHER)
    a, b = context(svc), context(svc, device=OTHER)
    release, entered = threading.Event(), threading.Event()
    real = svc.recommend_fn

    def blocking(inputs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return real(inputs)
    svc.recommend_fn = blocking
    result = {}
    thread = threading.Thread(target=lambda: result.update(a=ask(svc, a, snapshot_a)))
    thread.start()
    assert entered.wait(5)
    reply = ask(svc, b, snapshot_b, device=OTHER)                        # not blocked behind device A
    assert reply["status"] == "ok"
    release.set()
    thread.join(10)
    assert result["a"]["status"] == "ok"


def test_changes_during_a_run_fence_the_reply(svc):
    snapshot, created = availability(svc), context(svc)
    real = svc.recommend_fn

    def bump(inputs):
        update(svc, created["context_id"], mood="chill")
        return real(inputs)
    svc.recommend_fn = bump
    rejected(lambda: ask(svc, created, snapshot), 409, {"reason": "epoch_stale"})
    assert svc._work == {}


def test_ranker_failure_is_reported_and_releases_admission(svc):
    snapshot, created = availability(svc), context(svc)
    svc.recommend_fn = lambda inputs: (_ for _ in ()).throw(RuntimeError("chroma down"))
    rejected(lambda: ask(svc, created, snapshot), 503, "Recommendation unavailable")
    assert svc._work == {}


def test_forget_device_drops_state(svc):
    availability(svc)
    created = context(svc)
    svc.forget_device(DEVICE)
    assert DEVICE not in svc._snapshots
    rejected(lambda: update(svc, created["context_id"]), 404, "context_not_found")


# --- isolation ------------------------------------------------------------

def test_module_never_reaches_desktop_playback_or_broadcast():
    forbidden = {"engine.queue", "queue", "playback_sessions", "media_service", "engine.playback_coordinator",
                 "playback_coordinator", "engine.player", "main", "playback_api"}
    tree = ast.parse(Path(__import__("mobile_recommendations").__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}
    assert not imported & forbidden, imported & forbidden
    assert "_broadcast" not in Path(__import__("mobile_recommendations").__file__).read_text(encoding="utf-8")


def snapshot_history():
    with database.get_connection_cm() as conn:
        return {
            "play_events": conn.execute("SELECT COUNT(*) FROM play_events").fetchone()[0],
            "queue_history": conn.execute("SELECT COUNT(*) FROM queue_history").fetchone()[0],
            "sessions": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "transitions": conn.execute("SELECT COUNT(*) FROM transitions").fetchone()[0],
            "blacklist": conn.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0],
            "songs": conn.execute("SELECT SUM(play_count + skip_count + replay_count), MAX(last_played) FROM songs").fetchone()[:],
        }


def test_requests_leave_history_and_the_desktop_queue_untouched(svc, manager):
    events = []
    manager._preview_stop.set()          # the queue's own watchdog would rebuild a non-empty queue
    manager._broadcast = lambda *a, **kw: events.append((a, kw))
    manager._queue = [{"song_id": 20}]
    manager._manual_queue = [{"song_id": 30}]
    manager._preview_queue = [{"song_id": 40}]
    manager._recently_recommended_ids = {50}
    manager._current_song_id, manager._current_session_id = 9, 7
    generation = manager._rebuild_gen
    with database.get_connection_cm() as conn:
        conn.execute("INSERT INTO play_events(song_id, signal) VALUES (1, 'completed')")
        conn.commit()
    before = snapshot_history()
    database.set_setting("llm_reranking_enabled", "true")

    snapshot = availability(svc)
    created = context(svc, recent=[{"song_id": "100", "outcome": "completed"}, {"song_id": "110", "outcome": "skipped"}])
    update(svc, created["context_id"], mood="energetic", vibe_lock_song_id="40")
    created["epoch"] = 2
    assert ask(svc, created, snapshot)["status"] in ("ok", "insufficient")
    ask(svc, created, snapshot, request_id="again")

    assert snapshot_history() == before
    assert (manager._queue, manager._manual_queue, manager._preview_queue) == (
        [{"song_id": 20}], [{"song_id": 30}], [{"song_id": 40}])
    assert manager._recently_recommended_ids == {50}
    assert (manager._rebuild_gen, manager._current_song_id, manager._current_session_id) == (generation, 9, 7)
    assert events == []
