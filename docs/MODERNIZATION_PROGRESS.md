# Radio modernization progress

## Scope and safeguards

- User authorized implementation after initial read-only review.
- Branch: `codex/stability-server-architecture`.
- Starting commit: `63cc57562baa85645211fa120f42d78d0a67f8ac`. Audit/test foundation committed as `f5c9899`.
- Rollback tag: `codex/modernization-baseline-20261026`.
- All edits and temporary output stay inside this checkout. Original audio is read-only.
- One shared taste/history profile; independent playback sessions/queues.
- Mode switching transfers the current song and position explicitly.
- Automated tests and the isolated launcher never write production history or library state. On October 28, 2026, the user explicitly permitted real-history updates during manual checks.

## Phase status

0. Audit and baseline: complete. Static audit and isolated baseline committed as `f5c9899`; baseline was 38 passed, 3 skipped. Runtime features remain unverified.
1. Stability: core queue, playback coordinator, and native observation guards are implemented with targeted tests. The limited libVLC smoke gate passed against generated silence; audible quality remains unverified.
2. Recommendation/playback separation: integrated. Prepared plans, session queues and reservations, coordinator event acceptance, bounded empty-queue fallback, and session-local recommendation settings are wired. The fallback does not record history or consume a pick.
3. Authenticated media streaming: implemented. Authenticated session/media routes, stable track/resource IDs, path validation, and range streaming are integrated and have focused contract tests.
4. Browser playback and output transfer: implemented in the React app and session service. Browser audio, session controls, observed events, and browser/VLC handoff are wired. Isolated browser retest is pending after a lyrics-render crash fix.
5. LAN access and recovery: access policy is documented; no LAN or reverse-proxy validation has been performed.
6. Expanded tests/observability/CI: a lean backend contract workflow and frontend test/build workflow are added. Hosted CI has not yet run; the full backend suite needs a refreshed count.
7. Final validation: pending manual browser retest and user checks of live model/audio, Electron, OS integration, and any requested LAN deployment.

## Changes and checks

- Created dedicated branch and baseline tag without modifying production data.
- Restricted pytest discovery to backend/tests; operational test-named scripts are excluded.
- Test storage redirected before module collection; captured database path patched per test.
- Test fixtures now seed synthetic song records and reset settings caches per test.
- Added in-checkout temporary/cache configuration and storage isolation assertions.
- Baseline command: `backend\venv\Scripts\python -B scripts/run_tests.py -q`: 38 passed, 3 skipped. Three private energy-correlation checks skip without rated fixtures.
- First isolated run exposed three tests relying on real library clusters/energy bands; synthetic fixtures now supply 28 clustered songs. No application algorithm changed to satisfy those tests.
- Earlier Phase 1 guarded backend checkpoint: **109 passed, 5 skipped**. Three skips require private hand-rated data; two native integration cases are opt-in.
- Opt-in libVLC check: `$env:RADIO_NATIVE_SMOKE='1'; backend\venv\Scripts\python -B scripts/run_tests.py backend/tests/test_vlc_native.py -q`: **2 passed**. Generated silence, dummy audio output, disposable SQLite/configuration only. Covers normal song completion, two-clip talk completion without song history, and dispatcher shutdown. Does not verify audible output or effects quality.
- Earlier frontend checkpoint: **14 Node tests passed**, Electron source syntax check passed, Vite production build passed. Existing duplicate `border` declarations in FiltersPanel produce warnings. Live Electron startup/closing and LAN WebSocket proxy remain untested.
- Phase 1 verified changes are committed through `ad18699`. Phase 2 foundations include shared PreparedPlan building and queue reserve/commit/release/discard, injected player/energy/talk/announcement contexts, browser-scoped namespaces and mood support, and additive durable PlaybackStore work. Coordinator fake-adapter tests cover stop/preparation fences, receipt retries, transfer confirmation, speech/song commit, and failures.
- Historical checkpoints: baseline **38 passed, 3 skipped**; Phase 1 **109 passed, 5 skipped**; later full backend **256 passed, 6 skipped**; earlier frontend checkpoints **14** and **29** passed. These are not current counts.
- Most recently reported full backend checkpoint before recent integration changes: **375 passed, 8 skipped**. Most recently reported frontend checkpoint before recent integration changes: **58 passed**. Do not present either as a refreshed post-integration result.
- A real browser was manually exercised through the isolated QA server on port `18765`, using only a synthetic database and silent WAVs. That exposed a missing-lyrics rendering crash; a fix is in place and the browser run is pending retest. Model recommendation is mocked in this launcher; it does not validate live recommendation quality.
- `.github/workflows/contract-tests.yml` creates the required `backend/venv` on Windows/Python 3.13, installs only `backend/requirements-test.txt`, and runs selected isolated API-security, media, session-store/coordinator, settings, and play-event contracts. A separate Node 22 job runs frontend tests and build with Electron's binary download disabled. It intentionally avoids the full audio/model dependency set. Hosted CI has not yet run, and no CI result is claimed here.
- Red/Green evidence: reranker 5 failing regressions before fixes; queue fallback 16 failing cases and manual-add race 1 failing case before fixes; native emitter/ownership/volume/multi-clip/state-lock cases failed before their corresponding changes. Baseline monitor cancellation failure was replayed against the original source after its initial fix.
- The lifecycle test that uses a local socketpair hangs inside the sandbox and passed when run elevated. The audit runner permits sockets only for Windows asyncio's internal socketpair; production directories and writes outside the checkout are blocked, as are SQLite writes outside `.test-runtime/` and subprocesses.
- Red/Green TDD is required for new feature changes. Some tests and fixes were already made before that steering; those are historical changes and are not represented as having followed the newly stated sequence. No test or application run was performed for this documentation update.
- Temporary files and edits stay inside this checkout; no real library data was touched.

## Architecture risks to address

- Device and session state are process-local. Multiple API workers are not supported for one playback instance.
- Browser/native transfer and reconnect have focused coverage, but live real-media recovery and browser behavior after a deployed app restart still need manual verification.
- Real audio, metadata JSON, database, and embedding data remain untouched. Test and temporary outputs stay inside the checkout.

## Current architecture and manual QA references

The current routing, browser audio, authenticated media, session context, and output transfer design is summarized in [ARCHITECTURE.md](ARCHITECTURE.md). Safe manual steps and expected behavior are in [APP_USAGE_CHECKLIST.md](APP_USAGE_CHECKLIST.md). The isolated launcher command is:

```powershell
backend\venv\Scripts\python scripts\run_isolated_app.py --port 18765
```

Its three 25-second WAVs are silent and recommendation/model generation is deterministic. It confines writes to a new `.test-runtime/qa-*` directory, disables TTS/managed processes, and blocks outbound sockets. This provides an app-control walkthrough, not a real-audio or model-quality test.

## Rollback

Stop the development instance before changing branches. Inspect `git status` and preserve desired work, then switch to `main` or create a recovery branch at the baseline tag. Do not use `git clean`, reset data directories, or delete ignored files. Branch switching does not restore runtime data. No production schema migration has been performed.
