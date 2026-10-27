"""Run isolated tests with all temporary/cache files inside this checkout."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".test-runtime"


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
        [sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=ROOT, env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
