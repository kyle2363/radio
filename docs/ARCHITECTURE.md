# Radio architecture

Current repository map, November 1, 2026. The active branch is `android-app`, based on `stability-server-architecture`. This describes implemented boundaries; acceptance status belongs in [the Android checkpoint log](ANDROID_APP_PLAN.md) and [testing guide](TESTING.md).

## Three application surfaces

| Surface | Code | Playback owner |
| --- | --- | --- |
| Android | `android/` — standalone Kotlin/Compose Gradle project | One local Media3 `MediaSessionService` |
| Desktop | `frontend/` — React/Vite and Electron | Browser audio or server VLC, selected per session |
| Backend | `backend/` — FastAPI | Library, recommendations, desktop sessions, analysis, speech and portable APIs |

Android playback is local and independent of server availability. Desktop clients share a taste/history profile but have independent playback sessions. Output transfer moves the whole desktop session; it never merges queues or takes another listener's output silently.

## Desktop sessions and media

| Component | Authority |
| --- | --- |
| `backend/playback_sessions.py` | Per-listener queue, mood, energy, talk and settings overlay |
| `backend/engine/playback_coordinator.py` | Ordered commands, receipts, attempts, output grants and revision fences |
| `backend/db/playback_store.py` | Durable session state, idempotency receipts and accepted events |
| `backend/media_service.py` | Authorized stable media IDs, byte ranges and generated-resource leases |
| `frontend/src/audio/BrowserAudioEngine.js` | Browser output and observed transport events |
| `backend/engine/player.py` | VLC adapter and native observations |
| `backend/api_security.py` | HTTP, WebSocket and media authentication/origin policy |

Preparing a plan is not a play. An accepted actual logical start commits its reservation and records history. Browser and native observations enter the same session service; stale output observations are fenced. Handoff preserves queue, mood, DJ context, timer and song position. Replay seeks the started song to zero while retaining its attempt and upcoming queue; receipt retries do not duplicate the replay signal.

Browser control disconnection does not immediately stop buffered audio. Producer permits bound output admission; same-page observations retry with stable IDs. The observation outbox is in memory, so page loss can lose unsent events. Recovery reconciles authoritative snapshots. WebAudio provides an audio-clock expiry fence; HTML-only fallback and OS sleep/throttling still need manual acceptance. WebSocket events are notifications, not a durable event log.

## Recommendation, ingest and speech

Desktop recommendation flows through taste memory, retrieval, slot allocation, reranking, transitions and QueueManager. Shared profile settings live in SQLite, with per-session overlays where applicable. Speech is generated media and never song listening history.

Ingest is sequential: raw → analyzed → normalized → described → embedded → audio_embedded → clustered. Acestep captions are primary descriptive metadata; the LLM supplies tags. Export portable revisions after ingest settles. SQLite and Chroma do not share a transaction; catalog preparation is explicit and mobile reads never initiate export, ingest or models.

Optional Ollama/OpenAI-compatible services, MuQ, OmniVoice and Piper support recommendation, analysis and speech. The process manager owns configured subprocesses. Missing optional services must not impose a server dependency on Android playback.

## Android local state

Folder grants and content URIs feed a read-only recursive scan into Room. One Media3 service owns the production player, background controls and local queue. Queues snapshot the selected list. Bookmarks restore paused; preparation, restoration, seeking and paused navigation do not count as starts. Explicit additive Room migrations preserve existing state. Normalization attenuates only and never changes source audio or tags.

Local playlists, sleep timer, journal, feedback and Replay are implemented. Offline Radio checkpoint 2B adds persisted learning, tuning, Vibe lock and bounded refill through that same player; phone review remains pending. Manual profile sync is not implemented. See [ANDROID_APP_PLAN.md](ANDROID_APP_PLAN.md) for checkpoint evidence and the proposed cold-start taste bootstrap.

## Server and phone contracts

Debug USB pairing uses the explicitly enabled loopback gateway on port `18766`; release connections require HTTPS. Direct LAN hosting is deferred. Pairing, prepared catalog import, local hashing and matching are implemented. Future sync and speech contracts do not imply enabled Android features.

- [Pairing and import workflow](MOBILE_PAIRING.md)
- [Catalog contract](PORTABLE_CATALOG.md), [matching contract](MUSIC_MATCHING.md), [offline scoring](OFFLINE_RECOMMENDATIONS.md)
- [Portable sync API](PORTABLE_SYNC_API.md) and [optional connected speech](PORTABLE_MEDIA_PLAN.md)
- [Future connection design and historical Phase 0 audit](MOBILE_CONNECTION_PLAN.md)

## Storage and lifecycle

Original MP3s in `audio_files/` and metadata under `docs/understand/` are separate from generated resources. Production SQLite, Chroma, caches and logs live under `data/`; automated checks use `.test-runtime/`. Metadata and lyrics work files also exist under `docs/`; they are not documentation cleanup targets.

Electron owns backend startup and graceful shutdown. Normal startup migrates SQLite, closes stale sessions, prunes state, clears generated caches, initializes playback and may start managed processes. Never start the production application as a test fixture. Use the project Python venv and [isolated verification](TESTING.md).

The server binds to loopback by default. Run one API worker per instance because playback and queue coordination are process-local. Configure separate `RADIO_DATA_DIR`, `RADIO_MUSIC_DIR` and `RADIO_METADATA_DIR` for isolated instances. See [SERVER_ACCESS.md](SERVER_ACCESS.md).

## Song selection and desktop defaults


`llm_reranking_enabled` controls only the final LLM song-selection stage.
It defaults to `true`, including an additive `INSERT OR IGNORE` migration for
older databases. Existing users keep LLM selection enabled. In radio mode,
disabling it uses the existing deterministic assignment over the same retrieved
candidates and allocated slots. Normal, shuffle and sequential modes retain
their existing behavior. This switch does not disable embeddings, taste,
constellations, mood, energy, filters, DJ announcements, talk, TTS or commands.
Those features can still use their independently configured AI services.

Each new session snapshots the profile default into its settings overlay.
Changing the profile default does not alter existing sessions. A session
override persists in the existing session context JSON and survives reconnection
and restart. Settings displays both **LLM song selection** for the current
session and **LLM selection for new sessions** for the profile default.

### API

All existing API authentication requirements apply. Session operations additionally
require the `X-Radio-Playback-Owner` header; legacy settings requests select a
session using `X-Radio-Playback-Session` and its owner header.

* `POST /api/playback/sessions/{id}/reranking` accepts `{"enabled": false}`
  with a strict JSON boolean and returns the session snapshot.
* `GET /api/playback/sessions/{id}` exposes the effective value as
  `preferences.settings.llm_reranking_enabled` (`"true"` or `"false"`).
* `PUT /api/settings/llm_reranking_enabled` accepts
  `{"value": false, "scope": "session"}` or `{"value": false, "scope": "global"}`.
  The default scope is `session`. Legacy string values `"true"` and `"false"`
  are also accepted on this settings endpoint. Invalid values/scopes return 422.
* `GET /api/settings` includes the current session's `llm_reranking_enabled`
  and `llm_reranking_default_enabled` for the shared profile default.

Changing this setting preserves the current song, playback instance, listening
session, queue, preview, manual additions, reservations and prepared speech.
It does not request a rebuild. Subsequent rebuilds capture the effective mode
and a separate ranking configuration epoch. Work from an older epoch cannot
publish ranking results. A completion notification clears the old rebuild
indicator without cancelling the existing speech generation. DJ/talk generation
remains independent of the song-selection switch.

### Fresh Electron sessions and routine traffic

Fresh Electron sessions now create the same isolated browser-output session as
the web UI. Saved sessions retain their explicit output selection, including
VLC. The output selector still performs the established fenced handoff. Native
desktop claims remain available for compatibility; the React startup no longer
claims VLC automatically.

Position requests use the matching local browser audio observation directly,
avoiding redundant legacy status requests from the 300 ms position timer. Native output or
missing/mismatched observations retain the server status path. The browser
producer lease still renews every three seconds; its admission and expiry
fences are unchanged.

Uvicorn access logs hide successful routine polling, media, position, lease and
observation requests by default. Errors remain visible. Set
`RADIO_QUIET_ACCESS_LOGS=false` to restore successful request logging. Query
strings are removed in either mode so media grants cannot enter access logs.
Detailed queue rebuild step messages now use DEBUG; failures retain their
existing ERROR/WARNING logging. Routine queue/rebuild notifications no longer
print on every browser audio update in the renderer console.

### Verification

Run the guarded backend suite with the project virtual environment, plus
`npm test` and `npm run build` from `frontend/`. Focused backend coverage is in
`test_session_settings.py`, `test_queue_lifecycle.py`, `test_playback_api.py`
and `test_access_logging.py`. Frontend controller/bridge tests cover new and
restored Electron output and local browser position requests. Tests use
disposable fixtures and do not exercise audible output or live model quality.

## Historical evidence

Prior audit descriptions, test counts, rollback references and checkpoint records are preserved in [the archive](archive/README.md). They describe their recorded revisions and must not be treated as current acceptance results.
