# Android app plan

Status: user proceeded from sleep timer/playlists/queue editing. Offline volume normalization is implemented and verified, awaiting phone review. Server pairing follows; current Wi-Fi LAN reachability is unverified.
Branch: `android-app`, based on `stability-server-architecture`.

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
- Initial connectivity: home Wi-Fi, with manual sync.
- The user is unsure whether their current Wi-Fi permits LAN access. At the server-connection checkpoint, test actual phone-to-server reachability, server binding and network restrictions, then explain suitable connection options before configuring one. Do not infer LAN access from a shared SSID or the laptop's Internet connection.
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
| 5 | LAN pairing, private device credentials, validated catalog/vector import, matching and corrections | Failed imports preserve working catalog; uncertain matches are reviewable; no connection needed to play |
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
- Remote access beyond LAN is a later deployment decision; LAN development does not imply public server exposure.

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
