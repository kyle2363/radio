import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ingest import audio, llm_tags, metadata


def test_load_acestep_json_rejects_non_object_entry(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("[null]", encoding="utf-8")

    assert metadata.load_acestep_json(str(source)) is None


def test_metadata_path_and_lookup_disambiguate_same_basename(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata.config, "ACESTEP_JSON_DIR", str(tmp_path))
    first = str(tmp_path / "one" / "Artist - Song.mp3")
    second = str(tmp_path / "two" / "Artist - Song.mp3")
    Path(first).parent.mkdir()
    Path(second).parent.mkdir()

    first_path = Path(metadata.metadata_path(first))
    second_path = Path(metadata.metadata_path(second))
    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_text(json.dumps([{"caption": "first"}]), encoding="utf-8")
    second_path.write_text(json.dumps([{"caption": "second"}]), encoding="utf-8")

    indexed = metadata.build_json_index(str(tmp_path))
    loaded = {key: metadata.load_acestep_json(path) for key, path in indexed.items()}

    assert metadata.metadata_key(first) != metadata.metadata_key(second)
    assert metadata.get_metadata_for_mp3(first, loaded)["caption"] == "first"
    assert metadata.get_metadata_for_mp3(second, loaded)["caption"] == "second"


def test_legacy_fuzzy_metadata_match_rejects_equal_best_tie():
    items = {
        "artist song remix": {"caption": "remix"},
        "artist song live": {"caption": "live"},
    }

    assert metadata.get_metadata_for_mp3("/music/Artist - Song.mp3", items) is None


def test_legacy_fuzzy_metadata_match_rejects_weak_substring():
    items = {"song a remix": {"caption": "remix"}}

    assert metadata.get_metadata_for_mp3("/music/Song A.mp3", items) is None


def test_analyze_audio_raises_on_decode_failure(monkeypatch):
    def fail_load(*args, **kwargs):
        raise OSError("missing or unreadable")

    monkeypatch.setattr(audio.librosa, "load", fail_load)

    with pytest.raises(RuntimeError, match="missing or unreadable"):
        audio.analyze_audio("missing.mp3")


def test_analyze_audio_uses_actual_duration_for_onset_density(monkeypatch):
    sr = 22050
    y = np.ones(sr * 20, dtype=np.float32)
    monkeypatch.setattr(audio.librosa, "load", lambda *a, **k: (y, sr))
    monkeypatch.setattr(audio.librosa.feature, "rms", lambda **k: np.array([[0.1, 0.2]]))
    monkeypatch.setattr(audio.librosa.feature, "spectral_contrast", lambda **k: np.array([[1.0]]))
    monkeypatch.setattr(audio.librosa.feature, "spectral_rolloff", lambda **k: np.array([[1000.0]]))
    monkeypatch.setattr(audio.librosa.feature, "spectral_flatness", lambda **k: np.array([[0.1]]))
    monkeypatch.setattr(audio.librosa.beat, "beat_track", lambda **k: (120.0, np.array([])))
    monkeypatch.setattr(audio.librosa.onset, "onset_detect", lambda **k: np.arange(20))
    monkeypatch.setattr(audio.librosa.onset, "onset_strength", lambda **k: np.array([1.0]))
    monkeypatch.setattr(audio.librosa.feature, "spectral_centroid", lambda **k: np.array([[1000.0]]))
    monkeypatch.setattr(audio.pyln, "Meter", lambda *a, **k: SimpleNamespace(integrated_loudness=lambda _: -20.0))

    result = audio.analyze_audio("short.mp3")

    assert result["onset_density"] == 1.0


def test_normalize_current_songs_uses_v1_energy_when_v2_is_missing():
    songs = [
        {"id": 1, "raw_rms": 0.1, "crest_factor": 2, "spectral_contrast": 1,
         "spectral_rolloff": 0.2, "spectral_flatness": 0.1,
         "onset_density": 0.3, "spectral_centroid": 0.4, "lufs": -18,
         "rms_envelope_variance": 0.01},
        {"id": 2, "raw_rms": 0.3, "crest_factor": 4, "spectral_contrast": 2,
         "spectral_rolloff": 0.4, "spectral_flatness": 0.2,
         "onset_density": None, "spectral_centroid": None, "lufs": None,
         "rms_envelope_variance": None},
    ]

    combined = audio.normalize_current_songs(songs)
    v1_energy = {item["id"]: item["energy"] for item in audio.normalize_all_songs(songs)}

    assert len(combined) == 2
    combined_by_id = {item["id"]: item for item in combined}
    assert combined_by_id[1]["energy"] != v1_energy[1]
    assert combined_by_id[2]["energy"] == v1_energy[2]
    assert all("valence" in item for item in combined)


def test_generate_tags_retries_provider_errors_and_raises_on_exhaustion(monkeypatch):
    calls = []

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("provider unavailable")

    monkeypatch.setattr(llm_tags, "llm_chat", lambda: Client())

    with pytest.raises(ValueError, match="provider unavailable"):
        llm_tags.generate_tags("caption", retries=3)

    assert len(calls) == 3


def test_generate_tags_retries_transient_provider_error(monkeypatch):
    calls = []

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise TimeoutError("temporary outage")
            return SimpleNamespace(content=json.dumps({
                "emotional_tags": ["warm"],
                "genre_tags": ["indie-pop"],
                "instrument_tags": ["synth"]
            }))

    monkeypatch.setattr(llm_tags, "llm_chat", lambda: Client())

    result = llm_tags.generate_tags("caption", retries=2)

    assert len(calls) == 2
    assert result["genre_tags"] == ["indie-pop"]


@pytest.mark.parametrize("payload", ["[]", "null", '{"emotional_tags": [1]}'])
def test_generate_tags_rejects_invalid_tag_payloads(monkeypatch, payload):
    class Client:
        def chat(self, **kwargs):
            return SimpleNamespace(content=payload)

    monkeypatch.setattr(llm_tags, "llm_chat", lambda: Client())

    with pytest.raises(ValueError):
        llm_tags.generate_tags("caption", retries=1)
