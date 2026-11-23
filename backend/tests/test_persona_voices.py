"""Personas resolve to their own cloned voice on every clip-cloning engine."""
import pytest

from announcer import voice_manager
from announcer.personas import PERSONAS


class _FakeVM:
    def __init__(self, engine_id):
        self.engine_id = engine_id

    def get_active_engine(self):
        return self.engine_id, None


@pytest.mark.parametrize("engine_id", ["omnivoice", "zonos3"])
def test_cloning_engines_use_the_persona_clip(monkeypatch, engine_id):
    monkeypatch.setattr(voice_manager, "get_voice_manager", lambda: _FakeVM(engine_id))
    for persona in PERSONAS.values():
        assert persona.voice_id == persona.voice_id_omnivoice


def test_piper_uses_the_piper_voice(monkeypatch):
    monkeypatch.setattr(voice_manager, "get_voice_manager", lambda: _FakeVM("piper"))
    for persona in PERSONAS.values():
        assert persona.voice_id == persona.voice_id_piper


def test_dj_and_caller_get_different_voices_on_zonos3(monkeypatch):
    monkeypatch.setattr(voice_manager, "get_voice_manager", lambda: _FakeVM("zonos3"))
    dj = PERSONAS["dj"]
    callers = [p for p in PERSONAS.values() if p.id != "dj"]
    assert any(p.voice_id != dj.voice_id for p in callers)


def test_every_persona_voice_has_a_reference_clip():
    from announcer.omnivoice_engine import _list_voice_mp3s
    clips = _list_voice_mp3s()
    missing = sorted({p.voice_id_omnivoice for p in PERSONAS.values()} - set(clips))
    assert not missing, missing
