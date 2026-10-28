# Radio architecture

## Scope and evidence

This is the initial static architecture baseline, inspected on October 26, 2026 at commit `63cc57562baa85645211fa120f42d78d0a67f8ac`. It describes existing code, not a claim that runtime behavior has passed validation. See [BASELINE.md](BASELINE.md) for limitations and identified defects. Source line numbers in that document refer to the baseline commit.

The current modernization branch is `stability-server-architecture` (renamed by the user from `codex/stability-server-architecture`), with implementation checkpoint `c065ed3`; the rollback reference is `codex/modernization-baseline-20261026`. Git does not protect the ignored runtime data, original audio, or source JSON metadata.

## Existing system

```mermaid
flowchart TD
    Electron[Electron windows, tray, media keys] --> React[React controls and state hooks]
    Electron --> API[FastAPI main.py]
    React -->|HTTP commands and polling| API
    API -->|WebSocket broadcasts| React
    API --> Queue[QueueManager]
    Queue --> Memory[Taste, mood, energy, constellations]
    Memory --> Retrieval[Vector retrieval and behavioral filters]
    Retrieval --> Slots[Slot allocation]
    Slots --> Rank[LLM ranking with deterministic fallback]
    Rank --> Transition[Transition scoring]
    Transition --> Queue
    Queue --> Speech[Announcements, talk segments, voice manager]
    Queue --> Effects[Processed audio cache]
    Queue --> VLC[VLC player]
    API --> VLC
    VLC -->|Deferred completion callback| API
    API --> DB[(SQLite)]
    Queue --> DB
    VLC --> DB
    Retrieval --> Chroma[(ChromaDB)]
    Speech --> Models[Optional local or remote AI services]
```

The backend owns audio output. The frontend sends commands and displays backend state; it does not currently own music decoding or output. Queue construction, physical playback, and feedback persistence cross module boundaries in both directions.

## Startup and shutdown

`frontend/package.json` starts Vite and Electron together. Electron's `startBackend()` in `frontend/electron/main.cjs` spawns `backend/main.py`, preferring the project virtual environment. The current launcher falls back to a system Python if the virtual environment is missing; development and verification must use the project virtual environment explicitly.

`backend/config.py` loads the root `.env`, defines original-data and generated-cache paths, and establishes model/provider defaults. FastAPI's lifespan initializes/migrates SQLite, closes stale listening sessions, prunes old mood repulsion, adjusts settings, and starts configured managed processes. It also performs a background Chroma health check, cleans generated effect/separator/TTS caches, initializes VLC and the queue/mood singletons, installs completion callbacks, starts a preview rebuild, probes TTS, and starts the WebSocket broadcast worker.

Startup therefore mutates persistent state and caches. Starting the normal application is not a safe read-only baseline check.

The lifespan shutdown stops managed external processes, cancels the broadcast worker, and stops VLC. The original implementation does not join queue workers or await the cancelled broadcast task. Electron sends `/api/shutdown` and immediately attempts a process-tree kill; the graceful request is not awaited. These paths need explicit lifecycle tests before changing ownership.

## Storage and identifiers

| Resource | Location and owner | Safety boundary |
| --- | --- | --- |
| Original music | `audio_files/`; scanned by ingest, read by VLC | Irreplaceable originals; never write, move, or delete during this work |
| Primary descriptive metadata | `docs/understand/` JSON; ingest metadata loader | Read-only originals; metadata writer can intentionally modify these in normal app use |
| Library, history, settings | `data/radio.db`; `db/database.py` | No automated tests against this database |
| Text/audio vectors | `data/chroma/`; shared `db/chroma.py` singleton | No regeneration, reset, or write-producing compatibility probe |
| Speech and effects | `data/tts_cache/`, `data/dj_effect_cache/` | Generated resources; currently subject to startup/age cleanup |
| Artwork, alignment, separated vocals | `data/art/`, `data/lyrics_timing/`, separator directories | Preserve originals and existing derived data; isolate test output |
| Model files and logs | configured paths, `data/logs/` | Do not change model installations or unrelated configuration |

SQLite song IDs identify library rows. Paths locate physical media; scanner deduplication uses a path hash. Chroma collections use string song IDs. The `songs` and configured `songs_audio` collections hold distinct embedding spaces. SQLite connections enable WAL, foreign keys, and a busy timeout. `database.py` originally captures `config.DB_PATH` at import, a significant test-isolation concern.

`db/chroma.py` lazily constructs a persistent singleton and can create directories/collections. Opening it is not equivalent to a filesystem-only inspection. Counts in `AGENTS.md` are reference values, not a fresh inventory.

## Recommendation and queue flow

`engine/taste.py` maintains multiple timescales of taste. `retriever.py` combines text/audio vector candidates with mood, vibe lock, recency, blacklist, and feedback filtering. `slots.py`, `reranker.py`, and `transitions.py` allocate and rank candidates. `constellations.py` adds taste-pocket context. QueueManager coordinates these modules and has deterministic/single-song fallback paths; the ranking algorithm should remain unchanged during extraction.

QueueManager holds AI, manual, preview, and pending-talk queues, current song/session, memory, mood, recently recommended IDs, generation counters, speech/effect caches, and background worker events. `pop_next()` prioritizes pending talk, then manual songs, then AI songs; it waits on a condition for rebuild work and falls back outside the condition. Consuming a recommendation records queue history. That is distinct from actual playback history.

`on_song_started()` updates context, swaps or clears preview state, and schedules a rebuild. Rebuilds take a playback-context snapshot and perform expensive ranking and speech work before publishing results. Generations attempt to reject stale work. The preview watchdog and TTS checker can independently schedule or refresh work; cancellation, generation ownership, and consistent locking need regression coverage.

## Playback and feedback

`engine/player.py` wraps VLC media-list playback. `play_song()` starts a song and `play_chain()` chains speech/song resources. VLC callbacks track chain position and distinguish speech from songs. Generation and explicit-stop flags attempt to suppress stale callbacks. Completion dispatch moves application callbacks to background threads so subsequent VLC commands do not run directly inside VLC's event thread.

The player writes a completed play event. The callback registered in `main.py` updates queue/derived completion state, records energy, emits an event, consumes the next queue item, records a playing event, starts playback, and schedules the next rebuild. API playback routes also write history and invoke QueueManager. There is no single authoritative playback-instance event processor across these paths.

QueueManager still calls the global player directly, even though its constructor accepts a player. Sleep timer and Electron controls also target the shared desktop playback state. Browser session independence cannot be achieved by changing only the frontend.

## Speech, effects, and external services

`announcer/announcements.py` generates short DJ lines; `talk_segments.py` schedules longer segments and persona behavior. `voice_manager.py` coordinates OmniVoice and Piper, generated WAV processing, caching, and voice selection. QueueManager prepares speech ahead of use and can play speech-only chains. Speech must not become song listening history.

`engine/dj_effects.py` generates cached processed media; VLC additionally owns playback-specific gain/EQ behavior. Generated files currently have paths rather than a complete streamable-resource ownership contract. Browser support needs resource IDs and retention while preloaded, playing, or reconnectable, before exposing cleanup-managed files through HTTP.

`llm/registry.py` selects cached chat/embed providers and fallback behavior. Ollama and OpenAI-compatible implementations provide separate chat/embed interfaces. MuQ and OmniVoice are optional inference engines. `engine/process_manager.py` owns configured subprocess launch, health checks, log capture, and shutdown. Missing optional services must remain recoverable without blocking basic library playback.

## HTTP, WebSocket, and frontend

`frontend/src/api/index.js` centralizes HTTP commands. At baseline its non-localhost branch points back to the viewer's localhost and WebSocket uses a fixed localhost URL. React state hooks combine polling with WebSocket events. Electron tray/widget/mini-player routes likewise assume a local shared backend.

`main.py` hands cross-thread events to an asyncio queue using `run_coroutine_threadsafe`. A broadcast worker serializes outgoing writes and removes failed clients. The same write lock covers direct replies. Events are broadcast to connected clients without playback-session scoping; they are notifications, not a durable replay log. Recovery must be based on authoritative snapshots and revisions.

The baseline server binds to all interfaces when started as a script. CORS permits development origins, but CORS does not authenticate requests. Remote access requires an explicit authentication and origin policy for HTTP, WebSocket, and media requests.

## Concurrency boundaries

Concurrent actors include FastAPI handlers, VLC callback threads, deferred completion callbacks, recommendation rebuilds, preview/TTS workers, ingest jobs, model operations, managed-process monitoring, and the asyncio broadcast worker. Shared state is protected by several different locks, including player, callback, queue condition, preview, speech cache, and singleton locks. Lock coverage is not uniform. Expensive operations and native callbacks make lock order and bounded shutdown particularly important.

## Agreed target boundaries

The user selected **shared taste/history with independent playback sessions** and **explicit playback-mode transfer of the current song and position**. Shared history does not imply shared queues or permission for a browser to interrupt another device.

| Component | Intended authority |
| --- | --- |
| Recommendation service | Existing retrieval/ranking semantics; no physical audio output |
| Queue/session service | Session-specific queue, reservations, revision, current logical media |
| Playback coordinator | Playback instance, device ownership, state transitions, idempotent feedback |
| VLC adapter | Existing server audio transport and native callbacks |
| Browser client | Local media output and observed playback events |
| Media service | Authorized stable-ID streams, validated paths/ranges, generated-resource lifetime |
| Shared persistence | Library, taste inputs, history, settings, embeddings |
| HTTP/WebSocket boundary | Authenticated commands/events and recoverable session snapshots |

These are in-process responsibilities, not a requirement for microservices. Preserve legacy API compatibility and default VLC behavior. Transfer must explicitly retire the old playback instance, preserve the selected track/position, and prevent stale completion from advancing either session. Browser feature parity remains unproven until speech, effects, transitions, normalization, controls, sleep timers, and Electron behavior are tested.

## Current modernization checkpoint — October 28, 2026

This checkpoint updates the October 26 static baseline above. It records implemented paths and remaining validation; it is not a claim of full production readiness. See [MODERNIZATION_PROGRESS.md](MODERNIZATION_PROGRESS.md) for the phase log, test checkpoints, and manual QA status.

### Current request and playback path

```mermaid
flowchart LR
    React[React UI and session hook] -->|authenticated commands| API[FastAPI playback routes]
    API --> Sessions[PlaybackSessions]
    Sessions --> Coordinator[PlaybackCoordinator]
    Coordinator --> Store[PlaybackStore: revisions, receipts, attempts]
    Coordinator --> Queue[Session QueueManager reservations]
    Sessions --> Browser[BrowserAudioEngine]
    Sessions --> VLC[Lazy VLC adapter]
    Browser -->|observed playback events| Sessions
    VLC -->|native observations| Sessions
    Browser -->|stable IDs, ranges| Media[Authenticated MediaService]
    VLC --> Media
    Media --> Library[(SQLite library and generated leases)]
```

`main.py` now installs authenticated playback and media routers, scopes legacy control requests to the selected desktop session, and serves the built frontend. The normal server binds to loopback by default; access policy is documented in [SERVER_ACCESS.md](SERVER_ACCESS.md). The React app owns a browser playback session, a browser audio engine, an output selector, and controls for session playback. Electron also receives the active session binding through its preload bridge and has a graceful backend shutdown path.

`PlaybackSessions` creates per-listener queues and mutable mood, energy, talk, announcement, and preference contexts. `SessionSettings` overlays recommendation settings such as play mode and filters per session while delegating shared profile settings to the database. Queue reservations are committed after an observed logical start; they are not consumed when a plan is merely prepared. If startup has no available pick, `ensure_available` waits briefly for a queue rebuild and can add one filtered database fallback without recording history or consuming the recommendation.

`PlaybackCoordinator` owns playback revisions, command receipts, attempts, active reservations, output grants, and transfer fences. Browser observations and VLC adapter observations enter the same session service. The service serializes controls and handoff against transport replacement, routes completion/start feedback into the queue, and persists only accepted events. Browser handoff requires a correlated quiesce acknowledgement and position; output replacement retains the logical session and rejects stale observations. Reconnect preserves the logical plan while requiring a current browser grant.

`MediaService` resolves canonical library paths and leased generated media, supports byte-range streaming, and places private media behind authenticated routes. Browser and VLC plans use the same stable track/resource identifiers. `PlaybackStore` installs an additive schema after a verified backup and persists session state, idempotency receipts, attempts, and history events. The agent exercised migration/backup behavior against disposable databases only. The user subsequently launched the real app; its current production migration state was not inspected during the final review.

### Verified and pending validation

- Final reviewed-tree verification: **450 backend tests passed, 9 skipped; 83 frontend tests passed; 4 opt-in native VLC tests passed; production build passed**. These checks do not resolve the remaining reconnect race/idempotency review findings recorded in the handoff.
- The isolated launcher serves the real app on loopback with three generated silent WAVs and a disposable database. Recommendation/model generation is deterministic and mocked, TTS/process management are disabled, writes are guarded to a random `.test-runtime/qa-*` workspace, and outbound sockets are blocked. It does not use the original music or metadata.
- Browser QA on port `18765` exposed and verified a fix for a missing-lyrics rendering crash, then exercised pause/seek, same-attempt reconnect, and separate-tab mood. Subsequent user-reported pause/seek and development WebSocket problems led to further tested fixes; live acceptance is still open. See [APP_USAGE_CHECKLIST.md](APP_USAGE_CHECKLIST.md) and the local `CODEX_HANDOFF.md` (explicitly ignored by current Git rules).
- Real-library history updates were explicitly permitted for manual checks on October 28, 2026. Automated tests and the isolated launcher remain synthetic and do not write to the production history.
- Browser controls, authenticated routes, output transfer, native callback translation, and generated-media range handling have targeted automated coverage. The current CI contract workflow runs the lean backend contract suite and frontend tests/build; it does not run the entire backend suite, VLC, Electron, TTS/GPU, or live app.

Live model recommendation/quality, generated DJ and guest speech, audible VLC/EQ/DJ effects, LAN/reverse-proxy deployment, Electron tray/media-key behavior, and operating-system sleep/lock integration remain separate manual checks. The isolated VLC output uses dummy audio and cannot establish sound quality. Do not infer these checks from synthetic tests.

### Instance boundary

Configure each runtime with its own `RADIO_DATA_DIR`, `RADIO_MUSIC_DIR`, and `RADIO_METADATA_DIR`. TTS and managed process startup can be disabled with `RADIO_TTS_ENABLED` and `RADIO_PROC_MGR_ENABLED`. Playback coordinator, output admission, and queue locks are process-local: run one API worker for a given instance. Store private backups inside that instance's data directory and keep them out of source control.

### Playback reliability update — October 28, 2026

Control connectivity and physical playback are separate. A dropped control socket
keeps the current browser media element and buffered bytes. Local pause, seek,
resume and volume use the browser engine; native controls keep their existing
server path. Existing legacy status reads retain their full response contracts.

A browser obtains a producer permit through `POST /api/playback/sessions/{id}/lease`
before decoding, renewing every three seconds. The permit lasts twelve seconds;
its expiry persists in session JSON without changing the command revision. The
client uses a conservative deadline measured from the request's monotonic start,
with an early safety margin. With WebAudio, scheduled gain also silences expired
output independently of the page timer. The HTML-only fallback relies on timers
and media callbacks: a frozen browser renderer cannot provide the same strict
audio-clock fence. OS sleep, heavy throttling and fallback behavior remain manual
acceptance checks. Server wall-clock changes can affect persisted lease expiry.

Handoff requires correlated quiescence or an expired absent producer permit.
Socket absence alone does not authorize overlap. Each document has a fresh
producer identity; session storage restores controller credentials, not producer
identity. Reloading an active page can therefore wait for its former permit to
expire before explicit recovery. Grace cleanup retains the original connection
generation and playback instance and serializes with replacement registration.

Browser observations queue in memory with fixed event IDs through transient
failures. Recovery flushes them in order before reconciling authoritative state;
obsolete progress is dropped/coalesced. The outbox covers temporary outages of
the same page, not browser crashes or loss of the page's memory. Completion waits
for server acceptance and never invents a recommendation while offline. A restart
retires old instances, retains logical attempts, and requires explicit reactivation;
the persisted old permit must first expire. Receipt retries cannot add duplicate
history. Pending handoffs poll with the original command identity and cancel or
reconcile the same operation at their deadline; abandoned native reservations are
released by server cleanup.

Replay is an idempotent in-place seek to zero on the current started song. It
keeps the attempt, item, instance, pause intent and upcoming queue. Each distinct
replay command records one replay signal; retries reuse the receipt. It does not
request recommendations, start a new listening session or add a completion.
The queue refreshes its taste cache without writing a second durable replay.
