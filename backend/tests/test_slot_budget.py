"""Recommendation diversity is a five-pick budget, including at boundaries."""
from copy import deepcopy

import pytest

from engine.slots import allocate_slots
from portable.recommender import allocate_portable_slots


@pytest.mark.parametrize("level,counts", [
    (0, (3, 1, 0, 1)), (.25, (3, 1, 0, 1)), (.4, (3, 1, 0, 1)),
    (.4001, (2, 1, 1, 1)), (.6, (2, 1, 1, 1)),
    (.6001, (1, 2, 1, 1)), (1, (1, 2, 1, 1)),
])
@pytest.mark.parametrize("portable", [False, True])
@pytest.mark.parametrize("cluster", [None, 0, 1])
def test_diversity_budget(level, counts, portable, cluster):
    candidates = [dict(song_id=str(i), score=1-i/20, cluster_id=cluster, energy=.5)
                  for i in range(10)]
    allocator = allocate_portable_slots if portable else allocate_slots
    result = allocator(deepcopy(candidates), dict(energy=.5, cluster_id=cluster),
                       exploration_level=level)
    assert tuple(map(len, result.values())) == counts
    ids = [c['song_id'] for rows in result.values() for c in rows]
    assert len(set(ids)) == 5
    assert ids[:counts[0]] == [str(i) for i in range(counts[0])]


@pytest.mark.parametrize("archetype,counts", [
    ('comfort_listener', (4, 0, 0, 1)),
    ('deep_listener', (2, 1, 0, 2)),
    ('explorer', (1, 2, 1, 1)),
])
def test_archetypes_respect_budget_and_disabled_slots(archetype, counts):
    candidates = [dict(song_id=i, score=1-i/20, cluster_id=i, energy=.5)
                  for i in range(10)]
    result = allocate_slots(candidates, None, exploration_level=.25, archetype=archetype)
    assert tuple(map(len, result.values())) == counts


@pytest.mark.parametrize("size", [0, 1, 3, 5, 10])
def test_small_pools_and_vibe_lock(size):
    candidates = [dict(song_id=i, score=1-i/20, energy=.5) for i in range(size)]
    for locked in (False, True):
        result = allocate_slots(deepcopy(candidates), None, vibe_lock={'active': locked})
        ids = [c['song_id'] for rows in result.values() for c in rows]
        assert len(ids) == len(set(ids)) == min(5, size)
        if locked:
            assert not result['wildcard'] and not result['context']
