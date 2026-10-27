# Radio modernization progress

## Scope and safeguards

- User authorized implementation after initial read-only review.
- Branch: `codex/stability-server-architecture`.
- Baseline: `63cc57562baa85645211fa120f42d78d0a67f8ac`.
- Rollback tag: `codex/modernization-baseline-20261026`.
- All edits and temporary output stay inside this checkout. Original audio is read-only.
- One shared taste/history profile; independent playback sessions/queues.
- Mode switching transfers the current song and position explicitly.
- No live startup, live playback, migration, ingestion, or embedding regeneration as a test that mutates production data.

## Phase status

0. Audit and baseline: in progress. Static architecture review completed; isolated baseline tests being established. Runtime features remain unverified.
1. Stability: pending baseline. Confirmed embedding lookup defect and worker lifecycle risks identified.
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

## Rollback

Stop the development instance before changing branches. Inspect `git status` and preserve desired work, then switch to `main` or create a recovery branch at the baseline tag. Do not use `git clean`, reset data directories, or delete ignored files. Branch switching does not restore runtime data. No production schema migration has been performed.
