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


def test_model_load_happens_before_the_speech_timeout_starts(monkeypatch, tmp_path):
    """A slow cold load no longer eats the synthesis allowance (October 31 cold-start note)."""
    eng = omnivoice_engine.OmniVoiceTTSEngine()
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"fixture")
    eng._voice_mp3s, eng._transcripts = {"fixture": str(reference)}, {"fixture": "Reference words"}
    order = []
    def load():
        order.append("load")
        eng._model = SimpleNamespace(generate=lambda **kw: [[0.]])
        return True
    monkeypatch.setattr(eng, "is_ready", lambda: True)
    monkeypatch.setattr(eng, "_load_model", load)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=lambda path, *args: Path(path).write_bytes(b"wav")))
    def run(fn, timeout):
        order.append(("timed", timeout))
        return True, fn()
    monkeypatch.setattr(voice_manager, "run_with_timeout", run)
    monkeypatch.setattr(omnivoice_engine, "run_with_timeout", run)
    monkeypatch.setattr(voice_manager, "_append_silence", lambda path: None)
    manager = voice_manager.VoiceManager()
    monkeypatch.setattr(manager, "get_active_engine", lambda: ("omnivoice", eng))
    assert manager.synthesize("Hello", "fixture", str(tmp_path))
    # The load runs under its own (long) limit, then the 60 s first-generation allowance starts.
    assert order[:3] == [("timed", omnivoice_engine.OmniVoiceTTSEngine.LOAD_TIMEOUT), "load", ("timed", 60)]


def test_failed_model_load_falls_back_to_piper(monkeypatch, tmp_path):
    eng = omnivoice_engine.OmniVoiceTTSEngine()
    monkeypatch.setattr(eng, "is_ready", lambda: True)
    monkeypatch.setattr(eng, "_load_model", lambda: False)
    monkeypatch.setattr(omnivoice_engine, "run_with_timeout", lambda fn, timeout: (True, fn()))
    manager = voice_manager.VoiceManager()
    monkeypatch.setattr(manager, "get_active_engine", lambda: ("omnivoice", eng))
    fallback = []
    monkeypatch.setattr(manager, "_fallback_synthesize", lambda *args, **kw: fallback.append(args) or "piper.wav")
    assert manager.synthesize("Hello", "fixture", str(tmp_path)) == "piper.wav"
    assert fallback


def test_warm_up_spends_the_cold_allowance_before_any_listener_hears_speech(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(omnivoice_engine, "run_with_timeout", lambda fn, timeout: (True, fn()))
    assert engine.warm_up(str(tmp_path)) is True
    assert engine.synthesis_timeout == 30
    assert not list(tmp_path.glob("*.wav")), "the warm-up clip is not kept"
