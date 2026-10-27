# Isolated verification

From the repository root on Windows:

```powershell
backend\venv\Scripts\python -B scripts/run_tests.py -q
```

Use the existing backend virtual environment. The runner disables third-party pytest auto-loading and redirects temporary files and common model caches to `.test-runtime/` inside the checkout. Tests configure disposable storage before importing application modules, then use a fresh SQLite database per case. Synthetic song rows replace dependencies on the personal library. Production audio, metadata, embeddings, history and taste are not test fixtures. Python audit guards block writes to production directories or outside the checkout, SQLite writes outside `.test-runtime/`, subprocesses, and socket use except Windows asyncio's internal socketpair.

Default pytest discovery is restricted to `backend/tests`. Several legacy `backend/test_*.py` files are maintenance scripts with import-time writes, not automated tests. Do not run them or explicitly pass them to pytest. Do not start the production application as a test: startup updates the database and removes cached/generated media.

The existing hand-rated energy correlation checks require a private fixture and skip in the synthetic suite. The baseline committed as `f5c9899` was 38 passed, 3 skipped. Phase 1 focused embedding checks passed 7 tests; the latest isolated lifecycle check passed 2. The full suite is pending. The Windows lifecycle test hangs inside the sandbox on its local socketpair and passed when run elevated; do not run tests or the application from the sandbox without that known limitation in mind. Passing unit tests do not establish live VLC, GPU/TTS, Electron or browser audio parity.

For new feature changes, use Red/Green TDD: add a failing test, implement the change, then run the relevant test. Some tests and fixes predated that instruction and should be reported as such rather than retroactively described as Red/Green.

Temporary output is intentionally retained inside `.test-runtime/` for inspection. It is ignored by Git. No external temp directory or dependency installation is needed for the current backend suite.

Optional native integration uses libVLC with generated silence and dummy audio output:

```powershell
$env:RADIO_NATIVE_SMOKE='1'
backend\venv\Scripts\python -B scripts/run_tests.py backend/tests/test_vlc_native.py -q
```

This tests native event delivery without the original library, speaker output, models, or production history. It is separate from ordinary CI and does not prove audible quality, effects, or all advanced feature parity.
