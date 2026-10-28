# Radio modernization progress

## Scope and safeguards

- User authorized implementation after initial read-only review.
- Current branch: `stability-server-architecture`, tracking `origin/stability-server-architecture`. The user renamed the original `codex/stability-server-architecture` branch and committed the implementation checkpoint as `c065ed3`.
- Starting commit: `63cc57562baa85645211fa120f42d78d0a67f8ac`. Audit/test foundation committed as `f5c9899`.
- Rollback tag: `codex/modernization-baseline-20261026`.
- All edits and temporary output stay inside this checkout. Original audio is read-only.
- One shared taste/history profile; independent playback sessions/queues.
- Mode switching transfers the whole listening session: song/position, queue, mood/vibe, energy, DJ/guest context, and timer; other sessions remain independent.
- Automated tests and the isolated launcher never write production history or library state. On October 28, 2026, the user explicitly permitted real-history updates during manual checks.

## Phase status

0. Audit and baseline: complete. Static audit and isolated baseline committed as `f5c9899`; baseline was 38 passed, 3 skipped. Runtime features remain unverified.
1. Stability: core queue, playback coordinator, and native observation guards are implemented with targeted tests. The limited libVLC smoke gate passed against generated silence; audible quality remains unverified.
2. Recommendation/playback separation: integrated. Prepared plans, session queues and reservations, coordinator event acceptance, bounded empty-queue fallback, and session-local recommendation settings are wired. The fallback does not record history or consume a pick.
3. Authenticated media streaming: implemented. Authenticated session/media routes, stable track/resource IDs, path validation, and range streaming are integrated and have focused contract tests.
4. Browser playback and output transfer: integrated with targeted coverage. Synthetic browser playback, pause/seek, reconnect without duplicate play history, and separate-tab mood were exercised. The user's later pause/seek report produced further acknowledgement/proxy/reconnect fixes; current real-app retesting and remaining recovery review findings are still open.
5. LAN access and recovery: access policy is documented; no LAN or reverse-proxy validation has been performed.
6. Expanded tests/observability/CI: a lean backend contract workflow and frontend test/build workflow are added. Final reviewed-tree checks: **450 backend tests passed, 9 skipped; 83 frontend tests passed; 4 opt-in native VLC tests passed; production build passed**. Hosted CI has not run.
7. Final validation: pending live model/audio, Electron/OS/LAN checks and remaining reconnect race/idempotency findings. See the finalized local `docs/CODEX_HANDOFF.md`; it is explicitly Git-ignored by the user's current rules and must be carried separately to a new checkout.

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
- Automated runs did not modify production audio, metadata, history, or embeddings. The user subsequently launched the real app and authorized manual history updates; its current runtime/migration state was not audited in the final review. Test and temporary outputs stay inside the checkout.

## Final review of the user's checkpoint and later changes

The user committed the earlier implementation as `c065ed3`, then added reconnect grace/liveness, stored browser identity, retries/deadlines, empty-view quiesce acknowledgements, and WebSocket forwarding through Vite's `/api` proxy. Those modifications were reviewed and preserved. Two reproduced regressions were fixed with Red/Green tests: automatic reconnection now keeps paused sessions paused, and a stale handoff revision cannot enter vanished-browser reclaim.

Remaining review findings are documented for the next agent: disconnect-grace cleanup can race a newly registered same-client connection, reclaim does not retain the caller's original command receipt, and a client handoff timeout does not cancel the server's pending intent/reservation. Duplicated tabs with copied sessionStorage also need explicit producer-fencing verification. These cases are not proven safe by the passing suite.

The final backend run passed **450 tests, 9 skipped**; the full frontend run passed **83 tests**, native dummy-output smoke passed **4 tests**, the production build passed, and `git diff --check` passed. Existing duplicate `border` and bundle-size build warnings remain. No final review commit/push was made; source fixes and docs remain in the working tree alongside the user's changes.

## Current architecture and manual QA references

The current routing, browser audio, authenticated media, session context, and output transfer design is summarized in [ARCHITECTURE.md](ARCHITECTURE.md). Safe manual steps and expected behavior are in [APP_USAGE_CHECKLIST.md](APP_USAGE_CHECKLIST.md). The isolated launcher command is:

```powershell
backend\venv\Scripts\python scripts\run_isolated_app.py --port 18765
```

Its three 25-second WAVs are silent and recommendation/model generation is deterministic. It confines writes to a new `.test-runtime/qa-*` directory, disables TTS/managed processes, and blocks outbound sockets. This provides an app-control walkthrough, not a real-audio or model-quality test.

## Rollback

Stop the development instance before changing branches. Inspect `git status` and preserve desired work, then switch to `main` or create a recovery branch at the baseline tag. Do not use `git clean`, reset data directories, or delete ignored files. Branch switching does not restore runtime data. The agent only exercised schema installation on disposable databases; the user has since launched the real app, so inspect its backup/runtime state before planning any production rollback.

## Playback reliability completion — October 28, 2026

Branch: `stability-server-architecture`. Starting HEAD:
`72441902396c4b673ef94a3c09fab5a7c5c88d73`; the starting working tree was clean.
That commit already contained the earlier reconnect/proxy changes and the two
prior review fixes. This tracked checkpoint supersedes stale statements in the
ignored local `CODEX_HANDOFF.md`; no original library or production database was
used by automated verification. `main` was not merged or changed.

### Confirmed causes and resulting changes

- Control-socket closure revoked the browser's local view, discarded buffered
  audio, and forced reload/reactivation. The media element now survives control
  loss. Transient observation failures retain ordered events and original IDs;
  progress failures do not retire playable media. Pending completion waits for
  authoritative acceptance. Explicit media failure still pauses recoverably.
- Browser controls used the server route even when local audio was available.
  Pause, seek, resume and volume now reach the browser engine. Playback HTTP
  requests have a ten-second deadline; local pause never waits for renewal.
  Delayed pause acknowledgements cannot undo a newer scrub or resume.
- Buffered continuation made socket absence insufficient proof of silence.
  A twelve-second persisted permit, renewed every three seconds, gates browser
  output and absent-producer takeover. Per-document identity prevents copied
  session storage from creating two producers under one grant. Initial and
  promoted WebAudio nodes receive an audio-clock expiry gate. In-flight renewal
  is scoped to its instance so an old response cannot strand a new song.
- Disconnect grace used current state after an asynchronous boundary. Cleanup
  now retains the original generation and instance and serializes registration
  and retirement. Replacement grants are fenced from obsolete cleanup.
- Vanished-browser reclaim used a random internal reactivation receipt. It now
  follows the original handoff transaction and command identity after permit
  expiry. Handoff polling, cancellation and server reservation cleanup resolve
  lost responses without minting conflicting replacement commands.
- Replay used normal selection, whose actual-start callback clears/rebuilds the
  recommendation queue. Replay now seeks the existing item to zero and records
  one receipt-backed replay signal. It preserves pause intent, attempt, queue,
  mood and context. A real-queue regression exposed a second legacy history
  write; the queue now invalidates its taste cache without duplicating feedback.
- Integration checks caught idle Play resuming an empty element, Next calling
  an API that rejects active plans, and incomplete status responses hiding the
  current song. Idle Play now prepares media. Existing authenticated Next/skip
  endpoints keep undo, recovery and skip-feedback behavior. Legacy status
  response metadata remains available to the UI.
- Frontend CI failed installation because the lockfile omitted the declared
  Electron builder dependency. The lockfile was synchronized without replacing
  the working `node_modules`. A lean backend run reproduced missing NumPy and
  uvicorn imports; CPU import dependencies and explicit unrelated-adapter test
  doubles fix those contracts without removing assertions or installing models.
  The workflow additionally runs the new playback/disconnect/replay contracts.

### Verification and acceptance status

The initial current-HEAD baseline was **450 backend passed, 9 skipped**, and
**83 frontend passed**. Final verification: **461 backend passed, 9 skipped**;
**102 frontend passed**; expanded lean CI contracts **245 passed, 1 skipped**;
native libVLC synthetic/dummy-output smoke **4 passed**; Vite production build
and `git diff --check` passed. These are the results for the committed checkpoint.

Commands from the repository root:

```powershell
backend\venv\Scripts\python -B scripts/run_tests.py -q
node --test frontend/tests/*.test.js
$env:RADIO_NATIVE_SMOKE='1'
backend\venv\Scripts\python -B scripts/run_tests.py backend/tests/test_vlc_native.py -q
```

From `frontend`, build with `node node_modules/vite/bin/vite.js build`.
The lean probe uses only isolated dependencies in `.test-runtime`, invokes the
same guarded runner in worker mode, and preserves the installed GPU environment.
Windows async checks required execution outside the socket sandbox; production
write/service guards remained enabled. Skips are two Windows symlink privilege
cases, three private energy fixtures, and four opt-in native cases. Native tests
were explicitly run separately. Existing duplicate-border and bundle-size build
warnings remain.

Actual browser UI checks used synthetic silent WAVs in isolated loopback
instances: idle start, pause/resume, seek while paused, natural advancement,
paused replay to zero with unchanged upcoming order, and Next. Stopping a QA
backend showed the reconnecting message while retaining the current track.
These checks establish visible controls/state, not audible playback quality.
The isolated launcher native wrapper revealed additional QA-only ctypes
recursion during output transfer. It now preserves the `vlc.Instance` class and
passes native pointers to its original allocator. Seven launcher tests and an
actual patched `Instance`/`MediaPlayer` construction/release check passed. The
last full UI handoff retest was interrupted, so this checkpoint does not claim
that complete synthetic transfer check passed after the wrapper correction.

The user subsequently reported that everything seemed to be working well and
requested finalization and a commit on the current branch. This is recorded as
a positive initial app retest; it is not a sixty-minute per-mode acceptance log.
Final review also added local physical pause/position updates to the UI bridge,
guarded by the current instance/item/grant, without replacing song metadata.

Hosted CI at starting HEAD failed both jobs:
[run 40726455069](https://github.com/kyle2363/radio/actions/runs/40726455069).
Public job metadata confirmed frontend installation and backend test execution
failures. Raw logs required repository-admin authentication, so causes were
reproduced locally rather than inferred from generic exit annotations. A local
pass alone is not hosted CI acceptance. This checkpoint is committed locally at
the user's request; it has not been pushed, so hosted CI has not rerun against
these changes. No green hosted result is claimed.

### Remaining gates and risks, in priority order

1. **Listening acceptance remains open.** Supervised real-library browser,
   Electron and VLC checks, including at least sixty minutes per mode, have not
   been claimed. Confirm audible pause/seek/skip/replay, natural advance, both
   handoff directions, timers, independent sessions, AI DJ/TTS/effects, tray and
   media keys, restart, and OS sleep/lock. The user reported a successful initial
   rebuilt-app retest. No real-library listening history was modified by this
   automated work.
2. **Page crash/reload during an outage:** the pending observation outbox lives
   in the page's memory. Same-page reconnect retries are covered; unsent events
   lost with the page cannot be reconstructed. Recovery uses the last accepted
   server checkpoint and requires an explicit grant transition after reload.
3. **Timing and output limits:** persisted server permits assume a sane wall
   clock. Strict audio-clock expiry needs WebAudio; HTML-only fallback has timer
   and media-event fencing and needs frozen/background-page acceptance. Server
   restart recovery uses retained logical attempts and a conservative old-permit
   expiry; it does not promise uninterrupted audio across a backend restart.
4. **Deployment/quality:** LAN/TLS/reverse-proxy deployment, live model quality,
   GPU speech and processed-audio quality remain manual gates. Keep loopback and
   current authentication defaults; use one API worker per instance. No owner
   keys, cookies or private output capabilities belong in logs or reports.

Rollback source code with a normal revert of this branch's reliability commits
after stopping the application and preserving outstanding work. The pre-change
source checkpoint is `7244190`; the older modernization baseline tag remains
available for comparison. Never use `git clean`, reset/delete runtime data, or
restore a database blindly: source rollback does not roll back ignored media,
embeddings or database history. This change adds JSON fields, not a destructive
schema migration. Preserve private verified database backups before any separate
production rollback. Next priorities are supervised listening, outage/reload
acceptance, Electron lifecycle checks, then audible transition polish.
