"""Run isolated tests with all temporary/cache files inside this checkout."""
import os
from pathlib import Path
import subprocess
import sys
import socket

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".test-runtime"


def install_guards():
    """Fail closed if Python test code tries real storage or network services."""
    protected = [ROOT / "data", ROOT / "audio_files", ROOT / "docs" / "understand",
                 ROOT / "backend" / "announcer" / "voices"]

    def check_write(value):
        if isinstance(value, int) or value is None:
            return
        path = Path(os.fsdecode(value)).resolve()
        if path == Path(os.devnull).resolve():
            return
        if not path.is_relative_to(ROOT) or any(path.is_relative_to(p) for p in protected):
            raise PermissionError(f"Isolated tests cannot write to {path}")

    def guard(event, args):
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                check_write(path)
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"):
            check_write(args[0])
        elif event in ("os.rename", "os.link", "os.symlink"):
            check_write(args[0])
            check_write(args[1])
        elif event == "sqlite3.connect":
            database = str(args[0])
            if database != ":memory:" and not Path(database).resolve().is_relative_to(RUNTIME):
                raise PermissionError("Tests may open SQLite only inside .test-runtime")
        elif event in ("socket.connect", "socket.bind", "subprocess.Popen", "os.system"):
            # Windows asyncio needs a socket pair for its internal wakeup pipe.
            if event.startswith("socket.") and sys._getframe(1).f_code is getattr(socket.socketpair, "__code__", None):
                return
            raise PermissionError(f"External services/processes are disabled in unit tests: {event}")

    sys.addaudithook(guard)
    sys.radio_test_isolated = True


def main():
    temp = RUNTIME / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "HF_HOME": str(RUNTIME / "huggingface"),
        "TORCH_HOME": str(RUNTIME / "torch"),
        "MPLCONFIGDIR": str(RUNTIME / "matplotlib"),
        "XDG_CACHE_HOME": str(RUNTIME / "cache"),
        "NUMBA_CACHE_DIR": str(RUNTIME / "numba"),
    })
    return subprocess.call(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", *sys.argv[1:]], cwd=ROOT, env=env,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        install_guards()
        import pytest
        raise SystemExit(pytest.main(sys.argv[2:]))
    raise SystemExit(main())
