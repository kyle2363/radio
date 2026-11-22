"""Run isolated tests with all temporary/cache files inside this checkout."""
import os
from pathlib import Path
import re
import subprocess
import sys
import socket
import time

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


def shard_plugin(pytest, index, count):
    """Keep every Nth collected test so parallel workers split the run evenly."""

    class ShardPlugin:
        # trylast: runs after -k/-m deselection, so shards split only the selected tests.
        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, config, items):
            keep = items[index::count]
            kept = {id(item) for item in keep}
            config.hook.pytest_deselected(items=[item for item in items if id(item) not in kept])
            items[:] = keep

    return ShardPlugin()


# Options whose state is shared across a run (last-failed cache, debugger, early stop, one report file).
SERIAL_ONLY = ("--lf", "--last-failed", "--ff", "--failed-first", "--sw", "--stepwise", "--pdb",
               "-x", "--exitfirst", "--maxfail", "--junitxml", "--junit-xml", "--basetemp", "-s")


def parse_jobs(args):
    """Pop -j/--jobs N. Default: parallel for a full run, serial when tests are named."""
    jobs, rest, i = None, [], 0
    while i < len(args):
        arg = args[i]
        if arg in ("-j", "--jobs") and i + 1 < len(args):
            jobs, i = int(args[i + 1]), i + 2
            continue
        if arg.startswith("--jobs="):
            jobs = int(arg.split("=", 1)[1])
        else:
            rest.append(arg)
        i += 1
    if jobs is None:
        named = any(not a.startswith("-") for a in rest)
        serial = any(a.split("=", 1)[0] in SERIAL_ONLY for a in rest)
        jobs = 1 if named or serial else max(1, min(8, (os.cpu_count() or 2) // 2))
    return max(1, jobs), rest


SUMMARY = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")


def run_parallel(jobs, args, env):
    """Run shards concurrently, each guarded and isolated, then print one combined summary."""
    logs = RUNTIME / "shards"
    logs.mkdir(parents=True, exist_ok=True)
    workers = []
    for index in range(jobs):
        log = (logs / f"shard-{index}.log").open("w", encoding="utf-8", errors="replace")
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--shard", f"{index}/{jobs}",
                   "-p", "no:cacheprovider", f"--basetemp={RUNTIME / f'pytest-shard-{index}'}", *args]
        workers.append((index, log, subprocess.Popen(
            command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)))
    started = time.monotonic()
    totals, codes = {}, []
    for index, log, process in workers:
        code = process.wait()
        log.close()
        text = (logs / f"shard-{index}.log").read_text(encoding="utf-8", errors="replace")
        lines = [line for line in text.splitlines() if line.strip()]
        final = lines[-1] if lines else ""
        for count, kind in SUMMARY.findall(final):
            kind = "errors" if kind.startswith("error") else kind
            totals[kind] = totals.get(kind, 0) + int(count)
        codes.append(code)
        if code not in (0, 5):
            print(f"\n===== shard {index + 1}/{jobs} failed (exit {code}); full log: {logs / f'shard-{index}.log'} =====")
            print(text)
    summary = ", ".join(f"{totals[k]} {k}" for k in ("failed", "errors", "passed", "skipped", "xfailed", "xpassed") if totals.get(k))
    print(f"{summary or 'no tests ran'} in {time.monotonic() - started:.1f}s across {jobs} workers "
          f"(logs: {logs.relative_to(ROOT)})")
    failures = [c for c in codes if c not in (0, 5)]
    if failures:
        return max(failures)
    return 0 if any(c == 0 for c in codes) else 5


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
    jobs, args = parse_jobs(sys.argv[1:])
    if jobs > 1:
        return run_parallel(jobs, args, env)
    return subprocess.call(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", *args], cwd=ROOT, env=env,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        install_guards()
        import pytest
        args, plugins = sys.argv[2:], []
        if args[:1] == ["--shard"]:
            index, count = map(int, args[1].split("/"))
            args, plugins = args[2:], [shard_plugin(pytest, index, count)]
        raise SystemExit(pytest.main(args, plugins=plugins))
    raise SystemExit(main())
