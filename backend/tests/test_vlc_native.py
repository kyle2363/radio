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


def test_native_session_output_observes_each_silent_speech_and_song(monkeypatch):
    """Use real VLC callbacks, dummy output and only newly generated audio."""
    sandbox = Path(config.DB_PATH).parent
    for key in ("APPDATA", "LOCALAPPDATA"):
        directory = sandbox / key.lower()
        directory.mkdir()
        monkeypatch.setenv(key, str(directory))
    from engine import player
    from engine.vlc_session_output import VlcSessionOutput
    native_vlc = player.vlc
    class IsolatedVLC:
        def __getattr__(self, name):
            return getattr(native_vlc, name)
        def Instance(self, options):
            return native_vlc.Instance([*options, "--aout=dummy", "--no-video", "--ignore-config",
                                       f"--config={sandbox / 'vlc.conf'}"])
    monkeypatch.setattr(player, "vlc", IsolatedVLC())
    silence = sandbox / "generated-session-silence.wav"
    with wave.open(str(silence), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\0\0" * 11025)
    native = player.Player()
    observed, done = [], threading.Event()
    def observe(event):
        observed.append(event)
        if event["item_id"] == "song" and event["kind"] == "completed":
            done.set()
    output = VlcSessionOutput(native, lambda item: str(silence), observe)
    try:
        assert output.start({"id": "session", "instance_id": "attempt",
                             "history_session_id": database.create_session("neutral"),
                             "items": [{"item_id": "speech-1", "kind": "speech"},
                                       {"item_id": "speech-2", "kind": "speech"},
                                       {"item_id": "song", "kind": "song", "song_id": 1}]})
        assert done.wait(8), observed
        assert [(event["item_id"], event["kind"]) for event in observed] == [
            ("speech-1", "started"), ("speech-1", "completed"),
            ("speech-2", "started"), ("speech-2", "completed"),
            ("song", "started"), ("song", "completed")]
        assert len({event["event_id"] for event in observed}) == 6
        with database.get_connection_cm() as conn:
            assert conn.execute("SELECT count(*) FROM play_events").fetchone()[0] == 0
    finally:
        native.shutdown()


def test_native_session_pause_resume_seek_keeps_one_actual_start(monkeypatch):
    sandbox = Path(config.DB_PATH).parent
    for key in ("APPDATA", "LOCALAPPDATA"):
        directory = sandbox / key.lower()
        directory.mkdir()
        monkeypatch.setenv(key, str(directory))
    from engine import player
    from engine.vlc_session_output import VlcSessionOutput
    native_vlc = player.vlc
    class IsolatedVLC:
        def __getattr__(self, name):
            return getattr(native_vlc, name)
        def Instance(self, options):
            return native_vlc.Instance([*options, "--aout=dummy", "--no-video", "--ignore-config",
                                       f"--config={sandbox / 'vlc.conf'}"])
    monkeypatch.setattr(player, "vlc", IsolatedVLC())
    silence = sandbox / "generated-pause-silence.wav"
    with wave.open(str(silence), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\0\0" * 66150)
    native = player.Player()
    observed, started, resumed = [], threading.Event(), threading.Event()
    def observe(event):
        observed.append(event)
        if event["kind"] == "started":
            started.set()
        if event["kind"] == "resumed":
            resumed.set()
    output = VlcSessionOutput(native, lambda item: str(silence), observe)
    try:
        assert output.start({"id": "session", "instance_id": "attempt", "items": [
            {"item_id": "song", "kind": "song", "song_id": 1}]})
        assert started.wait(5)
        ack = output.quiesce("attempt", "handoff")
        assert ack["confirmed"] and ack["item_id"] == "song"
        native.seek(1000)
        native.resume()
        assert resumed.wait(5), observed
        assert [event["kind"] for event in observed].count("started") == 1
        assert [event["kind"] for event in observed].count("paused") == 1
        assert [event["kind"] for event in observed].count("resumed") == 1
    finally:
        native.shutdown()
