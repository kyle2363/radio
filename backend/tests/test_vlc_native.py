"""Opt-in libVLC integration using generated silence and disposable history."""
import os
from pathlib import Path
import threading
import wave

import pytest

import config
from db import database

pytestmark = pytest.mark.skipif(os.environ.get("RADIO_NATIVE_SMOKE") != "1",
                                reason="Opt-in native VLC smoke; synthetic silent audio only")


@pytest.mark.parametrize("talk", [False, True])
def test_native_silent_playback_completion_and_shutdown(monkeypatch, talk):
    # Keep native-library configuration writes inside the disposable runtime.
    sandbox = Path(config.DB_PATH).parent
    for key in ("APPDATA", "LOCALAPPDATA"):
        directory = sandbox / key.lower()
        directory.mkdir()
        monkeypatch.setenv(key, str(directory))
    from engine import player
    native_vlc = player.vlc
    class IsolatedVLC:
        def __getattr__(self, name):
            return getattr(native_vlc, name)
        def Instance(self, options):
            return native_vlc.Instance([*options, "--aout=dummy", "--no-video", "--ignore-config",
                                        f"--config={sandbox / 'vlc.conf'}"])
    monkeypatch.setattr(player, "vlc", IsolatedVLC())
    silence = sandbox / "silence.wav"
    with wave.open(str(silence), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\0\0" * 11025)
    native = player.Player()
    session = database.create_session("neutral")
    ended = threading.Event()
    observations = []
    native.on_song_ended(lambda song: (observations.append(song), ended.set()))
    try:
        if talk:
            assert native.play_chain([{"filepath": str(silence), "is_tts": True},
                                      {"filepath": str(silence), "is_tts": True}], session)
        else:
            assert native.play_song(1, str(silence), session)
        assert ended.wait(8), "Native completion did not arrive"
        assert observations == [None if talk else 1]
        with database.get_connection_cm() as conn:
            count = conn.execute("SELECT count(*) FROM play_events WHERE signal='completed'").fetchone()[0]
        assert count == (0 if talk else 1)
    finally:
        native.shutdown()
    assert not native._event_worker.is_alive()
