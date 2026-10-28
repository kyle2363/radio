"""Shared EQ data stays importable without audio hardware dependencies."""

from types import SimpleNamespace

import pytest

from engine.audio_profiles import EQ_PRESETS, allowed_presets, bands_for
from tests.test_playback_sessions import domain


def test_profiles_match_native_names_and_return_defensive_bands():
    assert allowed_presets() == [
        "off", "bass_boost", "vocal_boost", "night_mode", "flat", "loudness", "clarity"
    ]
    bands = bands_for("bass_boost")
    assert bands == EQ_PRESETS["bass_boost"] and len(bands) == 10
    bands[0] = 99
    assert EQ_PRESETS["bass_boost"][0] == 0.0
    assert bands_for("off") is None
    assert bands_for("unknown") is None


def test_player_keeps_legacy_preset_alias(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "vlc", SimpleNamespace())
    import engine.player as player_module

    assert player_module.Player.EQ_PRESETS is EQ_PRESETS


def test_session_snapshot_and_browser_control_share_profile_bands(domain):
    service, sid, owner, _media, events, _native = domain
    initial = service.snapshot(sid, owner)
    assert initial["context"]["volume"] == 70
    assert initial["context"]["normalization"] is True
    assert initial["context"]["talk_volume"] == "100"
    assert initial["context"]["eq"] == "flat"
    assert initial["context"]["eq_bands"] == bands_for("flat")

    event_count = len(events)
    with pytest.raises(ValueError):
        service.set_eq(sid, owner, "made_up")
    assert len(events) == event_count
    assert service.context_for(sid, owner).preferences["eq"] == "flat"

    changed = service.set_eq(sid, owner, "bass_boost")
    assert changed["context"]["eq"] == "bass_boost"
    assert changed["context"]["eq_bands"] == bands_for("bass_boost")
    _event, control = events[-1]
    assert control["action"] == "eq"
    assert control["eq"] == "bass_boost"
    assert control["eq_bands"] == bands_for("bass_boost")
