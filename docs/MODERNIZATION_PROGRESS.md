# Radio modernization progress

## Scope and safeguards

- User authorized implementation after initial read-only review.
- Branch: `codex/stability-server-architecture`.
- Starting commit: `63cc57562baa85645211fa120f42d78d0a67f8ac`. Audit/test foundation committed as `f5c9899`.
- Rollback tag: `codex/modernization-baseline-20261026`.
- All edits and temporary output stay inside this checkout. Original audio is read-only.
- One shared taste/history profile; independent playback sessions/queues.
- Mode switching transfers the current song and position explicitly.
- No live startup, live playback, migration, ingestion, or embedding regeneration as a test that mutates production data.

## Phase status

0. Audit and baseline: complete. Static audit and isolated baseline committed as `f5c9899`; baseline was 38 passed, 3 skipped. Runtime features remain unverified.
1. Stability: initial fixes verified, with coordinator-wide command/callback exclusion still open. Embedding lookup, nullable metadata, model output validation, filtered database fallback, queue worker lifetime, scheduling identity and manual-add wakeup races are covered. VLC emitters now have distinct generation identity, pre-start registration, a serial observation dispatcher and guarded native-handle lifetime. Astra passed the limited native stability gate; full feature parity remains unverified.
2. Recommendation/playback separation: pending.
3. Authenticated media streaming: pending.
4. Browser playback and feature parity: pending.
5. LAN access and recovery: pending.
6. Expanded tests/observability/CI: pending; basic isolation starts in phase 0.
7. Final validation: pending.

## Changes and checks

- Created dedicated branch and baseline tag without modifying production data.
- Restricted pytest discovery to backend/tests; operational test-named scripts are excluded.
- Test storage redirected before module collection; captured database path patched per test.
- Test fixtures now seed synthetic song records and reset settings caches per test.
- Added in-checkout temporary/cache configuration and storage isolation assertions.
- Baseline command: `backend\venv\Scripts\python -B scripts/run_tests.py -q`: 38 passed, 3 skipped. Three private energy-correlation checks skip without rated fixtures.
- First isolated run exposed three tests relying on real library clusters/energy bands; synthetic fixtures now supply 28 clustered songs. No application algorithm changed to satisfy those tests.
- Current guarded backend suite: **109 passed, 5 skipped**. Three skips require private hand-rated data; two native integration cases are opt-in.
- Opt-in libVLC check: `$env:RADIO_NATIVE_SMOKE='1'; backend\venv\Scripts\python -B scripts/run_tests.py backend/tests/test_vlc_native.py -q`: **2 passed**. Generated silence, dummy audio output, disposable SQLite/configuration only. Covers normal song completion, two-clip talk completion without song history, and dispatcher shutdown. Does not verify audible output or effects quality.
- Frontend: **14 Node tests passed**, Electron source syntax check passed, Vite production build passed. Existing duplicate `border` declarations in FiltersPanel produce warnings. Live Electron startup/closing and LAN WebSocket proxy remain untested.
- Red/Green evidence: reranker 5 failing regressions before fixes; queue fallback 16 failing cases and manual-add race 1 failing case before fixes; native emitter/ownership/volume/multi-clip/state-lock cases failed before their corresponding changes. Baseline monitor cancellation failure was replayed against the original source after its initial fix.
- The lifecycle test that uses a local socketpair hangs inside the sandbox and passed when run elevated. The audit runner permits sockets only for Windows asyncio's internal socketpair; production directories and writes outside the checkout are blocked, as are SQLite writes outside `.test-runtime/` and subprocesses.
- Red/Green TDD is required for new feature changes. Some tests and fixes were already made before that steering; those are historical changes and are not represented as having followed the newly stated sequence. No test or application run was performed for this documentation update.
- Temporary files and edits stay inside this checkout; no real library data was touched.

## Architecture risks to address

- Cross-component callbacks have time-of-check/time-of-use races. A serialized coordinator is needed to order playback, queue, and callback state changes.
- Mutable DJ, mood, energy, and sleep state is currently global. It must be scoped to each browser session before independent sessions can safely use those features.

## Rollback

Stop the development instance before changing branches. Inspect `git status` and preserve desired work, then switch to `main` or create a recovery branch at the baseline tag. Do not use `git clean`, reset data directories, or delete ignored files. Branch switching does not restore runtime data. No production schema migration has been performed.
