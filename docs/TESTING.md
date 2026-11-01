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

## Reliability regression contracts

The contract workflow now includes the playback API, session, disconnect, and
replay regressions as well as storage/security/media contracts. Its CPU-only
requirements include the real voice-processing import dependencies. The session
settings route unit tests replace unrelated ingest/vector adapters; their actual
route, settings, and command assertions remain enabled. No inference/model
environment is installed in CI. `frontend/package-lock.json` must agree with
`package.json`; validate with `npm ci` in a disposable checkout rather than
replacing a working development installation merely for verification.

Frontend deterministic tests cover transient event failures with unchanged event
identities, delayed state acknowledgements, local controls, instance-scoped lease
renewal, lease expiry, copied tab storage, and original-identity handoff recovery.
Backend regressions cover connection generations, exact-instance retirement,
persisted restart permits, reclaim receipts, cancellation/admission cleanup, and
replay with the real queue helper to detect duplicate durable feedback.

## Outstanding desktop acceptance and follow-ups

Carried forward from [the October 28 reliability record](archive/MODERNIZATION_PROGRESS.md#remaining-gates-and-risks-in-priority-order); this cleanup did not rerun application checks or establish a new acceptance result.

- Supervised real-library browser, Electron and VLC listening acceptance remains unclaimed, including sixty minutes per mode, audible controls, natural advance, both handoff directions, timers, independent sessions, DJ/TTS/effects, tray/media keys, restart and OS sleep/lock. A positive initial app retest is recorded historically; it is not the complete listening log.
- Verify page crash/reload during outages, explicit recovery and loss of unsent in-memory observations. Same-page retry coverage does not guarantee crash recovery.
- Verify frozen/background pages and HTML-only output fallback. Persisted permits assume a sane server wall clock; backend restart recovery does not promise uninterrupted audio.
- Live model quality, GPU speech, processed-audio quality and deployment checks remain manual gates. Phone direct LAN hosting is deferred; this list does not authorize exposing the server.
- The historical hosted CI failure and local fixes need a current hosted result before claiming hosted acceptance. Old test counts and CI status are evidence for their recorded revision only.

The older local handoff listed disconnect/reclaim/handoff/tab-identity review findings. Later reliability regression contracts above cover those areas; do not promote the older list to current defects without checking the later implementation and reproducing the concern.

### Deferred OmniVoice cold-start investigation

Recorded October 31 as future work only: initial OmniVoice requests reportedly time out and fall back to Piper, while later requests work. Model loading consuming the synthesis timeout is an unconfirmed hypothesis. Investigate readiness/loading separately from inference timeout, or warm-up before the first announcement, then verify cold startup and subsequent requests with TTS enabled. This note does not authorize implementation.

### Ingest recovery checks (November 1)

Run `backend\venv\Scripts\python -B scripts/run_tests.py -q backend/tests/test_ingest_jobs.py backend/tests/test_ingest_api_routes.py backend/tests/test_ingest_vectors.py backend/tests/test_ingest_metadata_audio.py` from the repository root. These tests use isolated storage and fake providers to check admission, failure recovery, metadata identity and complete clustering coverage. They do not establish model quality or GPU memory acceptance, and should not freeze a future model schedule or energy formula.

Manual acceptance remains pending: run individual phases with the actual managed chat, embedding and acestep processes; verify the prior model exits before the next loads, failed work resumes at its retained phase, and progress/controls recover after completion. Understand remains an explicit manual phase. Newly generated JSON uses a path hash to distinguish same-named recordings; ambiguous legacy metadata requires Understand again. Managed models are released at phase completion rather than all restarted together. Externally launched servers must be managed separately. Check real audio and lyrics output by listening; automated tests do not cover their quality.
