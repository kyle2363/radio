"""Run the real Radio API against a disposable synthetic library.

All generated data stays below .test-runtime/qa-<random>. Recommendation
generation is deterministic and mocked; playback plans, routes, persistence,
and browser audio streaming use the real application code.
"""

import argparse
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import sys
import wave


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QA_PASSWORD = "isolated-test"


def is_within(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_qa_root(project_root, qa_root):
    project_root, qa_root = Path(project_root).resolve(), Path(qa_root).resolve()
    expected_parent = (project_root / ".test-runtime").resolve()
    if qa_root.parent != expected_parent or not is_within(qa_root, expected_parent):
        raise ValueError("QA workspace must be a direct child of project .test-runtime")
    for protected in (project_root / "data", project_root / "audio_files", project_root / "docs" / "understand"):
        if is_within(qa_root, protected):
            raise ValueError("QA workspace overlaps protected project data")
    return qa_root


def validate_venv(executable, project_root):
    executable = Path(executable).resolve()
    venv = (Path(project_root) / "backend" / "venv").resolve()
    if not is_within(executable, venv):
        raise RuntimeError(f"Use the project backend virtualenv Python: {venv}")
    return executable


def workspace_paths(qa_root):
    qa_root = Path(qa_root).resolve()
    paths = {
        "data": qa_root / "data",
        "music": qa_root / "music",
        "metadata": qa_root / "metadata",
        "temp": qa_root / "tmp",
        "appdata": qa_root / "appdata",
        "localappdata": qa_root / "localappdata",
        "home": qa_root / "home",
        "vlc": qa_root / "vlc",
        "cache": qa_root / "cache",
    }
    for path in paths.values():
        if not is_within(path, qa_root):
            raise ValueError("Generated QA path escapes its workspace")
    return paths


def configure_environment(qa_root, port, environ=None):
    environ = os.environ if environ is None else environ
    paths = workspace_paths(qa_root)
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    values = {
        "RADIO_DATA_DIR": str(paths["data"]),
        "RADIO_MUSIC_DIR": str(paths["music"]),
        "RADIO_METADATA_DIR": str(paths["metadata"]),
        "TEMP": str(paths["temp"]), "TMP": str(paths["temp"]), "TMPDIR": str(paths["temp"]),
        "APPDATA": str(paths["appdata"]), "LOCALAPPDATA": str(paths["localappdata"]),
        "USERPROFILE": str(paths["home"]),
        "XDG_CACHE_HOME": str(paths["cache"]), "HF_HOME": str(paths["cache"] / "huggingface"),
        "TORCH_HOME": str(paths["cache"] / "torch"), "NUMBA_CACHE_DIR": str(paths["cache"] / "numba"),
        "MPLCONFIGDIR": str(paths["cache"] / "matplotlib"),
        "VLC_CONFIG_PATH": str(paths["vlc"] / "vlcrc"),
        "RADIO_BIND_HOST": "127.0.0.1", "RADIO_ALLOWED_HOSTS": "localhost,127.0.0.1,::1",
        "RADIO_PUBLIC_ORIGIN": "", "RADIO_TLS_CERT": "", "RADIO_TLS_KEY": "",
        "RADIO_TRUSTED_ORIGINS": f"http://127.0.0.1:{port},http://localhost:{port}",
        "RADIO_PASSWORD": QA_PASSWORD, "RADIO_AUTH_SECRET": secret,
        "RADIO_TTS_ENABLED": "false", "RADIO_PROC_MGR_ENABLED": "false",
        "OPENAI_API_KEY": "isolated-test", "CHAT_PROVIDER": "openai", "EMBED_PROVIDER": "openai",
        "OPENAI_BASE_URL_CHAT": f"http://127.0.0.1:{port}/v1",
        "OPENAI_BASE_URL_EMBED": f"http://127.0.0.1:{port}/v1",
        "OLLAMA_BASE_URL": f"http://127.0.0.1:{port}",
        "CHROMA_NO_ANALYTICS": "1", "CHROMA_TELEMETRY_OFF": "1",
        "ANONYMIZED_TELEMETRY": "False", "PYTHONDONTWRITEBYTECODE": "1",
    }
    environ.update(values)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        environ.pop(key, None)
    config_file = paths["vlc"] / "vlcrc"
    config_file.write_text("aout=dummy\n", encoding="utf-8")
    return paths, secret


def install_network_guard(port):
    """Deny outbound socket connections except to this local QA server."""
    def audit(event, args):
        if event != "socket.connect":
            return
        socketpair_code = getattr(socket.socketpair, "__code__", None)
        if socketpair_code is not None and sys._getframe(1).f_code is socketpair_code:
            return
        address = args[1] if len(args) > 1 else None
        if not is_allowed_outbound_address(address, port):
            raise PermissionError(f"Isolated QA blocks outbound connection to {address!r}")

    sys.addaudithook(audit)


def is_allowed_write(value, qa_root):
    if isinstance(value, int) or value is None:
        return True
    path = Path(os.fsdecode(value)).resolve()
    return path == Path(os.devnull).resolve() or is_within(path, qa_root)


def install_storage_guard(qa_root):
    def check(value):
        if not is_allowed_write(value, qa_root):
            raise PermissionError('Isolated app cannot write outside its synthetic workspace')

    def audit(event, args):
        if event == 'open':
            value, mode, flags = args
            if (mode and any(c in mode for c in 'wax+')) or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                check(value)
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime', 'os.truncate'):
            check(args[0])
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            check(args[0])
            check(args[1])
        elif event == 'sqlite3.connect' and str(args[0]) != ':memory:':
            check(args[0])
        elif event in ('subprocess.Popen', 'os.system'):
            raise PermissionError('External processes are disabled in isolated app checks')
    sys.addaudithook(audit)


def is_allowed_outbound_address(address, port):
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    host, target_port = address[0], address[1]
    if target_port != port:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return host.lower() == "localhost"


def create_synthetic_wavs(music_dir):
    music_dir = Path(music_dir)
    paths = []
    sample_rate, duration = 8000, 25
    silence = b"\0\0" * sample_rate * duration
    for song_id in range(1, 4):
        path = music_dir / f"qa-song-{song_id}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(silence)
        paths.append(path)
    return paths


def seed_synthetic_library(config, database, music_dir):
    database.init_db()
    audio_paths = create_synthetic_wavs(music_dir)
    with database.get_connection_cm() as conn:
        for song_id, path in enumerate(audio_paths, start=1):
            conn.execute(
                """INSERT OR REPLACE INTO songs
                   (id, filepath, file_hash, artist, title, album, duration_secs,
                    energy, valence, cluster_id, ingest_phase, gain_db)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (song_id, str(path), f"isolated-qa-{song_id}", "QA Ensemble",
                 f"Silent Test {song_id}", "Isolated QA", 25.0,
                 0.5, 0.5, song_id - 1, "clustered", 0.0),
            )
        settings = {
            "onboarding_complete": "true", "play_mode": "sequential", "current_mode": "normal",
            "dj_effect_enabled": "false", "tts_enabled": "false", "talk_segments_enabled": "false",
            "recommendation_filters": '{"folders":[],"artists":[]}',
        }
        conn.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", settings.items())
        conn.commit()


def install_app_mocks(main_module, qa_port):
    """Replace model-dependent recommendation building with a deterministic queue."""
    from engine.queue import QueueManager

    def synthetic_rebuild(self, seed_id=None, advance_talk_counter=False, expected_generation=None):
        with self._condition:
            if self._shutdown.is_set() or (expected_generation is not None and
                                             expected_generation != self._rebuild_gen):
                return
        conn = self._db.get_connection()
        try:
            rows = conn.execute(
                "SELECT id, artist, title, energy, valence, bpm FROM songs "
                "WHERE ingest_phase='clustered' ORDER BY id"
            ).fetchall()
            entries = [{"song_id": row["id"], "artist": row["artist"] or "",
                        "title": row["title"] or "", "energy": row["energy"],
                        "valence": row["valence"], "bpm": row["bpm"],
                        "slot": "qa", "reason": "Synthetic isolated QA sequence", "type": "song"}
                       for row in rows if row["id"] != self._current_song_id]
        finally:
            conn.close()
        with self._condition:
            if self._shutdown.is_set() or (expected_generation is not None and
                                             expected_generation != self._rebuild_gen):
                return
            if not self._queue:
                self._queue.extend(entries)
            self._rebuilding = False
            self._condition.notify_all()
        self._broadcast("queue_updated", {"queue": self._get_display_queue()})
        self._broadcast("queue_rebuilding", {"rebuilding": False})

    QueueManager._rebuild_queue = synthetic_rebuild
    main_module._chromadb_health_check = lambda: {"status": "disabled in isolated QA"}
    # Also make direct recommendation endpoints deterministic and independent
    # of SQLite embeddings, Chroma, Ollama, OpenAI, or any model process.
    from engine import retriever
    from engine import taste

    def synthetic_candidates(*_args, **_kwargs):
        return [{"song_id": song_id, "score": 1.0 - 0.1 * song_id,
                 "energy": 0.5, "valence": 0.5, "cluster_id": song_id - 1,
                 "artist": "QA Ensemble", "title": f"Silent Test {song_id}", "source": "isolated-qa"}
                for song_id in range(1, 4)]

    retriever.get_embedding_dimension = lambda *_args, **_kwargs: 3
    retriever.build_candidate_list = synthetic_candidates
    retriever.search_by_vector = lambda *_args, **_kwargs: synthetic_candidates()
    taste.MemorySystem.get_taste_vectors = lambda *_args, **_kwargs: {"text": [0.0, 0.0, 0.0], "audio": None}

    # Keep native output lazy for browser QA; when VLC exists, force its audio
    # output to dummy and keep its configuration in the QA workspace.
    try:
        import vlc
        install_vlc_dummy_output(vlc, os.environ["VLC_CONFIG_PATH"])
    except ImportError:
        pass


def install_vlc_dummy_output(vlc_module, config_path):
    """Keep python-vlc's native Instance type while forcing dummy audio.

    python-vlc passes an integer native pointer back through Instance when it
    wraps API results. Replacing the class with a factory function breaks that
    pointer wrapping, so only patch the class allocator and leave pointers
    untouched.
    """
    instance_type = vlc_module.Instance
    original_new = instance_type.__new__
    extra_options = ("--quiet", "--aout=dummy", "--ignore-config", f"--config={config_path}")

    def isolated_instance_new(cls, *args):
        if len(args) == 1 and isinstance(args[0], int):
            return original_new(cls, *args)
        options = []
        for arg in args:
            options.extend(arg if isinstance(arg, (tuple, list)) else [arg])
        options.extend(extra_options)
        return original_new(cls, options)

    instance_type.__new__ = staticmethod(isolated_instance_new)


def run(port=18765):
    validate_venv(sys.executable, PROJECT_ROOT)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("QA port must be an integer from 1024 to 65535")
    runtime_root = PROJECT_ROOT / ".test-runtime"
    runtime_root.mkdir(exist_ok=True)
    qa_root = validate_qa_root(PROJECT_ROOT, runtime_root / f"qa-{secrets.token_hex(8)}")
    paths, _secret = configure_environment(qa_root, port)
    install_network_guard(port)
    install_storage_guard(qa_root)

    backend_dir = PROJECT_ROOT / "backend"
    sys.path.insert(0, str(backend_dir))
    import config
    config.API_PORT = port
    config.MUQ_ENABLED = False
    from db import database
    seed_synthetic_library(config, database, paths["music"])
    import main as main_module
    install_app_mocks(main_module, port)

    print(f"URL: http://127.0.0.1:{port}")
    print(f"Test password: {QA_PASSWORD}")
    import uvicorn
    uvicorn.run(main_module.app, host="127.0.0.1", port=port, proxy_headers=False,
                log_level="info", access_log=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    run(args.port)


if __name__ == "__main__":
    main()
