"""With no voice chosen, announcements use the station announcer voice, not whichever clip sorts first."""

from announcer import voice_manager


class _Engine:
    def __init__(self, voices):
        self.voices = voices

    def get_available_voices(self):
        return list(self.voices)


def _manager(monkeypatch, voices, engine_id="zonos3"):
    manager = voice_manager.VoiceManager()
    monkeypatch.setattr(manager, "get_active_engine", lambda: (engine_id, _Engine(voices)))
    return manager


def test_unset_voice_uses_the_announcer_voice_even_when_another_clip_sorts_first(monkeypatch):
    manager = _manager(monkeypatch, ["Blondie - Radio Host", "deep radio voice", "Donny - Real New Yorker"])
    assert manager._resolve_voice(None) == voice_manager.DEFAULT_ANNOUNCER_VOICE == "deep radio voice"


def test_a_chosen_voice_still_wins(monkeypatch):
    manager = _manager(monkeypatch, ["Blondie - Radio Host", "deep radio voice"])
    manager._active_voice = "Blondie - Radio Host"
    assert manager._resolve_voice(None) == "Blondie - Radio Host"
    assert manager._resolve_voice("Juniper") == "Juniper"


def test_an_engine_without_the_announcer_clip_falls_back_to_its_first_voice(monkeypatch):
    manager = _manager(monkeypatch, ["amy", "ryan"], engine_id="piper")
    assert manager._resolve_voice(None) == "amy"


def test_the_fallback_is_not_pinned_across_engine_switches(monkeypatch):
    manager = _manager(monkeypatch, ["amy", "ryan"], engine_id="piper")
    manager._resolve_voice(None)
    monkeypatch.setattr(manager, "get_active_engine", lambda: ("zonos3", _Engine(["Blondie - Radio Host", "deep radio voice"])))
    assert manager._resolve_voice(None) == "deep radio voice"
