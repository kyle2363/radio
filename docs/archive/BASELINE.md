# Modernization baseline

## Baseline identity and limitations

Static source review dated October 26, 2026. Starting commit: `63cc57562baa85645211fa120f42d78d0a67f8ac`. Original branch: `main`. Implementation branch: `codex/stability-server-architecture`. Rollback tag: `codex/modernization-baseline-20261026`.

The initial Git status was clean for tracked files. That does **not** establish an inventory or backup of ignored files. `.gitignore` excludes audio, runtime data, documentation, voices, and environment files. Documentation intended for version control must be added selectively; do not add all ignored documentation, which includes original metadata.

This baseline was established by reading source and Git metadata only. No application startup, backend imports, playback, database mutations, embedding operations, builds, or tests were performed for this document. Later results belong in `MODERNIZATION_PROGRESS.md` and should name the tested revision and command. Static findings below are not claims of reproduced live failures.

## Initial findings in priority order

Line references identify the original baseline; use the symbol names after edits shift lines.

| Priority | Observation and source | Trigger / risk | Smallest proposed response |
| --- | --- | --- | --- |
| Critical for test safety | `backend/test_delete.py:2-6` runs database initialization and a song deletion at module import | Broad test discovery imports a maintenance script and mutates the real library | Restrict test discovery to reviewed tests; never execute maintenance scripts as tests |
| Critical for test safety | `backend/db/database.py:15` captures DB_PATH at import; `backend/tests/conftest.py` patches only config afterward | Earlier test imports can leave database connections pointed at the real DB despite fixture setup | Establish isolated paths before application imports and assert effective database destination |
| High | Startup in `backend/main.py:53-120` writes DB/session/settings state and clears generated media | A casual startup/smoke test affects the real installation | Separate test runtime paths and disable process/model startup before integration tests |
| High | `backend/main.py:5053` binds `0.0.0.0`; baseline HTTP/WebSocket code has no general authentication boundary | Network peers can reach sensitive operations if the host firewall allows it | Conservative binding defaults; explicit authenticated LAN configuration |
| High concurrency risk | `player.py:_fire_song_ended` (`335`) dispatches only song ID, while `main.py:_on_song_ended` (`128`) advances mutable current state | A delayed completion callback overlaps play/skip/stop and can act on a later playback instance | Pass immutable instance/session identity; accept completion once in a coordinator; reproduce with controlled scheduling |
| High concurrency risk | `queue.py:on_song_started` (`167`) writes `_queue` under preview lock while `pop_next` (`219`) uses condition lock | Queue swap overlaps consumption/rebuild | Use a consistent ownership lock and deterministic overlap tests |
| High concurrency risk | `queue.py:_rebuild_queue` (`360`) reads generation inside the newly scheduled worker | Old worker starts after a newer rebuild was scheduled and adopts the latest generation | Allocate/capture immutable generation at scheduling time |
| High worker-lifecycle defect | `queue.py` TTS checker (`1564-1565`) sets then clears the same event | Previous worker misses stop signal, remains active alongside replacement | Per-worker cancellation events and tracked shutdown |
| High shutdown limitation | `main.py` lifespan (`281-291`) does not stop/join queue workers; Electron `shutdownBackend()` immediately kills after a fire-and-forget request | Shutdown interrupts writes/generated work or leaves workers alive in embedded/test lifespan | Explicit bounded worker shutdown and awaited graceful exit |
| Medium confirmed code defect | `_fetch_song_embeddings`, `main.py:704`, uses undefined `client` for audio lookup | MuQ enabled: NameError is swallowed and audio embedding is absent | Resolve shared client explicitly; test text/audio success and failure independently |
| Medium network defect | `frontend/src/api/index.js:1-6` uses viewer localhost for remote hosts and a fixed localhost WebSocket | LAN browser targets itself instead of server | Central configurable/same-origin API and WebSocket resolution |
| Architectural blocker | QueueManager calls global VLC in `queue.py:1669,1752,1765,1819`; main/player/queue all own feedback pieces | Adding browser audio without extraction preserves shared state and can double-play/double-record | Introduce coordinator and adapter boundaries incrementally |

The completion and queue race entries describe source-backed hazards requiring deterministic regression reproductions. Existing generation checks, explicit-stop flags, snapshotting, and talk callback locking are real mitigations and should be retained or replaced deliberately; this audit does not assume every theoretical interleaving has occurred in the user's installation.

## Runtime validation matrix

All statuses describe the pre-change runtime baseline. No row is inferred to pass merely because the code exists.

| Area | Status | Safe validation needed |
| --- | --- | --- |
| Backend startup and readiness | NOT TESTED | Isolated paths, fake optional services, lifecycle integration |
| Library load and metadata compatibility | NOT TESTED | Synthetic fixtures; controlled read-only original-data inspection |
| VLC play/pause/resume/seek/stop | NOT TESTED | Fake adapter tests, then explicit isolated live audio smoke test |
| Skip, queue, replay, feedback | NOT TESTED | Temporary SQLite and deterministic event ordering |
| LLM and deterministic recommendations | NOT TESTED | Fixed candidates, provider failures/timeouts, no real embeddings written |
| AI DJ, TTS, speech-only chains | NOT TESTED | Fake generated media plus isolated real-engine check when available |
| Effects, EQ, normalization, transitions | NOT TESTED | Synthetic audio and capability matrix for each output mode |
| React/Electron, tray, widget, mini player | NOT TESTED | Frontend build/tests and controlled desktop interaction |
| Sleep timer and media keys | NOT TESTED | Fake clock/adapter, then desktop smoke test |
| Shutdown and restart | NOT TESTED | Cancellation/joins, interrupted work, explicit process ownership |
| Browser streaming and seeking | NOT IMPLEMENTED AT BASELINE | Range/security integration and actual browser playback |
| Independent sessions and mode transfer | NOT IMPLEMENTED AT BASELINE | Two devices plus observer, transfer song/position without duplicate history |
| LAN authentication and recovery | NOT IMPLEMENTED AT BASELINE | Authenticated HTTP/audio/WS, disconnect/reload/restart snapshots |
| Original audio/data integrity | NOT VERIFIED BY INVENTORY | Read-only manifest and comparison; never infer integrity from Git |

## Test entry-point safety

Do not run unrestricted `pytest` discovery against this checkout. Files named `test_*.py` outside the reviewed test suite include maintenance operations, and existing fixtures require repair before they can be trusted. Ordinary regression tests must use fake VLC, model providers, and Chroma access and synthetic audio; no GPU, music library, or running model server should be required.

Use only `backend/venv/Scripts/python` for Python commands. All temporary directories, test databases, caches, reports, and artifacts for this task must remain inside the radio project. Before importing application modules in a test process, set isolated paths, disable managed services, and ensure settings/cache singletons cannot carry real runtime state into the test.

## Rollback and acceptance status

The baseline Git tag protects tracked source only. To return to baseline code after saving current implementation work, stop an isolated/test runtime and switch to the baseline reference or a branch from it; do not use destructive clean/reset commands against this working directory. Existing untracked music, JSONs, databases, embeddings, and voices must remain in place. No schema migration or original-data write was performed for this audit, so this document introduces no data rollback operation.

Phase 0 has a source architecture map and rollback reference. Its functional baseline remains explicitly unverified until safe isolated checks are executed. Full modernization acceptance cannot be claimed until both legacy desktop playback and browser playback pass the documented compatibility, reliability, security, and data-integrity checks.
