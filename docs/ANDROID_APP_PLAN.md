# Android app plan

Status: background playback, system media controls and current-song-first shuffle implemented; verification and phone check-in below. Paused restart restoration remains a separate next increment.
Branch: `codex/android-app`, based on `stability-server-architecture`.

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

## Background playback checkpoint

- A non-exported Media3 `MediaSessionService` now owns the production engine. The activity ViewModel holds a local service connection rather than the player; leaving the app, screen-off, activity recreation and closing the UI do not pause or release ongoing playback. Reopening connects to the same live queue and position. Media3 manages its foreground-service notification and stopped/idle service lifetime.
- The media notification and lock-screen session publish filename-derived track titles and folder context. System Play/Pause/Previous/Next/seek/shuffle commands pass through the same guarded controller used by the app. The notification opens Now Playing. External transport cannot replace the queue with arbitrary URIs or change the app's repeat/speed policy.
- Added media-playback foreground-service and wake-lock permissions, with Media3's local-playback wake mode. There is no notification-permission dialog for the media session, Bluetooth discovery permission, battery-optimization exemption or boot auto-play receiver.
- Media3 handles audio focus and output becoming noisy. A temporary interruption suspends audio and may resume after focus returns; explicit pause stays paused when reopening the app. Physical earbud disconnect/reconnect and phone-call behavior need the user's phone check. Android protects its route-disconnect broadcast from test injection, so emulator media-key/focus tests are not a physical Bluetooth test.
- The service observes the canonical library and owns actual-start journaling even with no UI. Service tests inject an in-memory catalog/journal and use a dedicated 60-second silent MP3 generated under `.test-runtime/`; source library files and metadata are untouched.
- Shuffle activation places the current song first in the new play order, randomizes the rest, and avoids repeating the previous upcoming order when at least two upcoming tracks exist. Opening a new context while shuffle is already enabled uses the same rule. With only one upcoming song, only one order is possible. Song, position and pause/play state stay intact; the queue viewport does not jump to the first song. The counter remains the explicit scroll-to-current action.
- Process death, force-stop, reboot and long-idle service removal do not yet restore the queue. Durable paused restoration is the next checkpoint, followed separately by normalization and the sleep timer.
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
