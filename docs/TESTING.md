# Isolated verification

From the repository root on Windows:

```powershell
backend\venv\Scripts\python -B scripts/run_tests.py -q
```

Use the existing backend virtual environment. The runner disables third-party pytest auto-loading and redirects temporary files and common model caches to `.test-runtime/` inside the checkout. Tests configure disposable storage before importing application modules, then use a fresh SQLite database per case. Synthetic song rows replace dependencies on the personal library. Production audio, metadata, embeddings, history and taste are not test fixtures.

Default pytest discovery is restricted to `backend/tests`. Several legacy `backend/test_*.py` files are maintenance scripts with import-time writes, not automated tests. Do not run them or explicitly pass them to pytest. Do not start the production application as a test: startup updates the database and removes cached/generated media.

The existing hand-rated energy correlation checks require a private fixture and skip in the synthetic suite. Passing unit tests do not establish live VLC, GPU/TTS, Electron or browser audio parity.

Temporary output is intentionally retained inside `.test-runtime/` for inspection. It is ignored by Git. No external temp directory or dependency installation is needed for the current backend suite.
