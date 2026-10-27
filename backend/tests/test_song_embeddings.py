from unittest.mock import Mock

import numpy as np
import pytest

import config
from engine.song_embeddings import fetch_song_embeddings


def test_returns_both_embeddings_without_a_player(monkeypatch):
    monkeypatch.setattr(config, "MUQ_ENABLED", True)
    client = Mock()
    text, audio = Mock(), Mock()
    text.get.return_value = {"embeddings": np.array([[1.0, 0.0]])}
    audio.get.return_value = {"embeddings": np.array([[0.0, 1.0]])}
    client.get_collection.side_effect = [text, audio]
    result = fetch_song_embeddings(42, client_factory=lambda: client)
    assert [row.tolist() for row in result] == [[1.0, 0.0], [0.0, 1.0]]
    assert [call.args[0] for call in client.get_collection.call_args_list] == ["songs", config.MUQ_AUDIO_COLLECTION]
    audio.get.assert_called_once_with(ids=["42"], include=["embeddings"])


def test_missing_text_does_not_prevent_audio_lookup(monkeypatch):
    monkeypatch.setattr(config, "MUQ_ENABLED", True)
    client = Mock()
    collection = Mock()
    collection.get.return_value = {"embeddings": [[0.1, 0.2]]}
    client.get_collection.side_effect = [RuntimeError("unavailable"), collection]
    assert fetch_song_embeddings(2, client_factory=lambda: client) == (None, [0.1, 0.2])


@pytest.mark.parametrize("payload", [{}, {"embeddings": None}, {"embeddings": []}, {"embeddings": [None]}])
def test_missing_embedding_returns_none(payload):
    client = Mock()
    client.get_collection.return_value.get.return_value = payload
    assert fetch_song_embeddings(2, client_factory=lambda: client) == (None, None)
    client.get_collection.assert_called_once_with("songs")


def test_service_failure_is_recoverable(monkeypatch):
    monkeypatch.setattr(config, "MUQ_ENABLED", True)
    factory = Mock(side_effect=TimeoutError("offline"))
    assert fetch_song_embeddings(2, client_factory=factory) == (None, None)
