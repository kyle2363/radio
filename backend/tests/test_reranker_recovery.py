import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from engine import reranker


def candidates():
    return [{"song_id": i, "artist": "Fixture", "title": str(i), "energy": 0.5}
            for i in range(1, 7)]


def test_model_timeout_leaves_deterministic_fallback_available(monkeypatch):
    model = Mock()
    model.chat.side_effect = TimeoutError("optional service offline")
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    assert reranker.rerank(candidates()) is None
    assert model.chat.call_count == 3
    allocation = {"exploit": candidates()[:2], "explore": candidates()[2:3],
                  "wildcard": candidates()[3:4], "context": candidates()[4:5]}
    result = reranker.fallback_assignment(candidates(), allocation)
    assert [p["id"] for p in result] == [1, 2, 3, 4, 5]


def test_nullable_metadata_still_reaches_model(monkeypatch):
    model = Mock()
    model.chat.return_value = SimpleNamespace(content=json.dumps({"picks": [{"id": 1, "slot": "exploit"}]}))
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    rows = candidates()
    rows[0].update(artist=None, title=None, energy=None, lyrics=None)
    result = reranker.rerank(rows, current_song={"id": 7, "artist": None})
    assert result is not None
    assert model.chat.call_count == 1


@pytest.mark.parametrize("bad_id", [999, "1", True])
def test_model_cannot_introduce_tracks_outside_candidate_set(monkeypatch, bad_id):
    model = Mock()
    model.chat.return_value = SimpleNamespace(content=json.dumps({"picks": [
        {"id": bad_id, "slot": "exploit"}, {"id": 2, "slot": "exploit"},
    ]}))
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    result = reranker.rerank(candidates())
    assert result is not None
    assert len(result) == 5
    assert all(type(p["id"]) is int and p["id"] in range(1, 7) for p in result)


def test_overlong_model_output_cannot_expand_queue_batch(monkeypatch):
    model = Mock()
    model.chat.return_value = SimpleNamespace(content=json.dumps({"picks": [
        {"id": i, "slot": "exploit"} for i in range(1, 7)
    ]}))
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    result = reranker.rerank(candidates())
    assert len(result) == 5
