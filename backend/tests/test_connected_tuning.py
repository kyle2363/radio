"""Checkpoint 4.1: phone-only tuning of connected recommendations."""
import json
import random
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from db import database
from engine import energy as energy_module
from engine import recommendation_service as service
from engine import reranker, retriever
from portable.slots import allocate_ranked_slots, slot_counts
from tests.test_mobile_recommendations import (AVAILABLE, DEVICE, ask, availability, context, rejected,  # noqa: F401
                                               svc, update)
from tests.test_recommendation_service import DIM, TOTAL, inputs, library, unit  # noqa: F401

SLOTS = ("exploit", "explore", "wildcard", "context")


def counts_of(picks):
    return {slot: sum(p["slot"] == slot for p in picks) for slot in SLOTS}


# --- slot counts -----------------------------------------------------------------

@pytest.mark.parametrize("level", [0, .3, .41, .61, 1])
@pytest.mark.parametrize("archetype", [None, "explorer", "comfort_listener", "deep_listener", "mood_listener"])
def test_default_counts_are_unchanged(level, archetype):
    candidates = [dict(song_id=i, score=1 - i / 40, cluster_id=i % 3, energy=.5) for i in range(30)]
    plain = allocate_ranked_slots(candidates, None, exploration_level=level, archetype=archetype)
    explicit = allocate_ranked_slots(candidates, None, exploration_level=level, archetype=archetype,
                                     counts=slot_counts(exploration_level=level, archetype=archetype))
    assert {k: len(v) for k, v in plain.items()} == {k: len(v) for k, v in explicit.items()}
    assert sum(len(v) for v in plain.values()) == 5


@pytest.mark.parametrize("total,exploit,archetype,expected", [
    (8, 2, None, {"exploit": 2, "explore": 3, "wildcard": 0, "context": 3}),
    (8, None, None, {"exploit": 3, "explore": 3, "wildcard": 0, "context": 2}),
    (5, 2, None, {"exploit": 2, "explore": 2, "wildcard": 0, "context": 1}),
    (5, 4, None, {"exploit": 4, "explore": 1, "wildcard": 0, "context": 0}),
    (6, None, "comfort_listener", {"exploit": 4, "explore": 0, "wildcard": 0, "context": 2}),
    (7, 3, "explorer", {"exploit": 3, "explore": 2, "wildcard": 1, "context": 1}),
])
def test_counts_follow_total_and_exploit(total, exploit, archetype, expected):
    counts = slot_counts(total, exploit, exploration_level=.3, archetype=archetype)
    assert counts == expected
    candidates = [dict(song_id=i, score=1 - i / 40, cluster_id=i % 3, energy=.5) for i in range(30)]
    result = allocate_ranked_slots(candidates, dict(energy=.5, cluster_id=0), counts=counts)
    assert {k: len(v) for k, v in result.items()} == expected


def test_vibe_lock_ignores_counts():
    candidates = [dict(song_id=i, score=1 - i / 40, cluster_id=i % 3, energy=.5) for i in range(30)]
    result = allocate_ranked_slots(candidates, None, vibe_lock=True, counts=slot_counts(8, 2))
    assert {k: len(v) for k, v in result.items()} == {"exploit": 4, "explore": 1, "wildcard": 0, "context": 0}


@pytest.mark.parametrize("total,exploit", [(4, None), (9, None), (5, 5), (5, 0)])
def test_counts_reject_out_of_range(total, exploit):
    with pytest.raises(ValueError):
        slot_counts(total, exploit)


# --- reranker --------------------------------------------------------------------

def rows(n=20):
    return [{"song_id": i, "artist": "A", "title": str(i), "energy": .5} for i in range(1, n + 1)]


def model_returning(picks):
    model = Mock()
    model.chat.return_value = SimpleNamespace(content=json.dumps({"picks": picks}))
    return model


def test_reranker_honours_requested_counts_and_reasons(monkeypatch):
    model = model_returning([{"id": 1, "slot": "exploit", "reason": "close"}, {"id": 2, "slot": "explore"}])
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    wanted = {"exploit": 2, "explore": 3, "wildcard": 1, "context": 2}
    picks = reranker.rerank(rows(), slot_counts=wanted, reasons_for_all=True)
    assert len(picks) == 8 and len({p["id"] for p in picks}) == 8
    assert counts_of(picks) == wanted
    assert all(p.get("reason") for p in picks)
    prompt = model.chat.call_args.kwargs["messages"][0]["content"]
    assert "exactly 8 entries" in prompt
    assert "2 exploit, 3 explore, 1 wildcard, 2 context" in prompt
    assert "Include a specific, one-sentence reason of 12 words or fewer for every pick" in prompt


def test_reranker_caps_extra_model_picks_at_the_total(monkeypatch):
    model = model_returning([{"id": i, "slot": "exploit", "reason": "x"} for i in range(1, 15)])
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    wanted = {"exploit": 3, "explore": 2, "wildcard": 0, "context": 1}
    picks = reranker.rerank(rows(), slot_counts=wanted)
    assert len(picks) == 6 and counts_of(picks) == wanted


def test_reranker_default_prompt_and_reasons_unchanged(monkeypatch):
    model = model_returning([{"id": i, "slot": s, "reason": "r"} for i, s in
                             zip(range(1, 6), ("exploit", "exploit", "explore", "wildcard", "context"))])
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    picks = reranker.rerank(rows())
    prompt = model.chat.call_args.kwargs["messages"][0]["content"]
    assert 'containing exactly 5 entries' in prompt and "only for the first two picks" in prompt
    assert [bool(p.get("reason")) for p in picks] == [True, True, False, False, False]


# --- retriever: variety and phone recency ----------------------------------------

def ids(candidates):
    return [c["song_id"] for c in candidates]


def build(**kw):
    return retriever.build_candidate_list(text_taste_vector=unit([1] * DIM), audio_taste_vector=unit([1, 0] * (DIM // 2)),
                                          current_song_id=1, exploration_level=.3, **kw)


def test_variety_changes_retrieval_and_none_is_deterministic(library):
    plain = ids(build())
    assert ids(build()) == plain
    assert ids(build(jitter_strength=0)) == plain
    varied = ids(build(jitter_strength=1.0, jitter_rng=random.Random(3)))
    assert varied != plain
    assert ids(build(jitter_strength=1.0, jitter_rng=random.Random(3))) == varied


def test_variety_spreads_with_strength(library):
    plain = set(ids(build()))
    low = [len(set(ids(build(jitter_strength=.1, jitter_rng=random.Random(s)))) - plain) for s in range(6)]
    high = [len(set(ids(build(jitter_strength=1.0, jitter_rng=random.Random(s)))) - plain) for s in range(6)]
    assert sum(high) > sum(low)


def test_phone_plays_count_toward_recency(library):
    database.set_setting("recency_exclude_hours", "1")
    plain = build()
    first, second = plain[0]["song_id"], plain[1]["song_id"]
    shaped = build(extra_recent_hours={first: .5, second: 2.0})
    assert first not in ids(shaped)
    row = next(c for c in shaped if c["song_id"] == second)
    assert row["diagnostics"]["recency_penalty"] == .15


# --- energy direction --------------------------------------------------------------

@pytest.mark.parametrize("values,direction", [
    ([.2, .3, .5, .7, .8], "rising"), ([.8, .7, .5, .3, .2], "falling"), ([.5, .52, .5], "stable"), ([.9], "stable")])
def test_direction_rule_is_shared(values, direction):
    assert energy_module.direction_of(values) == direction


def test_energy_nudge_moves_picks_with_the_direction(library):
    def mean_energy(direction):
        result = service.recommend(inputs(energy_direction=direction, energy_nudge=True))
        energies = database.get_effective_energy_batch([p["song_id"] for p in result.picks])
        return sum(energies.values()) / len(energies)
    assert mean_energy("rising") > mean_energy("falling")


def test_service_returns_requested_pick_counts(library):
    result = service.recommend(inputs(slot_counts={"exploit": 2, "explore": 3, "wildcard": 1, "context": 2}))
    assert len(result.picks) == 8 and counts_of(result.picks) == {"exploit": 2, "explore": 3, "wildcard": 1, "context": 2}


# --- mobile contexts ----------------------------------------------------------------

def capture(svc):
    seen = []
    real = svc.recommend_fn
    svc.recommend_fn = lambda value: seen.append(value) or real(value)
    return seen


@pytest.mark.parametrize("tuning", [
    {"variety": 1.5}, {"variety": -0.1}, {"picks": 4}, {"picks": 9}, {"picks": 6.0}, {"similar": 1}, {"similar": 5},
    {"energy": "up"}, {"reranking": "maybe"}, {"pockets": "yes"}, {"surprise": 1},
])
def test_tuning_validation(svc, tuning):
    rejected(lambda: context(svc, tuning=tuning), 422)


def test_played_validation(svc):
    rejected(lambda: context(svc, played=[{"song_id": "10", "age_s": -1}]), 422)
    rejected(lambda: context(svc, played=[{"song_id": "10", "age_s": 1}] * 101), 422)
    rejected(lambda: context(svc, played=[{"song_id": "99999", "age_s": 5}]), 400)


def test_absent_tuning_is_checkpoint_4_and_reports_defaults(svc):
    seen = capture(svc)
    snapshot, created = availability(svc), context(svc)
    reply = ask(svc, created, snapshot)
    assert len(reply["picks"]) == 5
    assert seen[0].jitter_strength is None and seen[0].slot_counts is None and seen[0].energy_nudge is False
    assert seen[0].energy_direction == "stable" and seen[0].constellations_enabled is False
    assert reply["applied"] == {"variety": None, "picks": 5, "similar": counts_of(reply["picks"])["exploit"],
                                "energy": "stable", "reranking": "off", "pockets": "off"}


def test_pick_count_and_similar_reach_ranking_and_are_echoed(svc):
    snapshot = availability(svc)
    created = context(svc, tuning={"picks": 8, "similar": 2, "variety": .4})
    seen = capture(svc)
    reply = ask(svc, created, snapshot)
    assert seen[0].slot_counts == slot_counts(8, 2, exploration_level=.3, archetype=seen[0].archetype)
    assert seen[0].jitter_strength == .4 and seen[0].reasons_for_all is True
    assert len(reply["picks"]) == 8 and counts_of(reply["picks"])["exploit"] == 2
    assert reply["applied"]["picks"] == 8 and reply["applied"]["similar"] == 2 and reply["applied"]["variety"] == .4


def test_vibe_lock_overrides_the_mix(svc):
    snapshot = availability(svc)
    created = context(svc, vibe_lock_song_id="40", tuning={"picks": 8, "similar": 2})
    seen = capture(svc)
    reply = ask(svc, created, snapshot)
    assert seen[0].slot_counts is None
    assert counts_of(reply["picks"])["exploit"] == 4 and len(reply["picks"]) == 5
    assert reply["applied"]["picks"] == 5 and reply["applied"]["similar"] == 4


def test_reranking_override_beats_the_desktop_setting(svc, monkeypatch):
    snapshot = availability(svc)
    calls = []
    monkeypatch.setattr(reranker, "rerank", lambda **kw: calls.append(kw) or [
        {"id": int(i), "slot": "exploit", "reason": "llm"} for i in AVAILABLE[:5]])
    database.set_setting("llm_reranking_enabled", "true")
    off = context(svc, tuning={"reranking": "off"})
    reply = ask(svc, off, snapshot, request_id="a")
    assert not calls and reply["ranked_by"] == "deterministic" and reply["applied"]["reranking"] == "off"
    database.set_setting("llm_reranking_enabled", "false")
    on = context(svc, tuning={"reranking": "on"})
    reply = ask(svc, on, snapshot, request_id="b")
    assert calls and reply["ranked_by"] == "llm" and reply["applied"]["reranking"] == "on"
    assert calls[0]["reasons_for_all"] is True


def set_energy(values):
    with database.get_connection_cm() as conn:
        conn.executemany("UPDATE songs SET energy = ?, user_energy = NULL WHERE id = ?", [(e, sid) for sid, e in values.items()])
        conn.commit()


@pytest.mark.parametrize("energy,expected", [("rising", "rising"), ("falling", "falling"), ("steady", "stable")])
def test_explicit_energy(svc, energy, expected):
    snapshot = availability(svc)
    created = context(svc, tuning={"energy": energy})
    seen = capture(svc)
    reply = ask(svc, created, snapshot)
    assert seen[0].energy_direction == expected and seen[0].energy_nudge is True
    assert reply["applied"]["energy"] == expected


def test_auto_energy_follows_the_last_five_phone_songs(svc):
    set_energy({100: .1, 110: .2, 120: .3, 130: .7, 140: .8, 150: .9})
    recent = [{"song_id": str(s), "outcome": "completed"} for s in (100, 110, 120, 130, 140, 150)]
    snapshot = availability(svc)
    created = context(svc, recent=recent, tuning={"energy": "auto"})
    seen = capture(svc)
    reply = ask(svc, created, snapshot)
    assert seen[0].energy_direction == "rising" and reply["applied"]["energy"] == "rising"
    created = context(svc, recent=list(reversed(recent)), tuning={"energy": "auto"})
    ask(svc, created, snapshot, request_id="b")
    assert seen[1].energy_direction == "falling"


def test_phone_plays_reach_recency(svc):
    database.set_setting("recency_exclude_hours", "1")
    snapshot = availability(svc)
    created = context(svc, played=[{"song_id": "60", "age_s": 600}, {"song_id": "70", "age_s": 3 * 3600},
                                   {"song_id": "80", "age_s": 5 * 86400}])
    seen = capture(svc)
    reply = ask(svc, created, snapshot)
    assert 60 in seen[0].exclude_ids and "60" not in {p["song_id"] for p in reply["picks"]}
    assert seen[0].recent_hours == {70: 3.0}


class FakePockets:
    def __init__(self, active):
        self.is_active, self.built = active, 0
        self.active_pocket = None

    def build(self, memory_system=None):
        self.built += 1

    def select_active_pocket(self, **kw):
        return None


@pytest.mark.parametrize("active,report", [(True, "on"), (False, "inactive")])
def test_pockets(svc, active, report):
    svc.constellations = FakePockets(active)
    snapshot = availability(svc)
    created = context(svc, tuning={"pockets": True})
    seen = capture(svc)
    reply = ask(svc, created, snapshot)
    assert seen[0].constellations is svc.constellations and seen[0].constellations_enabled is True
    assert reply["applied"]["pockets"] == report


def test_tuning_change_bumps_the_epoch_and_fences_old_requests(svc):
    snapshot = availability(svc)
    created = context(svc, tuning={"picks": 6})
    assert update(svc, created["context_id"], tuning={"picks": 7})["epoch"] == 2
    rejected(lambda: ask(svc, created, snapshot), 409, {"reason": "epoch_stale"})
