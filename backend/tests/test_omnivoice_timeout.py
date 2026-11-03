"""Timeout policy without loading a model, GPU or real voice assets."""
import sys
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from announcer import omnivoice_engine, voice_manager


@pytest.fixture
def engine(monkeypatch, tmp_path):
    eng = omnivoice_engine.OmniVoiceTTSEngine()
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"fixture")
    eng._voice_mp3s = {"fixture": str(reference)}
    eng._transcripts = {"fixture": "Reference words"}
    eng._model = SimpleNamespace(generate=lambda **kw: [[0.]])
    monkeypatch.setattr(eng, "is_ready", lambda: True)
    monkeypatch.setattr(eng, "_load_model", lambda: True)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=lambda path, *args: Path(path).write_bytes(b"wav")))
    return eng


def test_first_generation_and_manager_allow_sixty_seconds_then_thirty(engine, monkeypatch, tmp_path):
    outer, inner = [], []
    def run(calls):
        def invoke(fn, timeout):
            calls.append(timeout)
            return True, fn()
        return invoke
    monkeypatch.setattr(voice_manager, "run_with_timeout", run(outer))
    monkeypatch.setattr(omnivoice_engine, "run_with_timeout", run(inner))
    monkeypatch.setattr(voice_manager, "_append_silence", lambda path: None)
    manager = voice_manager.VoiceManager()
    monkeypatch.setattr(manager, "get_active_engine", lambda: ("omnivoice", engine))
    digest = hashlib.md5(b"... Cached").hexdigest()[:12]
    cached = tmp_path / f"omnivoice_fixture_0.90_{digest}.wav"
    cached.write_bytes(b"wav")
    assert manager.synthesize("Cached", "fixture", str(tmp_path)) == str(cached)
    assert outer == inner == []
    first = manager.synthesize("First", "fixture", str(tmp_path))
    assert first
    assert manager.synthesize("First", "fixture", str(tmp_path)) == first  # Cache does not generate.
    assert manager.synthesize("Second", "fixture", str(tmp_path))
    assert outer == [60, 30]
    assert inner == [60, 30]


def test_first_failed_generation_consumes_warmup_allowance(engine, monkeypatch, tmp_path, caplog):
    timeouts = []
    def fail(fn, timeout):
        timeouts.append(timeout)
        return False, TimeoutError()
    monkeypatch.setattr(omnivoice_engine, "run_with_timeout", fail)
    assert engine.synthesize("First", "fixture", str(tmp_path / "one.wav")) is None
    assert "timed out after 60s" in caplog.text
    assert engine.synthesize("Second", "fixture", str(tmp_path / "two.wav")) is None
    assert timeouts == [60, 30]


def test_piper_retains_thirty_second_timeout(monkeypatch, tmp_path):
    calls = []
    def synthesize(text, voice, path):
        Path(path).write_bytes(b"wav")
        return path
    def run(fn, timeout):
        calls.append(timeout)
        return True, fn()
    eng = SimpleNamespace(is_ready=lambda: True, synthesize=synthesize)
    manager = voice_manager.VoiceManager()
    monkeypatch.setattr(manager, "get_active_engine", lambda: ("piper", eng))
    monkeypatch.setattr(voice_manager, "run_with_timeout", run)
    monkeypatch.setattr(voice_manager, "_append_silence", lambda path: None)
    monkeypatch.setattr(voice_manager, "_prepend_silence", lambda path: None)
    assert manager.synthesize("First", "fixture", str(tmp_path))
    assert calls == [30]
