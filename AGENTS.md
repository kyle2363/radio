# AI Radio Station

An AI-driven radio station for a personal MP3 library. Three surfaces share this repository:

- **Backend** — FastAPI service owning the library, recommendation, listening sessions, analysis, TTS, and ML.
- **Desktop** — React/Vite UI in an Electron shell, with selectable server-side VLC or browser-owned audio.
- **Android** — a native Kotlin/Compose app that plays the phone's local music independently of the server and is the active development focus.

Android playback remains local and independent. Checkpoints 1A–1C implement USB pairing, secure credential handling, catalog import/content matching, and separate Server/Matching screens. Accepted checkpoint 2A adds the local journal, feedback and Replay. Checkpoint 2B adds offline Radio, bounded service refill, local learning and Vibe lock; accepted November 3, 2026. Checkpoints 2C manual profile sync and 3A secure remote hosting are implemented, verified and phone-accepted. Checkpoint 3B automatic discovery is phone-accepted. Checkpoints 2C–3B are merged into `main` (November 7, 2026). Checkpoint 4 connected recommendations (`docs/CONNECTED_RECOMMENDATIONS_PLAN.md`) and checkpoint 4.1 phone-only connected tuning (`docs/CONNECTED_TUNING_PLAN.md`) are merged into `main`; 4.1 was phone-accepted November 7, 2026. Checkpoint 4.2 Radio mode redesign was phone-accepted, fully verified and merged into `main` November 11, 2026 (`f676e83`; `docs/RADIO_REDESIGN_PLAN.md`). Checkpoint 5 plain lyrics is merged into `main` (November 14, 2026, `5a35dad`; `docs/ANDROID_LYRICS_PLAN.md`). Checkpoint 6 visual redesign was phone-accepted November 17, 2026 and is merged. Checkpoints 9A–9C (shared feel rules, personal feels, feel sprint, this-or-that rankings, feel stations) were phone-accepted and merged into `main` November 18, 2026. 9D–9E (desktop Feels panel, feel stations, suggestions, review, feel history) are implemented on `desktop-9d-9e`. Checkpoint 7 (playlist sync, sync on open) is planned in `docs/CHECKPOINT_7_PLAN.md`; checkpoints 8–11 (listening context and stats, personal feel, polish, daily use; performance checkpoint 12 dropped) in `docs/CHECKPOINT_8_PLAN.md`. Checkpoint 13 connected talk (desktop DJ and talk show in the phone's connected Radio) is planned in `docs/CONNECTED_TALK_PLAN.md`. See `docs/ANDROID_APP_PLAN.md`, `docs/MOBILE_SYNC.md` and `docs/MOBILE_CONNECTION_PLAN.md`.

## Quick Start

### Backend

- **Venv**: `backend\venv\Scripts\python` — always use this, never system Python
- **Run**: `backend\venv\Scripts\python main.py` (from `backend/`)
- **Install deps**: `backend\venv\Scripts\pip install -r requirements.txt` (from `backend/`)
- Loopback only by default (`127.0.0.1:8765`); see `docs/SERVER_ACCESS.md`
- Process Manager auto-starts llama.cpp (chat `8081`, embed `8082`) and acestep (`8085`) when configured

### Desktop

- **Run**: `npm run dev` (from `frontend/` — Vite + Electron; Electron spawns the backend)
- **Browser only**: `http://localhost:3000`
- **Tests/build**: `npm test`, `npm run build` (from `frontend/`)

### Android

- **Build**: `.\gradlew.bat assembleDebug` (from `android/`)
- **Install**: `.\gradlew.bat installDebug`
- **Unit tests**: `.\gradlew.bat testDebugUnitTest`
- **Instrumented**: `.\gradlew.bat connectedDebugAndroidTest` (needs emulator/device)
- **Lint**: `.\gradlew.bat lintDebug`
- Independent Gradle build; not part of the Node/Python toolchain

### `.env` (repo root)

Copy `.env.example`; the backend loads it before reading env vars.

| Variable | Purpose |
|---|---|
| `LLAMA_CHAT_CMD`, `LLAMA_CHAT_WORK_DIR`, `LLAMA_CHAT_AUTO_START` | llama.cpp chat (`8081`) |
| `LLAMA_EMBED_CMD`, `LLAMA_EMBED_WORK_DIR`, `LLAMA_EMBED_AUTO_START` | llama.cpp embeddings (`8082`) |
| `ACESTEP_CMD`, `ACESTEP_WORK_DIR`, `ACESTEP_AUTO_START` | acestep (`8085`, manual) |
| `RADIO_DATA_DIR`, `RADIO_MUSIC_DIR`, `RADIO_METADATA_DIR` | Path overrides (tests rely on these) |
| `CHAT_PROVIDER`, `EMBED_PROVIDER` | `ollama`, `openai`, or `auto` |
| `RADIO_TTS_ENABLED`, `RADIO_PROC_MGR_ENABLED` | Master toggles |

Neither llama.cpp nor acestep is bundled. If you already run Ollama, select the `ollama` providers and skip the llama.cpp builds.

## Repository Workflow

- Integration target: `main`; Android checkpoints through 5 are merged (November 14, 2026). Use separate branches for subsequent work.
- Do not commit, rebase, or discard user changes without asking
- `docs/ANDROID_APP_PLAN.md` is the Android checkpoint log; this file is the stable map
- Generated media, caches, and test output stay in `.test-runtime/`
- Counts and tables here are reference values — re-verify against source

## Architecture

### Desktop and backend

The FastAPI backend owns recommendation, listening sessions, analysis, and ML on loopback. Output is selectable: server-side VLC or browser-owned audio over authenticated media streams. Electron drives the same logical session through authenticated HTTP and its IPC bridge. Changing output transfers queue, mood, DJ context, timer, and song position together.

| Component | Responsibility |
|---|---|
| `backend/playback_sessions.py` | Per-listener session: queue, mood, energy, talk, settings overlay |
| `backend/db/playback_store.py` | Transactional events, revisions, receipts, attempts |
| `backend/engine/playback_coordinator.py` | Orders output changes; fences stale observations |
| `backend/media_service.py` | Approved media with stable IDs and byte ranges |
| `backend/api_security.py` | Auth and origin policy for HTTP, WebSocket, media |
| `frontend/src/audio/BrowserAudioEngine.js` | Reports actual media events; preparing is not a play |
| `frontend/electron/main.cjs` | Desktop shell, backend spawn, tray, media keys |

One shared taste/history profile, independent playback sessions. A mode switch transfers the whole session; it never lets one client silently interrupt another.

### Android

A self-contained local player:

- Folder grants (`OpenDocumentTree`) + content URIs, never raw filesystem paths
- Recursive, read-only MP3 scan into Room; audio and tags are never modified
- A Media3 `MediaSessionService` owns the only production player
- Queues are snapshots, so later sorting or browsing cannot reorder playback
- Bookmarks restore the last song and position **paused**
- Normalization, sleep timer, playlists, and queue editing are local
- Pairing and catalog operations use the desktop server; playback remains independent. Debug USB forwarding targets loopback port `18766`; release connections require HTTPS. Direct LAN hosting is deferred.

### Shared contracts (server ↔ Android)

Catalog/matching, local offline Radio, manual profile sync, connected recommendations and connected tuning are implemented and merged into `main`:

- `docs/PORTABLE_CATALOG.md` — immutable revisions, provenance-aware embeddings, validation
- `docs/PORTABLE_SYNC_API.md` — device tokens, manual event sync, retry/conflict rules
- `backend/portable/{catalog,matching,recommender}.py` — reference algorithms
- `backend/tests/fixtures/portable_golden.json` — recommendation parity data

## Safe Verification

- **Python**: `backend\venv\Scripts\python -B scripts/run_tests.py -q` from the repo root. Only `backend/tests` is collected; the `test_*.py` scripts in `scripts/maintenance/` are not tests.
- **Frontend**: `npm test` and `npm run build` from `frontend/`.
- **Android**: unit tests, `connectedDebugAndroidTest`, and `lintDebug` from `android/`.
- Fixtures, caches, and generated audio stay in `.test-runtime/` (Android assets in `.test-runtime/android-fixtures/`).
- Never write to `audio_files/`, `docs/understand/`, production `data/radio.db`, or `data/chroma/` from automated checks.
- Isolated desktop launcher: `backend\venv\Scripts\python -B scripts/run_isolated_app.py --port 18765` (synthetic audio, disposable workspace).
- Android fixture generation: `scripts/generate_android_test_audio.py`, `scripts/generate_android_loudness_fixtures.py --ffmpeg PATH`.
- Listening-history updates are permitted during manual checks. That does not authorize destructive library operations or overwriting audio/metadata.
- Use Red/Green TDD for new features: failing test first, then implement.

## Tech Stack

| Layer | Tool |
|---|---|
| Desktop UI | Vite + React 18 + Tailwind CSS v4 |
| Desktop shell | Electron (tray, mini player, widget, media keys) |
| Backend | FastAPI + uvicorn, loopback `127.0.0.1:8765` |
| Desktop audio | python-vlc (media list, gapless chaining) + browser audio engine |
| Android UI | Kotlin 2.2.10, Jetpack Compose (BOM 2026.02.01), Material 3 |
| Android audio | Media3 ExoPlayer/Session 1.11.1 |
| Android storage | Room 2.8.5 (schema v11), SharedPreferences for shell prefs |
| Android build | AGP 9.5.1, KSP 2.3.12, minSdk 35, compile/target 37, Java 11 |
| Audio analysis | librosa + mutagen (library-wide normalization) |
| LLM chat | Ollama or OpenAI-compatible, auto-fallback |
| Embeddings | Ollama or OpenAI-compatible (`qwen3-embedding:0.6b`) |
| Audio embeddings | MuQ-MuLan (optional, 512-dim, GPU) |
| Vector DB | ChromaDB (`data/chroma/`) |
| Relational DB | SQLite (`data/radio.db`) |
| Clustering | scikit-learn KMeans (28 clusters) |
| TTS | OmniVoice (GPU voice cloning) + Piper (CPU fallback) |
| Lyrics timing | WhisperX forced alignment via vocal separation |

## Directory Map

```
radio/
├── android/                  # Independent Gradle build (dev.radio.mobile)
│   ├── app/src/main/java/dev/radio/mobile/
│   │   ├── MainActivity.kt   # Entry activity, notification intent
│   │   ├── connection/       # Pairing, discovery, API client, endpoint and signature policy
│   │   ├── data/             # Room v11, repositories, journal/sync, lyrics cache, playlists, snapshots
│   │   ├── music/            # Scanner, ordering, BS.1770 loudness
│   │   ├── playback/         # Media3 service, controller, normalization, sleep
│   │   ├── portable/         # Catalog import/validation, matching, sync policy
│   │   ├── recommendation/   # Offline recommender, connected Radio source, station policy, tuning
│   │   └── ui/               # Compose screens, queue, player, settings, theme
│   ├── app/src/test/         # JVM unit tests
│   ├── app/src/androidTest/  # Instrumented tests + fixture providers
│   └── app/schemas/          # Exported Room schemas v1–v11
│
├── backend/
│   ├── main.py               # FastAPI entry (~4,800 lines, ~156 routes + 1 WS)
│   ├── config.py             # Paths, models, timeouts, providers
│   ├── playback_sessions.py  # Per-listener session context
│   ├── playback_api.py       # /api/playback/sessions routes
│   ├── engine/playback_coordinator.py  # Output changes and fencing
│   ├── media_service.py      # Authorized streaming (range/lease)
│   ├── api_security.py       # Auth + origin/host policy
│   ├── server_settings.py    # DB-backed settings overlay
│   ├── frontend_hosting.py   # Serves the built frontend
│   ├── portable*.py          # Portable catalog/sync/media API
│   ├── portable/             # catalog.py, matching.py, recommender.py
│   ├── db/                   # schema.sql, database.py, chroma.py, playback_store.py
│   ├── ingest/               # 7-phase sequential pipeline
│   ├── engine/               # Queue, taste, retrieval, ranking, playback, effects
│   ├── announcer/            # TTS engines, personas, announcements, talk
│   ├── commands/parser.py    # NL command parser
│   ├── llm/                  # Provider registry + MuQ engine
│   └── tests/                # 77 pytest modules (only default test target)
│
├── frontend/
│   ├── electron/             # main, preloads, widget, backend lifecycle
│   ├── src/components/       # 53 components
│   ├── src/hooks/            # 11 hooks
│   ├── src/audio/            # BrowserAudioEngine
│   ├── src/api/index.js      # HTTP + WebSocket client
│   └── tests/                # Node test suites
│
├── scripts/                  # run_tests, run_isolated_app, fixture generators
│   └── maintenance/          # One-off lyrics/acestep/DB scripts (operational, not tests)
├── docs/                     # Architecture, Android plan, portable contracts
├── demo/                     # Standalone GitHub Pages demo (deployed by demo-pages.yml)
├── firebase/                 # Firestore rules template and emulator tests for discovery
├── audio_files/              # Original MP3 library (read-only)
├── data/                     # Runtime DB/chroma/caches/logs (gitignored)
└── .test-runtime/            # Disposable test output (gitignored)
```

## Desktop Subsystems

### Ingest pipeline (7 phases)

`raw → analyzed → normalized → described → embedded → audio_embedded → clustered`

| Phase | Module | Work |
|---|---|---|
| scanner | `ingest/scanner.py` | Walk library, path-hash dedup, register as `raw` |
| tags | `ingest/tags.py` | mutagen ID3, filename parsing, art → `data/art/` |
| audio | `ingest/audio.py` | librosa features, library percentile normalization → energy/valence 0–1 |
| described | `metadata.py`, `llm_tags.py` | acestep JSON (`caption` → `ai_description`, lyrics, BPM) + LLM tags |
| embedded | `ingest/embedder.py` | Text embedding → Chroma `songs` |
| audio_embedded | `ingest/embedder.py` | MuQ 512-dim → Chroma `songs_audio` when available |
| clustered | `ingest/clusterer.py` | KMeans 28, 85% text + 15% audio features |

Sequential by design; resume with `WHERE ingest_phase != 'clustered'`. Re-run clustering after ~50 new songs. Export the portable catalog only after ingest settles.

### Recommendation engine

`MemorySystem → Retriever → SlotAllocator → LLMReranker → TransitionScorer → QueueManager`

| Module | Role |
|---|---|
| `engine/taste.py` | 4-layer memory (immediate/session/daily/longterm), exponential decay |
| `engine/retriever.py` | Chroma top-60 (taste + mood + vibe lock + jitter), then SQLite filters |
| `engine/slots.py` | 2 exploit / 1 explore / 1 wildcard / 1 context; vibe lock → 4/1/0/0 |
| `engine/reranker.py` | One JSON call (`{"picks":[...]}`), deterministic fallback |
| `engine/transitions.py` | Rolling pair score: `0.8*old + 0.2*success` |
| `engine/queue.py` | Condition-protected queue; rebuild generation counters |
| `engine/constellations.py` | Up to 8 taste pockets; 20% hop chance, +0.15 boost, 5-min cache, after 50+ signals |

### Mood, energy and recovery

| Module | Behavior |
|---|---|
| `engine/mood.py` | 6 states (chill/neutral/energetic/focus/melancholy/discovery) with energy/valence ranges and centroids; auto-shifts on 3 skips or <35% completion |
| `engine/energy.py` | Last 5 songs; rising/falling/stable; target = mood midpoint + time bias, max 0.25 delta |
| `engine/recovery.py` | `recovery_score > 0.75`, not played in 30 min; `(completes/(completes+skips))*log(plays+1)`; fallback top 5% by `avg_completion` |
| vibe lock | Song embedding for 30 min / 10 songs, ~80% retrieval weight, persisted |

### Playback and speech

`engine/player.py` wraps VLC media-list playback for gapless `play_chain([TTS_WAV, song])`. The coordinator and store decide what counts as an actual start; browser and native observations enter the same session service. `MediaService` issues stable IDs and leases so preloaded media is not cleaned up mid-playback.

| Concern | Owner |
|---|---|
| Logical queue, mood, energy, settings | `playback_sessions.py` |
| Receipts, revisions, attempts, reservations | `playback_coordinator.py` |
| Durable events and history | `db/playback_store.py` |
| Grants, quiesce, position handoff | `playback_coordinator.py` |
| Media paths, ranges, leases | `media_service.py` |

A plan is not consumed until a logical start is observed. Empty-queue startup may add one filtered fallback without recording history. Stale observations from a replaced output are rejected by revision fences.

TTS is OmniVoice (GPU) with Piper (CPU). Announcements cover mood change, skip streak ≥3, vibe lock, wildcard, recovery, and a periodic interval. Talk segments (caller, monologue, guest) are disabled by default. Generated speech is never song history.

### Other desktop services

- **Process manager** (`engine/process_manager.py`): spawns/health-checks llama.cpp chat (`8081`), embeddings (`8082`), acestep (`8085`, manual); logs to `data/logs/`; CTRL_BREAK then forced kill.
- **Library verification** (`/api/library/verify`): orphaned rows, unregistered MP3s, rapidfuzz auto-match, manual confirm/add/delete. Deletes database rows only, never audio.
- **Lyrics timing**: WhisperX word timestamps in `data/lyrics_timing/`; `audio_separator.py` isolates vocals first; served at `/api/songs/{id}/lyrics-timing`.
- **Maintenance scripts** (operational, not tests) live in `scripts/maintenance/` and find the repo root themselves, so they run from any directory: `batch_understand.py`, `batch_fill_lyrics.py`, `batch_fill_genius.py`, `batch_scrape_lyrics.py`, `whisper_lyrics.py`, `batch_lyrics_timing*.py`, `reembed_all.py`, `lyrics_alignment_audit.py`, `run_ingest_phase.py` and the legacy `test_*.py` database helpers. The ingest pipeline's lyrics-timing phase imports `batch_lyrics_timing_smart.py` from there.

### WebSocket events

`/ws/events` broadcasts JSON notifications over an `asyncio.Queue`; failed clients are dropped.

| Event | When |
|---|---|
| `song_started`, `song_ended`, `song_skipped` | Transport lifecycle |
| `song_hearted`, `song_thumbed_down`, `song_replayed` | Feedback |
| `song_blacklisted`, `song_undo` | Blacklist / undo |
| `queue_updated`, `queue_rebuilding` | Queue changes |
| `mood_changed`, `vibe_lock_activated`, `vibe_lock_deactivated` | Mood and vibe lock |
| `play_mode_changed`, `folder_filter_changed` | Mode and filters |
| `taste_reseeded`, `error` | Reseed and failures |

Events are notifications, not a durable replay log; recovery uses authoritative snapshots.

### Desktop frontend

53 components and 11 hooks. The desktop UI is used through the Electron window only.

- **Player**: `NowPlaying`, `WaveformViz`, `PlayerControls`, `VolumeControl`, `PlaybackOutputSelector`, `MiniPlayer`
- **Queue/recs**: `QueuePreview`, `QueueContextMenu`, `ExplorationSlider`, `MoodButtons`
- **Library**: `SongList`, `FiltersPanel`, `QuickSearch`, `CollectionsList`, `CollectionDetail`, `IngestPanel`, `LibraryVerify`, `LyricsPanel`
- **Analytics**: `DailyReport`, `ListeningStats`, `StatsDashboard`, `SessionHistory`, `MoodTimeline`, `PriorSongs`, `ConstellationMap`
- **Shell/status**: `ArchetypeBadge`, `VibeLockBadge`, `SkipStreakIndicator`, `AmbientGlow`, `AuthenticationGate`, `HeaderButton`, `HeaderStatusPill`, `BrandIcons`
- **Mobile/server**: `MobilePairingSettings`, `MobileCatalogSettings`, `RemoteHostingSettings`
- **Utility**: `CommandBar`, `TooltipLayer`, `Onboarding`, `BootScreen`, `Toast`, `ParticleBurst`, `Skeleton`, `ShortcutsModal`, `SettingsContent`, `SkipFeedback`, `ErrorBoundary`, `CommandPalette`, `SleepTimer`, `TalkSettings`, `CalibrationPanel`, `BucketAnchor`, `WikiPage`

Hooks: `useRadioState`, `usePlaybackState`, `usePlaybackSession`, `useQueueState`, `useMoodState`, `useUIState`, `usePoll`, `useTransition`, `useBootPoll`, `useFocusTrap`, `useDismissTimer`.

Electron: `main.cjs` spawns/owns the backend and tray; `preload.cjs`/`preload-widget.cjs` expose IPC; `backend-session.cjs` tracks session binding; `backend-shutdown.cjs` stops gracefully; `window-startup.cjs` sequences startup; `widget.html` is the overlay.

### Startup and shutdown

Backend startup is not read-only: it migrates SQLite, closes stale sessions, prunes repulsion rows, clears generated caches (including TTS), initializes VLC and the queue, probes speech, starts the broadcast worker, and may start managed processes. Never use a normal app start as a baseline or fixture.

Electron spawns the backend, prefers the project venv, requests graceful shutdown, then force-kills the tree. Keep one owner of the player.

## Android App

### Build and toolchain

Standalone Gradle project (`dev.radio.mobile`): `minSdk 35`, `compileSdk`/`targetSdk 37`, Java 11, Compose, KSP Room schema export. Tests read fixtures from `../.test-runtime/android-fixtures`. Add dependencies only when a phase needs them.

The manifest includes `INTERNET`, foreground media playback, and wake lock permissions. Debug USB pairing permits only the loopback-forwarded HTTP endpoint at `127.0.0.1:18766`; release networking requires HTTPS. Direct LAN hosting is deferred.

### Packages and tests

| Package | Responsibility |
|---|---|
| `connection/` | `PairingRepository`, `DiscoveryRepository`, `RadioApiClient`, `PairedServerStore`, `ConnectionSecurity` (endpoint policy, signed descriptors) |
| `data/` | Room v11, `LibraryRepository`, `ListeningJournalRepository`, `PortableSyncRepository`, `LyricsRepository`, `LoudnessRepository`, `PlaybackSnapshotStore`, `Playlists` |
| `portable/` | `CatalogRepository`, `CatalogValidator`, `MatchingRepository`, `ConservativeMatcher`, `PortableSyncPolicy` |
| `recommendation/` | `OfflineRecommender`, `RadioRepository`, `ConnectedRadioSource`, `ConnectedPolicy`, `ConnectedTuning`, `StationPolicy`, `SharedRadioProfile` |
| `music/` | `LocalMusicScanner`, `LibraryOrdering`, `LoudnessMeter`, `LocalLoudnessAnalyzer` |
| `playback/` | `RadioPlaybackService`, `LocalPlaybackController`, `InitializingPlaybackController`, `ServicePlaybackController`, `NormalizationAudioProcessor`, `SleepTimer` |
| `ui/` | `RadioApp`, `RadioContent`, `StationUi`, `ConnectedRadioUi`, `LibraryContent`, `IntegratedQueue`, `PlayerPresentation`, `LyricsSheet`, `PlaylistBrowser`, `ServerSettings`, `MatchingReviewScreen`, `NormalizationSettings`, `SleepTimerDialog`, theme |

| Layer | Modules |
|---|---|
| JVM unit (23 classes) | Queue, snapshot, sleep deadline, loudness meter, ordering, backdrop policy, catalog validation/transfers/vectors, connection security, discovery documents, journal identity, listening attempts, lyrics client/contract, offline recommender, connected policy/source/client, Radio selection, station policy, example |
| Instrumented (39 classes) | Shell, playback UI, restoration, snapshot store, background and media playback, local library, library UI, migration, playlists, loudness, catalog import, matching, pairing, discovery, endpoint updates, server settings, journal and feedback, profile sync, Radio service/queue/station/UI/redesign, connected Radio, lyrics cache/repository/UI, example + fixtures and fixture providers |

Migration tests replay every exported schema and assert library, playlists, history, and bookmark survive. Keep `exportSchema = true` and commit new schema JSON.

### Local library

- One persisted folder grant plus cached folders/songs; revoked access is distinct from an empty library
- Recursive, read-only scans; same-named recordings keep distinct identities
- Filename-derived labels until server matching exists
- Sort: modified (newest default), oldest, first seen, title, filename, duration; remembered per view
- Explicit migrations `v1→…→v10→v11`; no destructive fallback

### Playback service

`RadioPlaybackService` is a non-exported `MediaSessionService` owning the only player. The activity binds locally (no catalog IPC). Media3 provides the notification, lock-screen controls, audio focus, and noisy-output handling.

- System controllers can play/pause/seek/shuffle but cannot replace the queue with arbitrary URIs or change repeat/speed
- Only an actual attempt records a playback start; preparing, restoring, seeking, and paused navigation do not
- Restoration installs the timeline paused, with natural order and exact shuffle permutation independent
- Queue edits act on the real Media3 timeline; emptying the queue clears the bookmark

### Queue and shuffle

Queues snapshot the opened list (folder, all songs, search, playlist). Each shuffle activation creates a fresh permutation with the current song first and avoids repeating the previous order. Disabling shuffle restores natural order. Toggling or editing never restarts or seeks the current song.

### Offline volume normalization

Optional, default off. Attenuates toward −14 LUFS (Spotify’s reference); quiet, silent, unknown, or invalid files keep original gain — never boosts. Read-only MediaExtractor/MediaCodec measurement with BS.1770 K-weighting, 400 ms blocks, absolute/relative gating. Cached in Room with a URI/size/modified fingerprint; stale results ignored. Analysis is explicit, foreground-only, runs up to 4 decoders in parallel, and pauses off-screen.

### Sleep timer

Presets 15/30/45/60, custom 1–240 minutes, optional 10-second fade, replace/cancel, and stop-at-end-of-song. Elapsed-time countdown continues while paused/backgrounded. Timers belong to the live service, are not restored after process loss, and never start the next song.

### UI and appearance

Navigation: Radio, Library, Settings, with a persistent mini-player. Opening screen is configurable (Last Screen default). Ocean Blue and Deep Teal palettes with system/light/dark override. The optional Now Playing backdrop is Off/Static/Animated and animates only during foreground playback. Library rows are compact (~8 per screen); search is a separate screen; the queue is a view inside Now Playing sharing the transport. Minimal mode keeps the same session.

| Palette | Light accent | Light container | Dark background |
|---|---|---|---|
| Ocean Blue | `#2563EB` | `#DBEAFE` | `#1E293B` |
| Deep Teal | `#0F766E` | `#CFEDEA` | `#0D2426` |

Palette, appearance, opening screen, minimal mode, backdrop, sort, and last screen persist in `radio_shell` SharedPreferences. Dynamic wallpaper color is disabled for consistent palette comparison.

### Status

Implemented: local scan/playback, background/lock-screen control, paused restoration, playlists/queue editing, sleep timer, normalization, themes, USB pairing, catalog import/matching, actual-start journal, feedback/Replay, offline Radio with persisted learning/tuning/Vibe lock and bounded refill, explicit manual profile sync and secure remote hosting. Sync preserves independent active playback. Checkpoints through 3B automatic discovery are phone-accepted.

Checkpoint 4 connected recommendations and 4.1 connected tuning are merged into `main`; checkpoint 4.2 desktop-first refill and Radio mode redesign are phone-accepted and merged November 11, 2026.

Checkpoint 5 adds plain lyrics from prepared desktop catalogs and a private offline cache; merged into `main` November 14, 2026. Checkpoint 6 visual redesign is phone-accepted. Not implemented: automatic sync, synchronized lyrics, statistics, speech, crossfade, and downloads.

| Increment | Deliverable | State |
|---|---|---|
| 0–1C | Shell, local library, USB pairing, catalog import/matching and review | Accepted; phone-tested per plan |
| 2A | Local journal, like/dislike feedback, Replay | Accepted November 1 |
| 2B | Offline Radio, persisted local learning/tuning, Vibe lock, bounded service refill | Accepted November 3, 2026 |
| 2C | Manual profile sync | Accepted November 4, 2026 |
| 3A | Secure remote hosting | Accepted November 4, 2026 |
| 3B | Automatic discovery | Phone-accepted November 4, 2026; merged into main |
| 4 | Connected recommendations | Merged into main November 7, 2026 |
| 4.1 | Connected tuning (phone-only overrides in Radio → Tune) | Phone-accepted and merged November 7, 2026 |
| 4.2 | Radio mode redesign (station surface, End/New mix/Not for me, missing states) | Phone-accepted, fully verified and merged November 11, 2026 |
| 5 | Plain lyrics with a private offline cache | Merged into main November 14, 2026 |
| 6 | Visual redesign (mood colours, shapes, tab pager) | Phone-accepted November 17, 2026; merged |
| 7 | Playlist sync, sync on open | Planned (`docs/CHECKPOINT_7_PLAN.md`) |
| 9A–9C | Shared feel rules, personal feels, sprint, this-or-that, feel stations | Phone-accepted and merged November 18, 2026 |
| 9D–9E | Desktop Feels panel, feel stations, suggestions, review, feel history | Implemented on `desktop-9d-9e` |
| 10–11 | Animation polish (shared-element player, audio-reactive backdrop, predictive back, springs, tuning dial); daily use (empty-queue Play, swipe rows, QS tile, shortcuts, typo search, Recently heard, finish-queue sleep) | Implemented on `android-10-11`; awaiting phone test |
| 8C | Fun stats (Wrapped cards, milestones, taste map, extra measures) | Implemented on `android-10-11`; awaiting phone test |
| 13 | Connected talk (desktop announcements and talk show in connected Radio) | Planned (`docs/CONNECTED_TALK_PLAN.md`) |

At each checkpoint: installable build, verification summary and limitations, and a short phone test list. A successful build is not approval to start the next increment.

## API Surface (grouped)

`backend/main.py` has ~156 routes plus `/ws/events`; playback, media, and portable routers add more.

| Prefix | Routes | Purpose |
|---|---|---|
| `/api/songs` | 23 | List/detail/art/lyrics timing/similar/metadata, blacklist, tags, delete |
| `/api/queue` | 18 | Contents, history, pop/peek, skip, replay, undo, reorder |
| `/api/play` | 15 | Status, transport, volume, EQ, mode, heart, thumbs-down, stop |
| `/api/ingest` | 12 | Status, progress, scan, run, per-phase, reprocess, retries |
| `/api/collections` | 8 | CRUD, membership, reorder |
| `/api/mood`, `/api/settings`, `/api/filter`, `/api/talk`, `/api/admin` | 6 each | Mood/vibe lock, settings, filters, talk show, admin |
| `/api/library` | 5 | Verify, auto-match, confirm, add, delete orphans |
| `/api/processes`, `/api/tts`, `/api/onboarding` | 4 each | Subprocesses, speech, cold-start seeding |
| `/api/taste`, `/api/sleep`, `/api/chroma`, `/api/dj-effects` | 3 each | Constellations, timer, health/dedup, effects |
| `/api/playback/sessions` | 4 | Create session, desktop claim, snapshot, queue |
| `/api/media` | 2 | Authenticated track/resource streaming (ranges) |
| `/api/calibration`, `/api/sessions`, `/api/stats` | 2 each | Calibration, sessions, statistics |
| `/api/portable/v1` | 11 | Capabilities, catalog, vectors, events, profile, devices, availability, contexts, recommendations |

Single-route groups: `/api/recommend`, `/api/archetype`, `/api/announcement`, `/api/command`, `/api/mode`, `/api/clusters`, `/api/tags`, `/api/search`, `/api/energy`, `/api/blacklist`, `/api/recovery`, `/api/embed`, `/api/llm`, `/api/health`, `/api/shutdown`, `/api/lyrics-timing`.

## Databases

### Desktop SQLite (`data/radio.db`)

13 base tables: `songs`, `play_events`, `sessions`, `transitions`, `blacklist`, `queue_history`, `settings`, `skip_feedback`, `collections`, `collection_songs`, `sleep_timer`, `mood_skip_repulsion`, `song_suppression`. `playback_store.py` adds a playback schema (sessions, receipts, attempts, events) after a verified backup.

`songs` carries filepath, path hash, tags, BPM, energy/valence, cluster, description, lyrics, tag sets, counters, `avg_completion`, `recovery_score`, `ingest_phase`, art, `gain_db`, and DJ-effect eligibility. `settings` seeds 69 keys.

### Android Room (schema v11)

| Entity | Purpose |
|---|---|
| `LibraryRoot`, `LocalFolder` | Persisted grant and cached tree |
| `LocalSong` | Stable identity, URI, name, dates, duration, availability |
| `LocalPlaybackStart` | Actual-start journal, deduplicated by attempt |
| `LocalPlaybackSnapshot` | Atomic paused-restoration bookmark |
| `LocalPlaylist`, `LocalPlaylistSong` | Ordered, unique membership |
| `LocalLoudness` | Normalization measurements by song identity |
| `LocalListeningEvent`, `LocalEventOutbox`, `LocalJournalClock`, `LocalSongFeedback` | Immutable local events, durable ACK state/provenance, Lamport clock and feedback feed local Radio and explicit manual sync |
| `PortableSyncCursor`, `PortableRemoteEvent`, `PortableProfileRegister`, `PortableSyncOwner` | Per-server/device download progress, remote ledger, winning shared values and installation ownership |
| `PortableCatalogRevision`, `PortableCatalogSong`, `PortableCatalogVector`, `PortableCatalogActive` | Imported server catalog metadata, vectors, and active revision |
| `LocalContentHash`, `PortableMatchDecision` | Local content identity and saved matching decisions |
| `LocalLyricsCache` | Bounded private plain lyrics keyed to matched local content, pairing ownership and immutable catalog revision |

## Config (`backend/config.py`)

| Setting | Value | Notes |
|---|---|---|
| `API_PORT` | 8765 | Loopback default |
| `N_CLUSTERS` | 28 | KMeans clusters |
| `MUSIC_DIR` | `BASE_DIR/audio_files` | Read-only originals |
| `OLLAMA_BASE_URL` | `http://192.168.1.20:5002` | External Ollama |
| `CHAT_PROVIDER` / `EMBED_PROVIDER` | `openai` | `ollama` / `openai` / `auto` |
| `OPENAI_BASE_URL_CHAT` / `_EMBED` | `127.0.0.1:8081` / `:8082` | llama.cpp servers |
| `MUQ_ENABLED` / `MUQ_AUDIO_BLEND` | `true` / `0.5` | Audio embeddings and blend |
| `TTS_ENABLED` | `true` | Master speech toggle |
| `CONSTELLATION_MIN_SIGNALS` | 50 | Activates constellations |
| `LLM_CHAT_TIMEOUT` / `LYRICS_EMBED_WORDS` | 45s / 150 | Timeouts and embedding text |
| `PROC_MGR_ENABLED` | `true` | Subprocess management |
| `VLC_SUPPRESS_STDERR` | `false` | Hide VLC init noise |

Notable `settings` rows: `exploration_level` (0.3), `current_mode` (radio), `llm_reranking_enabled` (true), `archetype` (mood_listener), `vibe_lock_*`, `play_mode` (ai), `theme` (dark), `waveform_mode` (orbit), `tts_engine` (omnivoice), `talk_segments_enabled` (false), `talk_frequency` (4), `dj_effect_enabled`/`_preset`, `folder_filter`, `jitter_strength` (0.12), `recency_exclude_hours` (1).

Prefer a new seed row over hardcoding a value in application code.

## Key Design Decisions

- **Android playback is server-independent.** Never add a network requirement to local play.
- **Actual starts only.** Preparing, preloading, restoring, and seeking are not plays on either platform.
- **Room migrations are explicit and additive.** No destructive fallback.
- **Queues are snapshots.** Later browsing or sorting must not reorder active playback.
- **Android never writes source audio or tags.**
- **Ingest is sequential.** No parallel LLM calls; resume at any phase.
- **acestep JSONs are primary metadata.** `ai_description` comes from `caption`; the LLM only tags.
- **Vibe lock changes slot allocation** to 4/1/0/0.
- **Settings live in the database**, not hardcoded app layers.
- **One shared profile, independent sessions.** Transfer moves the whole session; queues never merge.
- **Portable export runs after ingest.** SQLite and Chroma are separate stores.
- **Android normalization attenuates only.**

## Gotchas

- **No reliable ID3 tags** on the desktop library — filenames are primary.
- **No album art** for most MP3s; waveform/placeholder visuals are the default.
- **VLC is required** for native desktop output; browser audio works without it.
- **`torch`, `muq`, `omnivoice` are commented out** in `requirements.txt`.
- **`scripts/maintenance/test_*.py` scripts are not tests** — several write to the production database when run.
- **Never start the production app as a test** — startup mutates the DB and clears caches.
- **Emulator tests are not phone tests.** Bluetooth, battery, and ergonomics need the device.
- **Media3 1.11.1 is verified**; re-check behavior before upgrading.
- **`artist`/`title` may be `None`** — use `or ""` in prompts.
- **TTS cache is cleared on backend startup.**
- **`queue_rebuilding` is emitted by QueueManager**, not `main.py`'s helper.
- **Preview watchdog** must not bump the rebuild generation while talk is pending.
- **Talk counter advances** only from `on_song_started` rebuilds.
- **Folder filter** parsing needs `.strip()` guards and `try/except`.

## Where to Change What

| Task | Start here |
|---|---|
| Android playback/queue/shuffle/restore | `playback/LocalPlaybackController.kt` |
| Android library or identity | `music/LocalMusicScanner.kt`, `data/LibraryRepository.kt` |
| Android schema | `data/LibraryDatabase.kt` + migration + exported schema |
| Android normalization | `music/LoudnessMeter.kt`, `playback/NormalizationAudioProcessor.kt` |
| Android screens | `ui/RadioApp.kt`, `ui/LibraryContent.kt`, `ui/PlayerPresentation.kt` |
| Desktop recommendation | `engine/taste.py`, `retriever.py`, `slots.py`, `reranker.py` |
| Desktop playback/output | `playback_sessions.py`, `engine/playback_coordinator.py`, `media_service.py` |
| Desktop UI | `frontend/src/hooks/useRadioState.jsx`, `frontend/src/api/index.js` |
| Ingest / speech | `backend/ingest/*`, `backend/announcer/*` |
| Portable sync | `backend/portable*`, `backend/portable/*` |

## Docs Index

| Doc | Use |
|---|---|
| `docs/ANDROID_APP_PLAN.md` | Android requirements, increments, checkpoints |
| `docs/MOBILE_CONNECTION_PLAN.md` | Current mobile connection phase map and future sync/recommendation design |
| `docs/MOBILE_SYNC.md` | Manual sync permissions, workflow, recovery and phone checks |
| `docs/MOBILE_PAIRING.md` | USB pairing workflow |
| `docs/REMOTE_HOSTING.md` | Secure remote hosting over the Quick Tunnel |
| `docs/AUTOMATIC_DISCOVERY.md` | Firebase endpoint discovery setup and checks |
| `docs/CONNECTED_RECOMMENDATIONS_PLAN.md` | Checkpoint 4 connected recommendations |
| `docs/CONNECTED_TUNING_PLAN.md` | Checkpoint 4.1 phone-only tuning |
| `docs/ANDROID_LYRICS_PLAN.md` | Checkpoint 5 plain lyrics |
| `docs/CHECKPOINT_7_PLAN.md` | Checkpoint 7 playlist sync and sync on open |
| `docs/CHECKPOINT_8_PLAN.md` | Checkpoints 8–11 stats, personal feel and ranking, polish, daily use |
| `docs/CONNECTED_TALK_PLAN.md` | Checkpoint 13 connected talk (planned) |
| `docs/MUSIC_MATCHING.md` | Phone-to-catalog content matching |
| `docs/PORTABLE_MEDIA_PLAN.md` | Portable media design |
| `docs/README.md` | Documentation index and ownership |
| `docs/ARCHITECTURE.md` | Desktop boundaries and session model |
| `docs/archive/MODERNIZATION_PROGRESS.md` | Desktop phase log and gaps |
| `docs/SERVER_ACCESS.md` | Binding and access policy |
| `docs/APP_USAGE_CHECKLIST.md` | Manual desktop walkthrough |
| `docs/TESTING.md` | Test isolation and commands |
| `docs/PORTABLE_CATALOG.md` | Catalog format and validation |
| `docs/PORTABLE_SYNC_API.md` | Device auth, events, retries |
| `docs/OFFLINE_RECOMMENDATIONS.md` | Offline recommender design |
| `docs/archive/PRE_ANDROID_HANDOFF.md` | Pre-Android contract summary |
| `docs/archive/BASELINE.md` | Historical static audit |
| `docs/archive/LOCAL_HANDOFF.md` | Historical local handoff (gitignored); active follow-ups in TESTING.md |
