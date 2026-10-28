import asyncio
import importlib
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import config


def test_main_uses_shared_chroma_client_for_health_and_library_routes():
    from db.chroma import get_chroma_client
    main = importlib.import_module('main')
    assert main.get_chroma_client is get_chroma_client


def test_disabled_tts_does_not_schedule_voice_initialization(monkeypatch):
    for key, value in {"RADIO_PASSWORD": "", "RADIO_AUTH_SECRET": "", "RADIO_PUBLIC_ORIGIN": "",
                       "RADIO_BIND_HOST": "127.0.0.1", "RADIO_ALLOWED_HOSTS": "localhost,127.0.0.1,::1"}.items():
        monkeypatch.setenv(key, value)
    main = importlib.import_module("main")
    player, queue = Mock(), Mock()
    monkeypatch.setattr(main.player_engine, "get_player", lambda: player)
    monkeypatch.setattr(main, "_get_queue_manager", lambda: queue)
    monkeypatch.setattr(main, "_queue_manager", queue)
    monkeypatch.setattr(main, "_get_mood_state_machine", lambda: SimpleNamespace(get_state=lambda: "neutral"))
    scheduled = []
    class StartupThread:
        def __init__(self, target, **kwargs):
            self.name = kwargs.get("name", target.__name__)
            scheduled.append(target.__name__)
        def start(self):
            pass
        def join(self, timeout=None):
            pass
        def is_alive(self):
            return False
    class ThreadingProxy:
        Thread = StartupThread
        def __getattr__(self, name):
            return getattr(threading, name)
    monkeypatch.setattr(main, "threading", ThreadingProxy())
    monkeypatch.setattr(config, "TTS_ENABLED", False)
    async def verify():
        async with main.lifespan(main.app):
            assert main._server_ready is True
            assert "_check_tts_voice" not in scheduled
        assert main._server_ready is False
    asyncio.run(verify())
