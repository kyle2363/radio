# Android app plan

Status: the local-player milestone and checkpoints 1A–1C are phone-tested and accepted, including the separate Server/Matching screens and scalable review improvements. The user authorized committing this work and proceeding with checkpoint 2A local listening history and feedback. Direct LAN hosting is deferred.
Branch: `android-app`, based on `stability-server-architecture`.

## Checkpoint 1C phone feedback — dedicated Server and Matching screens

The user reports matching works, but reviewing 1,800 songs one at a time is too slow and changing inline content makes Settings jump. This revision stays within 1C and does not start the next feature checkpoint.

- Navigation is now **Settings → Server → Local matching**. Server has its own scrollable pairing/catalog screen with the existing keyboard compensation; Matching has its own full-size searchable list. Back returns Matching → Server → Settings, and the persistent mini-player uses the same playback session.
- Matching uses lazy stable-key rows, All / Review / Unmatched / Matched filters, search across filenames/titles/artists, and fixed-size controls. Review membership stays in place while decisions change; tap the filter again to refresh membership. Candidate detail opens in a fixed-size dialog after dismissing the search keyboard.
- Select a visible single candidate directly using its row checkbox. Multiple candidates require an explicit candidate choice in the dialog. **Confirm selected** applies those choices together; it never silently chooses between duplicate recordings. Confirm/reject/reset remain available in the dialog.
- Saving a correction retains the current list and refreshes only affected local files. It no longer queries provider metadata for every song after every approval. Batch saving reads catalog metadata once, rechecks each selected file before its own transaction, and refreshes affected rows once at completion. Cancellation may leave already-saved decisions; these remain visible. File and catalog replacement fences are preserved.
- Tests cover retaining list rows during save, bounded provider queries for a single correction, explicit batch target persistence, searching/scrolling 1,800 synthetic rows, duplicate-candidate choice, inline unique selection, stable Review rows and back navigation with unchanged playback. The test fixture counts provider queries to check the performance regression without an unreliable wall-clock threshold.
- A connected phone was automatically included by Gradle in the initial focused in-memory matching regression run, which installed the development APK. Remaining verification explicitly selects `emulator-5554` using both `ANDROID_SERIAL` and the Gradle device serial property. No claim of physical-device UX acceptance is made for this revision.

Final verification: **83 emulator tests passed**, zero failures/ignored, on `emulator-5554` only; **27 JVM tests passed**; debug build and lint passed (**0 errors, 38 warnings**). The initial UI failures were selectors expecting one filename node instead of the list plus dialog, and attempting to scroll a fixed-header Cancel button. Those test assumptions were fixed. Full keyboard/back-navigation tests and unchanged playback checks passed. List/dialog screenshots from the 1,800-row fixture were inspected.

Frozen installable APK: `.test-runtime/android-checkpoint1c-review/radio-checkpoint1c-review-debug.apk` (40,482,859 bytes; SHA-256 `34b79955266e9bc9fa7a49755a6686d20c382d04afe4ee14556666e4726cc8d3`). XML, lint, unit-test output and screenshots are in the same artifact directory. No backend/frontend behavior or schema changed in this revision; their previous suites were not rerun. No commit was made.

Phone check: update preserving data, open Settings → Server → Local matching, search/filter the real library, select several single-candidate rows and confirm together, review a duplicate, verify scrolling stays put, and return through Server/Settings while music continues.

## Content identity and matching checkpoint 1C — October 31, 2026

### Delivered behavior

- Desktop **Prepare catalog** explicitly hashes approved library files read-only before exporting metadata/vectors. SHA-256 values are cached against resolved path, size, modified time, filesystem identity and change time. Windows uses native file change time because Python's Windows `st_ctime` is creation time. Before/after checks reject changing reads; malformed cached digests are ignored. GET/status and ordinary scans never hash audio. Cancellation retains completed cache work and the previous published catalog. Hashing is outside the existing bounded publication window; five-minute UI polling remains bounded and can resume by reopening Settings.
- Android **Match local songs** explicitly reads granted URIs in bounded chunks, reuses valid hashes and supports cancellation. Missing size/modified metadata prevents hashing; changed fingerprints invalidate cache use. A hash index handles renamed identical copies. Artist/title filename prefixes and suffixes provide review suggestions only, preserving recording-version words. Duplicate exact server copies remain ambiguous. No audio fingerprint dependency, source writes, metadata rewriting or upload is added.
- Compact **Review matches** has Needs review / Unmatched / All filters, paginated local files, candidate evidence and Confirm / Reject / Reset actions. Saved corrections are scoped to server/catalog, local byte hash and target content identity (exact target payload when no hash exists). Replacing a server recording invalidates an old correction; removed targets and rejected candidates remain resettable. Explicit corrections reread local bytes and fence catalog/library changes before saving. Settings disables competing pairing/import/matching actions during work.
- Room v7 adds only content hashes and saved decisions, with explicit v6→v7 migration and exported `7.json`. Migrations from every source schema v1–v6 preserve local songs, playlists, playback starts, bookmarks and loudness data. Playback code, source tags and the active queue are unchanged.

### Verification and limits

- Guarded backend suite: **577 passed, 9 skipped**. Skips remain Windows symlink privilege, private energy fixtures and opt-in VLC. Focused hash/catalog suite: **23 passed**, including real Windows same-size edit/restored-mtime invalidation, changed-read rejection, cache reuse, cancellation, export and approved-root checks.
- Frontend: **128 tests passed** and production build passed. Existing duplicate-border and large-chunk warnings remain.
- Android: **27 JVM tests passed**; **80 full emulator tests passed**, zero failures/ignored. After the final cancellation/direct-song-lookup/missing-target action refinements, **12 focused emulator checks passed** (matching/review, six migration sources, actual keyboard and Server appearance), with unit tests/build/lint rerun. `assembleDebug` passed; final lint reports **0 errors, 38 warnings**. Full regression and final focused XML, unit XML and lint output are frozen under `.test-runtime/android-checkpoint1c/`.
- Focused emulator checks passed for matching persistence, rejected/reset candidates, target replacement/removal, server/catalog separation, live cancellation, UI actions and all six source migrations. A fixture initially claimed 32-byte files while returning 37 bytes; its metadata was corrected after the new read-length fence rejected it. An earlier backend run exposed Windows creation-time semantics; the native change-time fix passed focused checks and the final full suite.
- Physical-phone matching coverage and playback/storage performance still need user review. Same-sounding retags/re-encodes may remain unresolved. Android cache freshness depends on honest provider size/timestamps; no byte-cache scheme proves unchanged content when a provider hides edits. Unmatched files remain playable. No offline Radio, feedback journal/sync, public hosting, Cloudflare/Firebase or speech is introduced.
- The planning document was accidentally truncated by a worker encoding error and recovered from the original recorded creation patch and subsequent edits. UTF-8 reads and documentation checks were repeated. No user data was discarded, no production media was used for automated tests, and no commit was made.

### Phone check

1. Update the debug app without clearing data; restart the updated desktop app/backend and restore USB forwarding.
2. Let ingestion settle, then **Prepare catalog** on desktop. Wait for completion (the first content-hash pass can be lengthy), then **Import catalog** on the phone.
3. While local music plays, choose **Match local songs**, cancel once and retry. Check matched / needs-review / unmatched counts; renamed identical copies should match.
4. Review an uncertain/duplicate entry, confirm or reject it, then reset/change the correction using All if needed. Reopen offline and verify the decision persists and playback/queue remain intact.
5. Report the three counts and unexpected misses. Stop for phone acceptance before checkpoint 2A.

See [the setup guide](MOBILE_PAIRING.md) for cache limitations and recovery. Frozen APK: `.test-runtime/android-checkpoint1c/radio-checkpoint1c-debug.apk` (40,356,903 bytes; SHA-256 `c0aba83d52f9063896ea5680115dfc6a2ed9bef508c4b379d31a44560ba543e0`).

## Checkpoint 1B acceptance and 1C scope — October 31, 2026

- The user reports 1B testing succeeded, including the bundled Server-settings keyboard fix, and authorized 1C.
- Add explicit cached file hashes on desktop and phone, conservative local-to-catalog matching, and persistent confirmations/rejections. Settings → Server → Catalog gains **Match local songs** and **Review matches**, with matched, needs-review and unmatched counts.
- Keep the personal two-user deployment simple. No new account/scaling infrastructure, offline recommendation mode, shared feedback/sync or remote hosting is authorized by this checkpoint. Source audio and tags remain read-only, and playback stays independent.

## Catalog import and keyboard checkpoint 1B — October 31, 2026

### Delivered behavior

- Desktop Settings → Server / Paired devices now has **Prepare catalog**, status and revision/song/vector counts. The explicit action enables the existing USB gateway and prepares one shared immutable snapshot from existing metadata/vectors. Status polling runs only while the settings section is open and preparation is running; reading status never starts export or enables hosting.
- The mobile gateway serves only prepared catalog revisions, exact-base deltas and digest-addressed GET/HEAD vector bytes with `catalog:read`. Mobile GETs never run ingestion/export or models. A failed preparation retains the last prepared revision. API-owned ingest activity and an epoch fence catch overlapping ingest, including work that starts and finishes during export. Independent maintenance scripts are outside this tracker; avoid them and catalog edits during preparation. SQLite and Chroma still lack a shared transaction.
- Android Settings → Server has **Import catalog**, saved revision/counts, progress, Cancel and clear errors. No automatic download or sync is added. Full manifest and vector checks precede one Room transaction that publishes the active revision. Local songs, content URIs, playlists, queue order, bookmarks, normalization and playback remain separate.
- Room **v6** adds only imported-catalog metadata and active-revision tables. Explicit v5→v6 migration and exported `6.json` are included; tests replay all source schemas v1–v5 and preserve existing data. Private vector files are outside backup; restored metadata with missing bytes asks for reimport rather than pretending to have usable vectors.
- Canonical Python JSON bytes are validated directly, preserving numeric spellings, negative zero, Unicode scalar key ordering and additive fields. Descriptor provenance, little-endian encoding, dimensions, normalization, finite/nonzero values, lengths, content hashes, duplicate IDs and safe blob references are checked. Shared bytes are validated under every descriptor interpretation.
- The phone stores the **full** manifest checksum as its revision identity; a separately requested exact-base delta must describe the same target. A delta returned in place of the required full response is rejected. Validated blobs are reusable after interruption. Reference-aware private cleanup retains all active and in-progress references, reclaiming unrelated inactive blobs/metadata so updates do not permanently exhaust the cache.
- Valid signed server leases are reused within an import, with protected pairing checked on every request and the identity refreshed before expiry. This avoids exhausting the anonymous identity endpoint's 20/minute limit. Bounded, cancellable `Retry-After` waits handle catalog/vector throttling within the five-minute import budget. Expired/revoked credentials stop import; wrong-source, older, corrupt, incomplete or cancelled updates retain the working catalog. Cancellation after an already-committed adoption is reported as completion.
- Fixed the reported keyboard problem in the actual Settings hierarchy: consume Scaffold insets, apply IME padding to the scrolling container and bring the whole pairing form into view after the keyboard resizes. The focused field and Pair button remain above the keyboard, and dismissing it preserves the mini-player and playback state.
- Kept the scope appropriate for one personal server and a couple of listeners. No account-management layer, distributed workers, new runtime dependencies or hosting infrastructure was added in 1B.

### Verification

| Check | Result and limits |
| --- | --- |
| Full guarded backend suite | **569 passed, 9 skipped** in 118.23 s. Same environment/opt-in skips as 1A; no production app/data/audio/Chroma used. |
| Frontend | **127 passed**; Vite build passed. Existing duplicate-border and bundle-size warnings remain. Includes explicit enable-before-prepare, failure ordering, read-only status, polling/disposal and bounded private IPC regressions. |
| Real loopback catalog smoke | **9 checks passed** against disposable synthetic exports, including explicit preparation, canonical full/delta/vector delivery, failed-update retention, scope/route rejection, revocation and listener closure. Log: `.test-runtime/mobile-catalog/live-smoke-final.log`. |
| Android JVM | **22 passed**. |
| Final full API 37 emulator run | **75 passed**, zero failures/ignored, in 2m58s. Includes existing playback/UI/storage regressions, 18 catalog import checks, all five source-schema migrations, pairing and actual IME/settings checks. |
| Focused Android checks | **34 passed** before the full run: catalog import (18), migrations (5), keyboard (1), Server appearance (1), pairing security (9). Shared fixtures exercise Python/Kotlin wire compatibility; a 30-blob import verifies bounded identity fetches. |
| Build and lint | Debug APK built. `lintDebug`: **0 errors, 35 warnings**. The new `UsableSpace` suggestion is intentionally conservative: admission reserves actual free bytes and does not reclaim unrelated app caches. |

The keyboard test failed with Pair obscured before the fix, then passed with the real IME in the real RadioApp settings hierarchy; current fake playback/queue/position remains unchanged after dismissal. Other Red/Green cases include missing import/control implementation, prepared gateway ordering, rate-limit headers and Windows atomic-directory publication retries. Full and delta representation checks, cancellation before/after commit, corrupt/missing blobs, exact retry, cache reclamation and quotas have explicit regressions.

Astra's boundary review began but could not finish because of its usage limit. The root and an independent Sol source review identified and corrected identity-throttling and full/delta publication issues; the final regression checks above passed. This is not a claim of an external security audit or physical-phone validation.

The emulator required a cold boot/software renderer after an old snapshot stalled and one transient offline event. Those runs were not counted as passing. The final full run completed successfully; no physical device was installed or targeted. No original audio/tags, production history, database or embeddings were used as fixtures. No commit was made.

### Files, setup and acceptance

- Backend: `mobile_catalog.py`; catalog read/preparation integration in `mobile_gateway.py`, `mobile_hosting.py`, `main.py`; bounded atomic publication retry in `portable_server.py`; synthetic golden fixtures and regression/CI additions.
- Desktop: narrow catalog prepare/status IPC in the existing main/preload/helper; compact `MobileCatalogSettings.jsx` and preparation-state helper/tests.
- Android: new `portable/CatalogJson.kt`, `CatalogValidator.kt`, `CatalogRepository.kt`, `data/PortableCatalogs.kt`, v6 migration/schema and repository wiring; catalog authentication/download support in `connection/`; Server import controls and Settings IME fix; catalog/migration/keyboard regressions.
- Docs: current [USB pairing/import guide](MOBILE_PAIRING.md), portable catalog/sync/access notes and this checkpoint. Older stable-map v5 references are historical; source and exported schema now use v6.

Frozen installable APK: `.test-runtime/android-checkpoint1b/radio-checkpoint1b-debug.apk` (39,432,653 bytes). Standard build output remains `android/app/build/outputs/apk/debug/app-debug.apk`. The final emulator XML is `.test-runtime/android-checkpoint1b/full-instrumented-results.xml`; other diagnostic artifacts remain under `.test-runtime/`.

Phone check-in:

1. Install the update with data preserved, restart the updated desktop app/backend and restore USB forwarding. Existing pairing should still work.
2. Enter/paste in Server settings with the software keyboard visible. Verify the whole field and Pair button are reachable; dismiss it and confirm playback/navigation are unchanged. Use an unpaired fixture/Forget pairing only if you intentionally want to repeat provisioning.
3. Let library processing finish. Choose **Prepare catalog** on desktop, wait for ready, then **Import catalog** on the phone while local music plays. Check counts/revision; local filenames and queue must not be replaced.
4. Reopen offline and confirm the imported revision remains visible. Cancel/retry or briefly disconnect USB during an update and verify the previous catalog remains usable until a complete import succeeds.

No hashing/matching, offline recommendations, feedback/outbox/profile sync, LAN/public hosting, Cloudflare, Firebase or speech is implemented by 1B. Imported vectors are a foundation, not a new recommendation mode. After resolving phone feedback, the recommended next checkpoint is **1C — explicit content identity and conservative matching**. Stop here for user review and approval.

## Checkpoint 1A acceptance and 1B scope — October 31, 2026

- The user reports USB pairing works as expected and explicitly approved proceeding with checkpoint 1B.
- New phone feedback: opening the keyboard to enter the pairing bundle covers the field and Settings does not scroll it into view. Fix this within 1B, including focused-field/Pair visibility with the real keyboard and actual Settings scroll hierarchy.
- 1B adds explicit desktop catalog preparation and authenticated, validated Android catalog/vector import, with atomic adoption and retention of the previous valid catalog on failure. Imported server records stay separate from local media and the active queue. No content hashing, song matching, offline scoring, profile sync or remote hosting is included.

## USB pairing checkpoint 1A — October 31, 2026

### Delivered behavior and design

- Added the approved compact Android Settings → Server flow: masked one-time bundle paste, Pair, server identity/status, pending Retry pairing, Check connection and Forget pairing. Electron development Settings → Server / Paired devices creates/copies expiring bundles, lists devices and revokes them individually.
- A separate loopback-only ASGI listener at `127.0.0.1:18766` exposes only signed identity, ticket exchange and authenticated capabilities. It is off at launch, starts on explicit bundle creation and closes during desktop/backend shutdown. Desktop routes and playback ownership stay on the existing service.
- Electron main supplies a private parent capability over stdin; neither that capability nor the device bearer reaches the renderer. Narrow IPC validates the main window and projects bounded, sanitized responses. The visible bundle contains a five-minute one-time ticket, not a desktop password or device bearer.
- Persistent P-256 server identity is protected by Windows DPAPI. Android verifies its pinned SPKI/signature, endpoint, identity, fresh lease and generation/revision before dispatching credentials. The client saves its random candidate bearer/exchange ID before transmission; the server atomically registers the existing portable digest authority and an exact-retry receipt. Lost responses do not create duplicate devices. Grants contain only `catalog:read`.
- Android Keystore AES-GCM protects credentials; backup and device-transfer exclusions cover their directory. A non-backup marker, backed-up nonsecret identity expectation and existing key detect restoration/key loss and quarantine inaccessible state. Pairing metadata uses a separate SharedPreferences store; Room stays at v5, preserving existing migrations, playlists, loudness, history and bookmarks.
- Platform networking adds no Android HTTP dependency. It refuses redirects, limits response bytes and uses bounded executors with a cancellable cumulative deadline and guards against dispatch after timeout. A signed lease is refreshed if a delayed exchange leaves too little validity before sending the bearer. Cleartext is accepted only for the exact USB loopback endpoint in debug builds; the release client and release network configuration reject it.
- Added `cryptography==50.0.2` to the backend/lean test requirements for standard signing primitives; installed only that wheel into the existing project venv. Identity creation is lazy and fails closed if protected storage is unavailable. Pairing's additive server schema installs after a verified backup; existing portable devices survive.

### Verification and limitations

| Check | Result and scope |
| --- | --- |
| Full guarded backend suite | **558 passed, 9 skipped** in 129.81 s. Skips remain Windows symlink privileges (2), private energy fixtures (3), and opt-in native VLC tests (4). No production startup/data/media used. |
| Frontend | **117 passed**; production Vite build passed. Existing duplicate-border and bundle-size warnings remain. |
| Real Windows loopback/DPAPI smoke | **10 checks passed**, including a final rerun after listener refinements. Summary: `.test-runtime/mobile-pairing/live-smoke-final.json`; all data is disposable. Verified signed bytes, persisted DPAPI identity, exact retries, authorization/route/Host/origin rejection, revocation and socket/listener shutdown. This was a local socket test, not a phone connection. |
| Android JVM | **18 passed** on final code. |
| Full API 37 emulator suite | **54 passed** before the last isolated deadline/test-store refinements; included the existing 45 local-player/storage/UI regressions and the initial nine new checks. |
| Final Android focused suite | **10 passed** (nine pairing-security checks and one Server UI check) after deadline/cancellation/isolated-store refinements. Shared Python-signed fixture verifies native interoperability and expired-during-exchange identity refresh. |
| Android packaging/lint | Debug app/test APKs built; `lintDebug` passed with **0 errors, 34 warnings**; release manifest processing passed. Two new `UseKtx` suggestions concern intentionally synchronous identity commits before secret dispatch. No release APK or physical-phone test was claimed. |
| Visual/security review | Inspected actual Server composable light/dark emulator captures with a no-network fixture repository. Astra reviewed the security paths; the reported response bounds, lease freshness and cancellation-fencing findings were fixed and source-reviewed as resolved. This does not substitute for phone acceptance. |

Red/Green evidence includes missing implementation first, the generation/revision floor regression, slow request-body handling (old per-chunk behavior returned 400 instead of the expected total-deadline 408), and Electron oversize/stalled-response tests. Cross-language review caught fractional wire expiries; mobile wire expiry fields now use integer epoch seconds. A UI test's one-second loading wait was replaced by the existing suite's five-second condition wait, without a sleep. Instrumented credential tests use separate files/preferences/markers/Keystore aliases and assert cleanup leaves the default identity metadata/key untouched.

Android logs/reports are in `.test-runtime/android-pairing/` (`full-instrumented.log`, `full-instrumented.xml`, `final-checks.log`, `final-instrumented.xml`, plus red/fence logs). Reviewed images are `pairing-review/server-light.png` and `server-dark.png`. No source audio/tag, production database, Chroma store or real listening history was used as a fixture. No commit was made.

### Changed files and next check-in

- Backend: new `mobile_identity.py`, `portable_pairing.py`, `mobile_gateway.py`, `mobile_hosting.py`; local integration/shutdown in `main.py`; both requirements; two mobile test modules and shared `mobile_pairing_golden.json` fixture; CI contract selection.
- Electron/frontend: private lifecycle/control helper, narrow main/preload IPC, `MobilePairingSettings.jsx`, its Settings insertion, transient bundle-state helper and Node regressions.
- Android: `connection/` protocol/security/store/repository/client, `ui/ServerSettings.kt` and the Settings insertion, manifest/build/network/backup configuration, JVM and instrumentation tests. Existing playback/scanner/Room implementation is unchanged.
- Documentation: [USB setup and recovery guide](MOBILE_PAIRING.md), server-access and portable-contract notes, this checkpoint log, architecture status pointer and exact ignore exceptions for the new documents.

Installable debug APK: `android/app/build/outputs/apk/debug/app-debug.apk`. Follow [MOBILE_PAIRING.md](MOBILE_PAIRING.md) for exact dependency, install, `adb -d reverse tcp:18766 tcp:18766`, desktop creation and phone paste/retry steps. Bundle transfer is manual; no QR/camera scanner or automatic clipboard bridge is included. Packaged Electron does not enable the development flow.

Phone/desktop review: update without clearing data; start local music, create/redeem a USB bundle and check the server; unplug/reconnect USB and retry while preserving playback; revoke and confirm rejection without losing local data; quit/relaunch Radio and verify the USB gateway requires explicit enabling again. Check both themes. Existing local-player acceptance remains accepted; these are new pairing/integration checks.

Not implemented: catalog/vector import, content hashing/matching, offline recommendations, feedback/outbox/sync, LAN/public hosting, Cloudflare, Firebase, connected AI or speech. The pairing grant does not make those features available. No external resources were provisioned. Recommendation: resolve any phone feedback within 1A, then obtain explicit approval for **1B — validated catalog/vector import**. Stop here; do not advance automatically.

## Server integration architecture checkpoint — October 30, 2026

- The current local player is accepted; do not request the same phone acceptance again. New integration changes retain their own regression and acceptance gates.
- The user confirms the phone has the same audio as the laptop, sometimes with different filenames and extra artist/title suffixes. Exact byte equality remains unverified. Plan explicit read-only content hashes, conservative matching and reviewable corrections; filenames alone are not recording identity.
- Direct LAN hosting is deferred by the user. Develop over USB/`adb reverse`; the proposed first remote release uses an optional dedicated mobile gateway, Cloudflare Quick Tunnel and Firebase Spark endpoint discovery. No external setup or production implementation has been performed.
- [MOBILE_CONNECTION_PLAN.md](MOBILE_CONNECTION_PLAN.md) records the source audit, current implementation matrix, dependency graph, proposed API/security contracts, module changes, smaller checkpoints, verification and unresolved risks. Its proposed server sequence replaces the LAN-first ordering below once reviewed; the local-player checkpoint history remains historical evidence.
- Phase 0 changed documentation only. Focused checks passed: 60 portable foundation tests and 15 Electron lifecycle/auth-helper tests. No Android build/phone check, full regression suite, live hosting test or production startup was performed in this checkpoint.
- Next proposed checkpoint: **1A — identity and USB pairing only**. Stop for architecture review and explicit implementation approval first. Catalog import, matching, learning/sync, remote hosting, discovery, connected ranking and speech follow separate gates.

## Collaboration and release gates

- Build with the user, one agreed increment at a time.
- Early checkpoints cover individual screens or behaviors. Later checkpoints cover phases.
- At each checkpoint: provide an installable build when applicable, summarize changes and verification, list limitations, and give a short phone test checklist.
- Stop for user feedback and explicit agreement before starting the next increment. A successful build alone is not approval to proceed.
- Address feedback within the current increment before expanding scope.
- First usable release: dependable local music playback. First intelligent release: offline recommendations and manual profile sync.
- Preserve existing staged and unstaged starter files. Review the final starter diff before any commit; creating the branch does not commit them.

## Agreed product requirements

### Audience and experience

- Primarily a personal app, also suitable for demonstrating in a portfolio.
- Radio discovery and manually choosing music have equal importance.
- Configurable opening screen; default is Last Screen, falling back to Library on first launch.
- Restore the previous song and position paused.
- Rich controls for a sense of control, plus an easily accessible minimal mode.
- Visual direction combines the desktop Radio identity with familiar Spotify-style music browsing, using original assets and styling.
- Offer Ocean Blue and Deep Teal for comparison in the installed app. Follow the phone's theme by default, with light/dark overrides for review. No permanent palette default has been selected.
- Minimum Android version is API 35 (Android 15), as selected by the user. Compile/target remain API 37.

### Music library

- Approximately 2,000 MP3 files in internal storage under Music, with around eight subfolders organized by date.
- Prioritize folders, user playlists, and songs ordered by when they were added to the phone.
- Default sorting: date modified, newest first. Offer oldest first, date added/first seen (clearly labeled), title, filename, and duration sorting where supported. Preserve the selected sort per library view.
- Compact library rows should show around eight items on a typical phone at normal text scaling. Search opens a separate screen from an icon, leaving room for the list. Preserve accessibility touch targets and allow larger fonts to take more space.
- Existing playlists are in Samsung Music. Migration is deferred; mobile playlists start fresh without altering Samsung Music.
- Prefer matched server metadata, then filename-derived display labels.
- Keep audio files untouched: no moving, retagging, deletion, or duplicate copies during scanning.
- Unmatched songs remain playable and eligible for fallback recommendations.
- Persist local identity and folder access. Missing or revoked access must be distinguishable from an empty library.
- Exact historical transfer time may not be available. Inspect actual device/provider metadata before promising an accurate transfer chronology. Keep first-seen time separately and label any fallback honestly.

### Playback

- Primary output: Bluetooth earbuds.
- Early priorities: gapless playback, shuffle, volume normalization, and sleep timer.
- Crossfade is a later addition.
- Support editable queues, next/previous, seek, lock-screen controls, and background playback.
- Distinguish actual playback starts from queue preparation and preloading in all history tracking.
- Define Bluetooth disconnect, interruption, shuffle, and timer semantics at their implementation checkpoint.
- Normalization must use genuine measured/provided gain data; define a safe fallback for unmatched files before implementation.

### Radio and server integration

- Highest-priority recommendation controls: vibe lock, heart/dislike, and skip reasons.
- Local music playback and recommendation continue with the laptop switched off after any required catalog import.
- Initial development connectivity: USB/`adb reverse`, with manual sync before automatic scheduling. Direct LAN hosting is deferred as of October 30; optional remote HTTPS hosting is proposed in the architecture checkpoint above.
- Current Wi-Fi LAN reachability remains unverified and is not required for the first connected release. A later LAN checkpoint must explicitly address reachability, TLS/identity, listener scope and network restrictions. A shared SSID never establishes reachability.
- Proposed shared profile: sync supported listening feedback, favorites, dislikes, and preferences. Keep active song, position, queue, mood, timer, and DJ state local to each device.
- Never interpret matching server metadata or removed server records as permission to delete phone audio.
- Future server music downloads are desired but require an additional transfer capability.
- DJ speech/talk is connected-only. On disconnection or unavailable speech, music continues. No offline speech generation is planned.
- Basic local playlists belong in the local-player release. Plain lyrics take priority over listening statistics; synchronized lyrics follow when timing data is available through an agreed contract.

## Proposed design decisions awaiting review

- Ocean Blue: light accent `#2563EB`, light container `#DBEAFE`, dark background `#1E293B`.
- Deep Teal: light accent `#0F766E`, light container `#CFEDEA`, dark background `#0D2426`.
- Use lighter, contrast-appropriate accents in dark mode; keep surfaces restrained and neutral. Disable wallpaper-driven color substitution so palette comparisons are consistent.
- Missing-art treatment: restrained, recognizable music placeholder. Avoid decorative waveforms and excessive gradients in accordance with the product direction below. Real artwork should provide visual interest when available.
- Navigation: Radio, Library, and Settings; persistent mini-player opens the full player.
- Library entry points: Folders, Playlists, and Songs, with search and recent-addition sorting.
- Detailed player: essential transport controls, favorite/dislike and vibe lock readily accessible; queue, lyrics, and skip feedback within easy reach.
- Minimal player: same playback session, quieter presentation, core controls; toggling must not reset playback.
- Next song is directly available in the mini-player, full player, and minimal player.
- Seek bar: thin rounded 3dp track and circular 16dp thumb, with a 48dp interaction area and native slider accessibility/keyboard/RTL behavior.
- Approved exception to the otherwise restrained use of gradients: optional soft blue/teal background on Now Playing only. Settings offer Off, Static (initial default), and Animated. Keep library and settings surfaces neutral.
- Animated backgrounds run only during foreground preview playback, stop on pause or when hidden, and fall back to static when Android's animator duration scale is zero. Background phase is read during drawing; gradient brushes are cached. There is no real-time blur, audio analysis, or new rendering dependency.
- Until real playback exists, Play/Pause controls a clearly identified visual preview only. It does not play audio, advance song time, or record listening history. Theme/background preferences persist; preview-playing state does not survive app recreation.
- Opening screen options: Radio, Library, Now Playing, or Last Screen. Suggested initial default: Last Screen, falling back to Library on first launch.
- Review method: approve a small set of screen mockups first, then judge working screens on the phone.

## Implementation sequence

| Increment | Deliverable | Acceptance checkpoint |
| --- | --- | --- |
| 0 | Confirm open design choices and scope; document architecture and starter baseline | User approves visual direction and first increment before feature implementation |
| 1A | Light/dark shell, navigation, configurable opening screen | User checks navigation, theme, text size, and spacing on phone |
| 1B | Detailed/minimal player presentation and missing-art treatment using clearly identified preview content | User approves both presentations before playback wiring |
| 2A | Folder access, MP3 scanning, stable local records, filename labels | Real Music folders scan correctly; retry/restart does not duplicate tracks; audio untouched |
| 2B | Folders, search, sorting, local playlists | User verifies browsing order and can create/reorder a playlist; access failures are recoverable |
| 3A | Play/pause/seek, mini-player, actual-start tracking | Real local files play; UI reflects actual media events; preparing a track records no play |
| 3B | Queue editing, next/previous, shuffle and gapless sequencing | User checks queue expectations and listens for transitions between representative files |
| 4 | Background service, lock-screen/Bluetooth controls, interruption handling, paused restoration, normalization and sleep timer | Screen-off/Bluetooth/restart tests pass; timer behaves as agreed; local player release accepted |
| 5 | USB development pairing, private device credentials, validated catalog/vector import, matching and corrections; split into proposed 1A–1C checkpoints above | Failed imports preserve working catalog; uncertain matches are reviewable; no connection needed to play |
| 6 | Native offline recommender with fallback tracks, vibe lock, feedback and skip reasons | Golden fixtures agree within documented numeric tolerances; user evaluates choices offline |
| 7 | Manual shared-profile sync with durable outbox, deduplication and conflict handling | Retry/interruption does not duplicate plays or lose events; active queues stay independent |
| 8 | Prioritized extras, each approved separately | Lyrics and stats first unless reprioritized; connected speech, crossfade, downloads and remote access scoped separately |

Phase 4 may be split further if reliability or normalization work needs its own review. Basic local history is stored from the start of playback; its statistics UI can arrive later. Plain lyrics precede synchronized lyrics unless usable timing data is available through an agreed contract.

## Technical direction

- Kotlin and Jetpack Compose in the existing independent `android/` Gradle build.
- One application module with clear packages for UI, playback, local music, data, recommendation, and sync.
- Media3 for playback/session integration; Room for structured data; DataStore for preferences; coroutines/Flow for background work and UI state.
- Use folder grants and content URIs for user-selected music; evaluate MediaStore metadata during the scan/order investigation.
- Keep playback independent of server transport and profile synchronization.
- Add dependencies only when their phase needs them; use versions compatible with the verified starter toolchain.
- Use targeted unit/integration tests for identity, queues, recommendation parity, catalog validation, and retry-safe synchronization. Combine those with real-device playback checks.
- Use synthetic fixtures for automated checks and never overwrite the user's audio or source metadata.
- Keep portfolio documentation, screenshots and architecture notes accurate as features become available. Use synthetic demonstration content when sharing publicly.

## Existing contracts and limitations

- `docs/PORTABLE_CATALOG.md`: immutable catalog revisions, provenance-aware embeddings, validation, and local/server identities.
- `docs/PORTABLE_SYNC_API.md`: device-scoped credentials, manual event sync, retry rules and supported shared preferences.
- `backend/portable/matching.py` and `backend/portable/recommender.py`: reference algorithms.
- `backend/tests/fixtures/portable_golden.json`: synthetic recommendation parity data.
- `docs/PORTABLE_MEDIA_PLAN.md`: connected-speech reference; native speech generation is not currently exposed as an implemented capability.
- Existing portable APIs do not transfer active playback or music files. Timed lyrics, native speech delivery, downloads, automatic sync, and synchronized undo/retractions must not be assumed available.
- Optional remote hosting now has a Phase 0 proposal in [MOBILE_CONNECTION_PLAN.md](MOBILE_CONNECTION_PLAN.md). Direct LAN access is deferred; no public exposure is implemented or enabled by these planning changes.

## Decisions confirmed at the planning checkpoint

1. Ocean Blue and Deep Teal replace violet; both must be selectable in the actual Android app. Last Screen remains the opening default.
2. Samsung Music playlist migration deferred.
3. Date modified newest first is the initial library sort, with multiple sorting options.
4. Plain lyrics precede listening statistics; basic playlists arrive with local browsing.
5. Following the mockup review, build the shell with both theme options, a denser list, separate search screen, and Next controls. Stop for installed-app feedback before adding library scanning or audio playback.

The first shell uses clearly labeled synthetic songs and folders. Theme, appearance, opening-screen and minimal-mode preferences persist locally. Transport actions either switch preview tracks or explain which later phase supplies real playback. No media access or server connection is included in this checkpoint.

## First installed-shell checkpoint validation

- Debug app and test APK builds passed with the user's minimum SDK 35 setting.
- Four instrumentation tests passed on the existing Android 17 / API 37 emulator: package identity, eight-folder layout and separate search, Next in mini/full/minimal players, and theme/appearance persistence across activity recreation.
- Visually reviewed actual emulator captures for the compact library and player. Four palette/appearance combinations were captured under `.test-runtime/android-shell/`.
- Android lint completed with zero errors and 22 warnings, including existing dependency/template notices, an older dependency's skipped lint checks, and style suggestions. This is not a warning-free baseline.
- Updated only Android test dependencies to JUnit extension 1.3.0 and Espresso 3.7.0 to resolve the older test stack's Android 17 input API failure. See the official AndroidX Test release notes: https://developer.android.com/jetpack/androidx/releases/test.
- The Gradle connected-test runner encountered an ADB cleanup connection problem; final verification installed the built APKs and ran `adb -s emulator-5554 shell am instrument -w dev.radio.mobile.test/androidx.test.runner.AndroidJUnitRunner` directly, returning `OK (4 tests)`.
- The physical phone was not connected during this verification. Android 15 minimum-version behavior and real-device layout still require later checks; the minimum SDK declaration alone is not a test of Android 15.
- No commit was made. Await theme, density and control feedback before advancing to scanning or playback.

## Player appearance revision validation

- Debug app/test APK builds, local unit tests, and Android lint passed. Lint has zero errors; warnings remain, including a deprecated lifecycle-owner import supported by the existing dependency baseline.
- All six instrumentation tests passed on the API 37 emulator with the final slider/background revision. Additional coverage verifies accessible seeking and the 48dp touch area, next-song position reset, persisted background selection, actual gradient pixel changes, pause/resume behavior, background/foreground transitions, and Off mode.
- Exhaustive local motion-policy test covers all combinations of Off/Static/Animated, playing/paused, foreground/background, and system animations enabled/disabled. Live Android system-setting changes are observed by the app; no phone setting was changed during verification.
- Visually reviewed an actual animated Ocean Blue player capture. Review images and instrumentation output are in `.test-runtime/android-shell/`. Physical-device battery and frame-time benchmarks have not been performed; emulator functional checks are not a battery measurement.
- This remains the appearance checkpoint. Await user feedback before advancing into music scanning or real playback.

# Radio Android — Product Design Direction

## Integrated queue revision

- Queue is now a view within Now Playing, replacing the modal dialog. It shares the same background instance and bottom seek/transport controls with the artwork view.
- Queue button switches to a scrolling, flat list. Current track is selected and accent-colored; a current-position/total counter and in-order/shuffled label reflect the actual playback order. Tap a row to jump to it, or tap the counter to return to the current song.
- Back and the artwork icon return to the artwork view without resetting the queue, seek position, playback or minimal-mode choice. Artwork/details scroll separately above the anchored shared transport, keeping controls accessible on smaller screens.
- Opening the queue starts at the current item. Selecting another song, pressing Next, automatic track changes and position ticks leave the viewport in place; the counter remains an explicit shortcut to the current song. Large queues use lazy rows and cached metadata lookups.
- Each off-to-on shuffle toggle creates a fresh Media3 permutation and avoids repeating the previous order when at least two tracks exist. Switching off restores the original queue order; neither toggle restarts/seeks the current song or records a listening start. The actual player and displayed queue share the new order.
- Shared artwork/queue transport has an additional 12dp bottom gap above the existing system navigation inset, reducing accidental Home gestures without a large layout shift.
- No queue add/remove/reorder controls were introduced. Queue snapshot semantics, listening-start journal and storage permissions are unchanged; the playback engine now replaces its retained shuffle order on activation.
- Debug build and lint passed (zero lint errors, existing warnings remain). All eight targeted emulator checks passed, including real MP3 playback through the shared controls, queue selection/back navigation, preserved seek position, eight complete rows at the standard reference-device text size, and 550-item scrolling without position-tick jumps.
- Follow-up verification: all seven unit tests and 12 targeted emulator tests passed; debug app/test builds and lint passed with zero errors and 29 existing warnings. Repeated shuffle activations on two- and twelve-track queues change the order, preserve the paused song/position, create no extra audible starts, restore natural order when disabled, and drive Next in the displayed order. A 550-track UI check verifies selection and Next preserve the exact viewport while the counter still returns to the current song. Ocean/light and Teal/dark captures confirm the small bottom gap and retained list density.
- Visually reviewed actual emulator captures for Ocean Blue/light and Deep Teal/dark. Screenshots and results are under `.test-runtime/android-shell/`; the large fixture uses a 16/550 counter to compare list density against the supplied Samsung Music reference.
- User check-in: toggle shuffle off/on repeatedly, select tracks after scrolling the queue, and check the small additional Home-button clearance in both player views. Review this before advancing to background playback.
- Background playback implementation is recorded below. Review this increment before implementing paused queue/song/position restoration, then normalization and the sleep timer separately.

## Offline volume normalization checkpoint

- Acceptance update, October 30: the user reports the latest local-player milestone phone-tested and approved. The verification notes and original phone checklist below record the implementation checkpoint, not a renewed request for that acceptance.
- Added an optional, initially off Volume normalization section under Settings → Playback. It lowers louder measured tracks toward a fixed -18 LUFS target. Quiet, silent, unknown, invalid or unmeasured files remain at original gain; this version never boosts. The phone's system volume is unchanged. This is track-level attenuation, not compression, a limiter, or true-peak measurement.
- A read-only MediaExtractor/MediaCodec analyzer measures complete mono/stereo files without creating an AudioTrack or playback event. The streaming meter uses BS.1770 K weighting, 400ms blocks with 100ms steps, and absolute/relative loudness gates. Reference coefficients come from ITU-R BS.1770-5 Annex 1; sample-rate conversion is an independent bilinear transfer-function mapping. PCM and source files are never rewritten or retagged.
- Analysis is explicitly started for the current song or unmeasured available songs, one decoder at a time. It pauses on leaving the activity/screen-off, keeps completed measurements, discards interrupted work, and resumes by skipping valid completed files. Cancellation before a worker starts also clears the running state. The app does not automatically analyze the user's 2,000 files as part of installation, scanning, playback, or automated tests.
- Room schema v5 adds a loudness cache with local identity, URI/size/modified fingerprint, scan context, measurement algorithm version, result/status and timestamp. Rescans exclude stale measurements from playback. Silent results stay explicit; decoder failures can be retried and use original gain. Explicit v4-to-v5 migration preserves playlists, library, history and the playback bookmark.
- A Media3 PCM processor selects gain using each stream's timeline identity, including buffered next tracks. Disabling normalization and unknown tracks pass PCM through unchanged. Empty drain buffers are explicitly handled. Sleep fading remains a separate downstream gain, so cancelling a timer cannot remove track normalization. Startup waits for saved normalization state before releasing pending playback commands; normalization updates do not reset position or manufacture listening starts.
- Independent synthetic MP3 references cover mono/stereo, 32/44.1/48kHz, quiet material, relative-gated sections and silence. The Android meter matched FFmpeg EBU-R128 readings within 0.2 LU on the generated references. Fixture generation uses `backend\\venv\\Scripts\\python -B scripts/generate_android_loudness_fixtures.py --ffmpeg PATH` and writes only `.test-runtime/android-fixtures/`; test assets and synthetic provider cache are not production media.
- Verification: app/test builds and fourteen unit tests passed; lint reports zero errors and 31 warnings. Full API 37 instrumentation returned `OK (40 tests)` in `.test-runtime/android-shell/normalization-full-tests.txt`. Coverage includes reference measurements, silence/corrupt-file fallback, read-only access, gain per stream, cancellation/resume, valid cache fingerprints, migration from schemas v1–v4, saved settings and the existing audio/background/queue/timer/restore regressions. A final targeted automatic decoder-reuse transition check also passed in `normalization-transition-tests.txt`. The settings capture is `normalization-settings.png`. No physical battery/whole-library analysis benchmark was run.
- Phone check-in: update without clearing app data; open Settings → Playback, analyze a few songs using Current song, and enable Volume normalization. Compare loud/quiet songs with the switch on/off, then check seek, next, background playback and the sleep fade. Pause analysis by leaving the app and resume it later; completed counts should remain. Whole-library analysis can take time and is deliberately foreground-only in this increment. Review before pairing/server import.

## Sleep timer, playlists and queue editing checkpoint (completed)

- Sleep is available from the clock button in artwork, minimal and queue views. Offer 15/30/45/60-minute presets, custom 1–240 minutes, optional final ten-second countdown fade, replacement/cancel, and stop at the end of a song. The countdown uses elapsed time rather than the wall clock, continues while paused/backgrounded, and pauses audio at expiry. The fade changes app gain only and resets on cancellation/expiry. End-of-song mode uses Media3's native pause-before-next behavior so the next recording does not begin or record a play.
- Sleep timers belong to the live service, not the restart bookmark. Process/service loss restores playback paused with no revived timer. Selecting/skipping a song keeps an active timer; end-of-song mode then applies to the selected song's end. There is no exact-alarm or battery-exemption request.
- Room schema v4 adds app-local playlists and ordered, unique song membership. Explicit v3-to-v4 migration preserves the existing catalog, playback bookmark and listening history. Create, rename, delete, multi-select add, remove and move songs up/down. Duplicate names are rejected case-insensitively; adding an existing song does not duplicate membership. Missing members remain visible as unavailable; deleting playlists never deletes library records or files.
- Playlists open under Library → Playlists. Each song's menu in the library/search can Play next, Add to queue, or Add to playlist. Playback starts a snapshot in the playlist's custom order, independent of subsequent playlist edits or deletion. This increment does not import Samsung Music playlists or sync playlists with the server.
- Queue supports Add songs, Play next, per-song move/remove menus, and an Edit mode with accessible up/down/remove buttons. Queue options can save the displayed order to a playlist or clear upcoming songs while retaining the current song and earlier entries. Adding an existing queue identity with Play next moves it rather than duplicating it. Other queue additions preserve current playback and position; adding to an empty queue leaves it paused.
- Queue edits operate on the actual Media3 timeline/permutation. Moving or removing another song preserves current position and actual-start identity. Removing the current song selects its displayed successor, or the preceding item when no successor remains, preserving play/pause intent; removing the last entry stops playback, cancels the timer and explicitly clears its restart bookmark. Edits to a shuffled queue affect its displayed permutation; disabling shuffle returns to the stored natural order. Edits are included in paused restoration, and queue selection still does not auto-scroll.
- Review captures are under `.test-runtime/android-shell/`: `playlist-custom-order.png`, `editable-queue.png`, and `sleep-timer-dialog.png`. The dialogs and dense queue were reviewed on the reference emulator, including the timer with its number keyboard open. Current settings text now reflects working background playback and paused restoration.
- Verification: debug app/test APK builds and all eleven unit tests passed; lint reports zero errors and 29 existing warnings. The final full API 37 emulator suite returned `OK (33 tests)` in `.test-runtime/android-shell/local-player-editing-tests.txt`. New coverage checks v3-to-v4 migration and preserved bookmarks, playlist persistence/uniqueness/order/deletion boundaries, actual audio position/start counts during natural and shuffled queue edits, timer replacement/cancellation/fade-gain reset, screen-off expiry, no next-song start at an end-of-song stop, last-entry bookmark clearing, restart cancellation of timers, and the complete create/edit/play/delete UI flow. Existing library, restore, background, themes, search and density tests also passed. Phone audio/battery checks remain part of the user review.
- Phone check-in: create a playlist, add several songs, reorder/rename it and play it; edit/delete the saved playlist while checking the active queue remains independent. Try library Play next, queue Add/Edit/Remove, save queue to playlist and clear upcoming. Set a one-minute timer, lock the screen and check the fade/pause; cancel/replace a timer and try End of song. Reopen after force-stop to confirm the edited queue returns paused and the sleep timer stays off. Review before starting normalization or server integration.

## Paused restart restoration checkpoint (accepted)

- Room schema v3 adds a single atomic, versioned playback bookmark. It stores natural queue identities, exact current shuffle order, source context, current song, position and shuffle mode. It deliberately stores no playing flag. Explicit migrations from v1/v2 preserve the catalog and listening-start journal; there is no destructive fallback.
- The service loads the bookmark against the saved library, installs its timeline paused without preparing/decoding audio, and only prepares/plays after an explicit user command. A restored song can be sought before pressing Play. Returning to an already running background service still reconnects to the live player without resetting it.
- Pause, seek, track/order changes, player events and normal service shutdown request immediate checkpoints. Continuous playback checkpoints every five seconds. One repository-owned IO writer serializes atomic saves and subsequent loads, including across service restarts; initial empty player state cannot erase the saved queue. Abrupt process termination may lose roughly the most recent five seconds plus an in-flight write; saving does not depend on receiving a shutdown callback.
- Natural order and the exact shuffled permutation are restored independently. A song that had advanced into the middle of a shuffle list stays there on restoration; it is pinned first only when a new shuffle order is requested. UI commands selected during startup wait until restoration finishes so a new song choice wins over the old bookmark.
- Known unavailable tracks and lost folder access remain explicit, paused and guarded. Unknown catalog IDs are omitted, duplicate/stale order entries are normalized, and positions are bounded to known durations. If the previous identity is absent, a surviving queue item is selected paused at zero with an explanation. If no saved items remain or the bookmark is unreadable, the user can choose a new library song. Original audio and metadata are untouched.
- Restoration creates no listening start. Explicit Play after process/service recreation starts a new actual-play attempt; preparing, restoring, seeking and paused navigation do not manufacture history.
- Verification: debug app/test builds and lint passed (zero errors, 29 existing warnings). All ten unit tests and the full 26-test API 37 emulator suite passed. Coverage includes v1/v2 migration preservation, atomic ordered saves of a 2,000-song bookmark, malformed data, missing records/access and position bounds, exact shuffled restoration, paused seek, real resume and Next, and background/UI regressions. Separate save/relaunch instrumentation runs also passed with an explicit emulator force-stop between them. Synthetic data lives in `.test-runtime/`; results are under `.test-runtime/android-shell/restoration-*.txt`.
- Phone check-in: update without clearing app data, play a folder, enable shuffle, advance a song, seek and pause. Force-stop Radio from Android's app settings, reopen it, and verify the same song, time, queue order and shuffle state return silently. Try seek while paused, Play and Next. Also try closing/reopening during ongoing playback: the live background session should continue. Review before normalization or the sleep timer.

## Background playback checkpoint (accepted)

- A non-exported Media3 `MediaSessionService` now owns the production engine. The activity ViewModel holds a local service connection rather than the player; leaving the app, screen-off, activity recreation and closing the UI do not pause or release ongoing playback. Reopening connects to the same live queue and position. Media3 manages its foreground-service notification and stopped/idle service lifetime.
- The media notification and lock-screen session publish filename-derived track titles and folder context. System Play/Pause/Previous/Next/seek/shuffle commands pass through the same guarded controller used by the app. The notification opens Now Playing. External transport cannot replace the queue with arbitrary URIs or change the app's repeat/speed policy.
- Added media-playback foreground-service and wake-lock permissions, with Media3's local-playback wake mode. There is no notification-permission dialog for the media session, Bluetooth discovery permission, battery-optimization exemption or boot auto-play receiver.
- Media3 handles audio focus and output becoming noisy. A temporary interruption suspends audio and may resume after focus returns; explicit pause stays paused when reopening the app. Physical earbud disconnect/reconnect and phone-call behavior need the user's phone check. Android protects its route-disconnect broadcast from test injection, so emulator media-key/focus tests are not a physical Bluetooth test.
- The service observes the canonical library and owns actual-start journaling even with no UI. Service tests inject an in-memory catalog/journal and use a dedicated 60-second silent MP3 generated under `.test-runtime/`; source library files and metadata are untouched.
- Shuffle activation places the current song first in the new play order, randomizes the rest, and avoids repeating the previous upcoming order when at least two upcoming tracks exist. Opening a new context while shuffle is already enabled uses the same rule. With only one upcoming song, only one order is possible. Song, position and pause/play state stay intact; the queue viewport does not jump to the first song. The counter remains the explicit scroll-to-current action.
- The expanded system media controls include shuffle and repeat as `SLOT_OVERFLOW` custom actions. Secondary back/forward slots are unsupported by the framework-session conversion in Media3 1.11.1 and silently dropped both buttons; the 2026-10-30 fix restores them. Previous/play-pause/next remain native transport actions. Shuffle/repeat use plain icons; Android/OEM SystemUI owns backgrounds, ordering and compact visibility. Stock API 37 shows only play/pause with a filled background, so circles around previous/next depend on the phone's system UI. Taps use the app's shared controller, and icons follow its state. No custom notification provider is involved.
- Notification regression verification (2026-10-30): a new test using the framework session token from the actual media notification failed before the slot fix and passes afterward. It checks published custom actions, framework shuffle/repeat execution, native transport availability, unchanged song and no extra listening starts. The other seven background-service tests passed, as did all 14 JVM unit tests; debug APK builds and lint passed (zero errors, 31 warnings). Optional instrumentation argument `captureNotification=true` captures the synthetic-fixture notification; reviewed screenshot and failing baseline are in `.test-runtime/android-notification/`. Phone check: update without clearing data, expand notification and lock-screen controls, confirm shuffle/repeat have no circles, tap both and compare app state, then verify previous/play-pause/next retain the phone's native appearance.
- Paused restoration after process/service loss is now covered by the checkpoint above. Normalization and the sleep timer remain later work.
- Verification: debug app/test APKs built successfully; seven unit tests passed; Android lint reports zero errors and 29 existing warnings. Full API 37 emulator instrumentation returned `OK (22 tests)`, covering screen-off progress, foreground notification/metadata, activity recreation, releasing all UI connections and reconnecting to the same player, MediaController and hardware media-key commands, transient audio focus, actual-start deduplication, storage/migration regressions, and 1/2/3/12-track shuffle plus stationary selection in a 550-track queue. Output is saved in `.test-runtime/android-shell/background-full-tests.txt`. Physical Bluetooth and sustained phone battery behavior remain for the check-in.
- Phone check-in: update without clearing app data; play through earbuds, press Home, lock the screen, and leave it playing for several minutes. Try notification/lock-screen Play/Pause/Next/Previous, earbud buttons and notification return to Now Playing. Disconnect earbuds and confirm music pauses; reconnecting should require Play. Try an interruption and resume, then reopen the app and confirm the queue/position remain. Scroll the queue before enabling shuffle and confirm the current song becomes item 1 without the view jumping there.

## Initial foreground playback checkpoint (superseded by background service above)

- Media3 ExoPlayer 1.11.1 plays the scanned content URIs locally. No server connection, source-media writes, or copying to app storage is needed.
- Tapping a track starts a snapshot of the visible ordered song list: the opened folder's direct tracks, All Songs, or search results. Unavailable entries are excluded and distinct local identities preserve same-named recordings. Later browsing/sort changes don't reorder the active queue.
- Working controls: play/pause, accessible seek-on-release, next, previous, native shuffle, automatic next-track advance, and queue inspection/jump-to-song. Previous restarts the current track after three seconds; otherwise it moves back. Queue ends without wrapping; Play at the end replays the final song.
- Mini-player/full/minimal views use actual Media3 position, duration, buffering, error, and isPlaying state. The optional moving background follows actual audio playback. Position polling runs only during playback and reuses the queue/shuffle snapshot.
- Failed decoding or unreadable files show a recoverable playback error. Next can move to another queue entry. Revoked grants/current missing files stop playback; rescan updates availability. Source-URI changes reopen paused against the new grants. Search navigation returns to the same query before returning to the originating folder.
- At this initial checkpoint, an activity ViewModel owned the engine and leaving the app paused playback. The background checkpoint above supersedes that policy and adds service/session ownership. Durable queue/position restoration, crossfade, normalization and sleep timer remain later work.
- Room schema v2 adds a local playback-start journal using an explicit 1-to-2 migration, with no destructive fallback. A start is stored once per playback attempt after Media3 reports actual playback; preparation, prefetch, decoder failure, seeking and pause/resume do not increment starts. Completion/skip projections, sync, statistics and persistent feedback are not implemented by this checkpoint.
- Verification: seven local unit tests passed; the full emulator suite returned `OK (17 tests)`. Real silent-MP3 tests cover play/pause, seek, next/previous, automatic advance, end/replay, actual-start deduplication, decoder-error recovery and permission loss. Migration test preserves an existing v1 catalog and validates journal idempotency. Real-player UI test covers folder-bounded queues, queue jumps, sorting independence and foreground pause. After a final search-return improvement, all six targeted player/navigation UI checks passed again.
- Debug APK/test APK builds and lint passed: zero lint errors, 29 warnings (including existing dependency/template/style notices). The API 37 AVD required a cold boot after its saved snapshot stalled. Physical Bluetooth output and the user's actual files remain for the user check-in; emulator silence is not an audible-transition or battery benchmark.
- Synthetic audio is generated only under `.test-runtime/android-fixtures/` and packaged only into the instrumentation APK. Regenerate it with `backend\\venv\\Scripts\\python -B scripts/generate_android_test_audio.py --ffmpeg PATH_TO_FFMPEG`, then build `:app:assembleDebugAndroidTest`. Test APK assets also include the exported Room schemas. Test audio/providers, disposable test databases and screenshots never use the user's library as fixtures.
- Check-in: update the phone build without clearing app data, open a folder and play a song; try pause/resume, seek, next/previous, shuffle and queue jumps; compare All Songs versus a folder queue; check Bluetooth earbud audio and the animated player background. Address feedback before implementing screen-off playback.

## Local-library checkpoint implementation and check-in

- Production library screens now use actual selected-folder records; synthetic songs/folders exist only in test code. The player remains a visual preview for a selected local file, with no audio or listening-history writes.
- Select one Music folder through Android's system tree picker. Only persistent read permission is retained. Changing the source folder requires choosing it explicitly; picker cancellation leaves the saved library alone.
- Room 2.8.5 stores the selected root, nested folders, canonical provider/document identities, read URIs, filenames, modification times, first-seen times, sizes, durations when readable, and availability. KSP 2.3.12 generates Room code; the version-one schema is checked into `android/app/schemas/`.
- Scanner uses batched child-document queries and sequential read-only metadata extraction off the UI thread. Unchanged files with known duration reuse it. A provider failure/cancellation preserves the last committed snapshot; successful publication updates the database in one transaction.
- Stable IDs are based on provider authority and document ID, so repeated scans and overlapping folder selections don't merge same-named files or duplicate unchanged documents. Provider IDs may change when external apps move/rename files; broader reconciliation belongs to later matching work.
- Files absent from a successful rescan become unavailable records, not deleted files. Revoked folder grants produce an explicit recovery action while cached browsing remains available. Files with unreadable metadata remain in the library with filename labels and unknown duration.
- Folders browse hierarchically, including root-level MP3s; Back returns to the parent. Songs searches title/filename/folder labels. Leaving search dismisses its keyboard. Default sort is date modified newest first, with oldest-first, added-to-app, title, filename and duration choices. Unknown timestamps/durations sort last. Folder added/duration sorts include available descendant tracks.
- Added-to-app is explicitly first discovery by Radio, not a claim about the phone's historical transfer time. Display currently uses filename-derived titles; server metadata is a later phase. Playlists and Samsung Music migration remain deferred.
- Source files, Gradle/KSP configuration and Room schema remain visible to Git. A scoped Android ignore exception prevents the root `data/` pattern from hiding the Kotlin data package; build products and runtime data remain excluded.
- Verification: full suite returned `OK (12 tests)` on the API 37 emulator; five local unit tests passed. Coverage includes nested traversal, duplicate filenames, durable reopen, rescans, missing files, provider failure, cancellation, access revocation, overlapping tree identities, the real empty/scanned/recovery UI, search, and earlier player/theme behavior. Final keyboard dismissal is additionally checked by targeted screen tests.
- Test provider/controls ship only in the test APK, use private `.test-runtime` fixtures and are removed from the emulator after checks. Test databases are disposable; the app's actual catalog is not seeded or deleted by tests. Review images/results are under `.test-runtime/android-shell/`.
- Build/lint passed with zero lint errors; dependency/template/style warnings remain. The synthetic provider deliberately supplies undecodable MP3 bytes to test filename retention and unknown-duration handling. The user's approximately 2,000 real files, actual duration extraction, OEM provider behavior, and scan speed still require the phone check-in.
- User check-in: install/run the build on the phone, choose Music and approve the system folder selection; inspect counts, nested folders, filenames, search and sorting; reopen to check persistence, and rescan to confirm no duplicates. Do not proceed to real playback until feedback is addressed.

## Overall goal

Design Radio to feel like a mature, professionally developed Android music player, not an AI-generated app concept, dashboard, or portfolio mockup.

The intended experience is **music-first, refined, functional, consistent, and quietly distinctive**. Take inspiration from the usability of established Android music players and the visual polish of commercial music apps, while maintaining an original Radio identity.

## Visual principles

- Use Material 3 as the foundation, with deliberate customization rather than excessive custom UI.
- Use a restrained visual language: neutral surfaces, a limited accent palette, consistent typography, spacing, corner radii, and icon styles.
- Avoid excessive gradients, glassmorphism, neon glows, shadows, decorative waveforms, floating cards, and unnecessary animations.
- Do not put every element inside a card. Use flat lists, spacing, dividers, and visual hierarchy when more appropriate.
- Let real album artwork and music content provide much of the visual interest. Missing artwork should use tasteful, recognizable placeholders.
- Prioritize natural information density. Library and queue views should be efficient; the Now Playing screen can be more spacious and immersive.
- Avoid generic marketing-style headings and decorative descriptions. Use direct, conventional music-player terminology.

## Interaction principles

- Every control must have a clear purpose, familiar behavior, and visible state.
- Keep common actions immediately accessible; place advanced actions in contextual menus, sheets, or expandable sections.
- Preserve user context, including browsing position, playback state, queue, and navigation.
- Support meaningful loading, empty, error, offline, permission-denied, and missing-file states.
- Use subtle motion only when it explains navigation, feedback, or state changes.
- Follow Android conventions for touch targets, back navigation, system bars, accessibility, and media controls.

## Design and implementation process

Before implementing each major screen, present its intended hierarchy, interactions, and visual treatment for review.

Avoid designing screens solely around beautiful placeholder content. Test with realistic music metadata, missing artwork, long filenames, large libraries, and actual device behavior.

Use shared design tokens and reusable components to prevent inconsistencies between screens.

Do not introduce decorative elements or additional UI libraries simply to make the app appear more impressive. Every visual decision should have a product or usability justification.

Evaluate each implementation on an actual Android phone in both light and dark themes, not solely through screenshots or previews.

**Success criterion:** Radio should feel like a coherent music player that someone could comfortably use every day, not a collection of individually impressive screens.
