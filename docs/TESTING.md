# Isolated verification

From the repository root on Windows:

```powershell
backend\venv\Scripts\python -B scripts/run_tests.py -q
```

Use the existing backend virtual environment. The runner disables third-party pytest auto-loading and redirects temporary files and common model caches to `.test-runtime/` inside the checkout. Tests configure disposable storage before importing application modules, then use a fresh SQLite database per case. Synthetic song rows replace dependencies on the personal library. Production audio, metadata, embeddings, history and taste are not test fixtures. Python audit guards block writes to production directories or outside the checkout, SQLite writes outside `.test-runtime/`, subprocesses, and socket use except Windows asyncio's internal socketpair.

Default pytest discovery is restricted to `backend/tests`. Several legacy `backend/test_*.py` files are maintenance scripts with import-time writes, not automated tests. Do not run them or explicitly pass them to pytest. Do not start the production application as a test: startup updates the database and removes cached/generated media.

The historical baseline committed as `f5c9899` was 38 passed, 3 skipped. Passing unit tests do not establish audible VLC, GPU/TTS, Electron or browser audio parity.

For new feature changes, use Red/Green TDD: add a failing test, implement the change, then run the relevant test. Some tests and fixes predated that instruction and should be reported as such rather than retroactively described as Red/Green.

## Manual app walkthrough

Use the separate isolated launcher when manually checking app controls or session behavior. It creates a new `.test-runtime/qa-*` workspace, three silent synthetic WAVs, a disposable SQLite database, a loopback-only server, and a deterministic recommendation sequence. It does not launch the production app against the personal library. The detailed walkthrough, expected behavior, reconnect/transfer checks, limitations, and bug report template are in [APP_USAGE_CHECKLIST.md](APP_USAGE_CHECKLIST.md).

Start it from the repository root in PowerShell:

```powershell
backend\venv\Scripts\python scripts\run_isolated_app.py --port 18765
```

Open `http://127.0.0.1:18765` and use password `isolated-test`. Stop it with **Ctrl+C** in the launcher terminal. Recommendation/model generation and Chroma health checks are mocked or disabled; the real routes, playback plans, session store, browser streaming, and UI remain active. The launcher blocks outbound connections and confines writes to its synthetic workspace. The silent files do not test audible quality. Do not use the production backend for this walkthrough.

The isolated native output uses VLC dummy audio when VLC is installed; desktop-output switching can check state transfer but produces no sound. Without VLC, skip native-output checks and use browser playback.

Temporary output is intentionally retained inside `.test-runtime/` for inspection. It is ignored by Git. No external temp directory or dependency installation is needed for the current backend suite.

Optional native integration uses libVLC with generated silence and dummy audio output:

```powershell
$env:RADIO_NATIVE_SMOKE='1'
backend\venv\Scripts\python -B scripts/run_tests.py backend/tests/test_vlc_native.py -q
```

This tests native event delivery without the original library, speaker output, models, or production history. It is separate from ordinary CI and does not prove audible quality, effects, or all advanced feature parity.
