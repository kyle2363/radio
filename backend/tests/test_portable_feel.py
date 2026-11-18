"""Checkpoint 9A: shared feel scoring rules (mirrored by Android FeelRules.kt)."""
import json
from pathlib import Path

import pytest

from portable import feel

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "feel_golden.json").read_text())
NOW = 1_800_000_000.0
DAY = 86_400.0


def rating(song, value, age_days=0.0, weight=1.0):
    return {"song": song, "value": value, "at": NOW - age_days * DAY, "weight": weight}


def compare(winner, loser, age_days=0.0, tie=False):
    return {"winner": winner, "loser": loser, "tie": tie, "at": NOW - age_days * DAY}


def test_recency_halves_after_six_months():
    assert feel.recency_weight(0.0) == 1.0
    assert feel.recency_weight(feel.HALF_LIFE_DAYS) == pytest.approx(0.5)
    assert feel.recency_weight(-5.0) == 1.0  # future-dated events never weigh more


def test_bucket_ratings_order_songs():
    states = feel.score_feel([rating("hot", 0.875), rating("cold", 0.125)], [], NOW)
    assert states["hot"]["score"] > 0.5 > states["cold"]["score"]


def test_comparisons_build_a_transitive_ranking():
    states = feel.score_feel([], [compare("a", "b"), compare("b", "c")], NOW)
    assert states["a"]["score"] > states["b"]["score"] > states["c"]["score"]


def test_recent_evidence_outweighs_old():
    states = feel.score_feel([], [compare("a", "b", age_days=400), compare("b", "a", age_days=1)], NOW)
    assert states["b"]["score"] > states["a"]["score"]


def test_tie_keeps_songs_equal():
    states = feel.score_feel([], [compare("a", "b", tie=True)], NOW)
    assert states["a"]["score"] == pytest.approx(states["b"]["score"])


def test_more_evidence_more_confidence():
    one = feel.score_feel([rating("a", 1.0)], [], NOW)["a"]["confidence"]
    many = feel.score_feel([rating("a", 1.0)] * 5, [], NOW)["a"]["confidence"]
    assert 0 < one < many < 1


def test_member_needs_direct_evidence_and_positive_score():
    states = feel.score_feel([rating("in", 1.0), rating("out", 0.0)], [], NOW)
    assert states["in"]["member"] and not states["out"]["member"]


def test_neighbours_borrow_low_confidence_scores_only_for_unrated():
    states = feel.score_feel([rating("a", 1.0)], [], NOW)
    vectors = {"a": [1.0, 0.0], "near": [0.9, 0.1], "far": [-1.0, 0.0]}
    borrowed = feel.neighbour_scores(states, vectors)
    assert "a" not in borrowed and "far" not in borrowed
    assert borrowed["near"]["score"] > 0.5
    assert borrowed["near"]["confidence"] < states["a"]["confidence"]
    assert not borrowed["near"]["member"]


def test_uncertain_pairs_prefer_close_and_unconfident_songs():
    states = feel.score_feel([rating("a", 0.9), rating("b", 0.88), rating("c", 0.1)], [], NOW)
    pairs = feel.uncertain_pairs(states, played=set(), count=1)
    assert sorted(pairs[0]) == ["a", "b"]


def test_settled_rises_with_rounds():
    few = feel.settled_percent(feel.score_feel([], [compare("a", "b")], NOW))
    lots = feel.settled_percent(feel.score_feel([], [compare("a", "b")] * 8, NOW))
    assert 0 <= few < lots <= 100


def test_energy_change_threshold():
    assert feel.energy_changed(None, 0.4)
    assert not feel.energy_changed(0.40, 0.41)
    assert feel.energy_changed(0.40, 0.43)


def test_legacy_user_energy_becomes_one_weighted_rating():
    migrated = feel.legacy_energy_rating(0.7, 3, 1_700_000_000.0)
    assert migrated == {"value": 0.7, "weight": 3.0, "at": 1_700_000_000.0}
    assert feel.legacy_energy_rating(None, 0, None) is None


@pytest.mark.parametrize("case", GOLDEN["score_feel"], ids=lambda c: c["name"])
def test_score_feel_matches_golden(case):
    states = feel.score_feel(case["ratings"], case["comparisons"], case["now"])
    assert set(states) == set(case["expected"])
    for song, want in case["expected"].items():
        assert states[song]["score"] == pytest.approx(want["score"], abs=1e-9)
        assert states[song]["confidence"] == pytest.approx(want["confidence"], abs=1e-9)
        assert states[song]["member"] == want["member"]
