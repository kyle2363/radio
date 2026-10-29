# Song selection and desktop defaults

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

## API

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

## Fresh Electron sessions and routine traffic

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

## Verification

Run the guarded backend suite with the project virtual environment, plus
`npm test` and `npm run build` from `frontend/`. Focused backend coverage is in
`test_session_settings.py`, `test_queue_lifecycle.py`, `test_playback_api.py`
and `test_access_logging.py`. Frontend controller/bridge tests cover new and
restored Electron output and local browser position requests. Tests use
disposable fixtures and do not exercise audible output or live model quality.
