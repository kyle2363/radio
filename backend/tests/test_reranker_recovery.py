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


def test_enriched_prompt_has_bounded_candidates_and_session_context(monkeypatch):
    model = Mock()
    model.chat.return_value = SimpleNamespace(content='{"picks": [{"id": 1}]}')
    monkeypatch.setattr(reranker, "llm_chat", lambda: model)
    rows = [{"song_id": i, "lyrics": " ".join(f"word{j}" for j in range(40)),
             "genre_tags": '["electronic", "downtempo", "extra"]',
             "emotional_tags": ["reflective"], "instrument_tags": '["synth"]',
             "diagnostics": {"audio": {"current_similarity": .84, "taste_similarity": .78},
                             "text": {"current_similarity": .65, "taste_similarity": None}}}
            for i in range(1, 41)]
    result = reranker.rerank(rows, current_song={"cluster_id": 0},
                             exploration_level=.7, hour=0)
    prompt = model.chat.call_args.kwargs["messages"][0]["content"]
    assert "Current song cluster: 0" in prompt
    assert "Exploration level: 0.70" in prompt
    assert "Hour of day: 0" in prompt
    assert "current_audio=0.84" in prompt and "taste_audio=0.78" in prompt
    assert "current_text=0.65" in prompt and "taste_text=" not in prompt
    assert "tags: electronic, downtempo, reflective, synth" in prompt
    assert "word29" in prompt and "word30" not in prompt
    assert "  30 |" in prompt and "  31 |" not in prompt
    assert all(p["id"] <= 30 for p in result)


def test_invalid_optional_metadata_does_not_break_candidates():
    text = reranker.build_candidates_list([{
        "song_id": 1, "genre_tags": "{bad json", "emotional_tags": None,
        "instrument_tags": '[1, null, "piano"]',
        "diagnostics": {"current_similarity": float("nan"), "taste_similarity": .6},
    }])
    assert "piano" in text
    assert "taste_text=0.60" in text
    assert "nan" not in text
