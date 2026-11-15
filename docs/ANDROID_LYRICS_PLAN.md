# Android plain lyrics — checkpoint 5

Date: November 14, 2026.
Status: design and contract approved November 14, 2026; implemented, verified and
merged into `main` November 14, 2026. Phone acceptance is pending.
Baseline: checkpoint 4.2 is phone-accepted and merged into `main` at `f676e83`
(November 11). This proposal follows the lyrics-before-statistics priority in
[ANDROID_APP_PLAN.md](ANDROID_APP_PLAN.md).

## 1. Deliverable and boundaries

Show existing desktop-library plain lyrics for the current, trusted matched local
song. Save successfully fetched text privately on the phone for offline reading.
Opening lyrics never changes playback, the queue, Radio, position, timers,
feedback or listening history. Playback remains entirely local.

This increment includes a narrow authenticated mobile read endpoint, explicit
loading/unavailable/offline states, bounded caching, and phone verification.
It does not add synchronized highlighting, tap-to-seek, lyric editing, embedded
tag extraction, scraping, transcription, generation, bulk downloads, automatic
profile sync, or a new network requirement for playing music. Source audio and
tags remain read-only. Existing desktop lyric behavior stays intact.

## 2. Screen hierarchy and interaction

Add **Lyrics** to the Now Playing overflow menu in Radio, normal playback, queue
view and minimal view. Keep it discoverable when a song is unmatched so the user
can see the explanation. Disable it only when no current song exists.

Open an expandable Material 3 bottom sheet. Use the existing palette and station
surface rules, typography and system insets. Avoid another decorative card or
animated background. The sheet contains:

1. A drag handle and heading, **Lyrics**, with a close button.
2. The current local song title (up to two lines) and folder context. Do not invent
   an artist when the local library only has a filename.
3. A small source/status line: **From Desktop**, **Saved on this phone**, or an
   unavailable explanation.
4. Scrollable, selectable plain text with preserved line and stanza breaks,
   start alignment and readable body text. Refresh remains reachable outside the
   scrolling text; no auto-scroll or line highlighting.

Opening first checks the current match and private cache. A cache hit displays
immediately without a network request. A miss may fetch once while the sheet is
visible. **Refresh** explicitly requests the same prepared resource again; it
does not prepare or import a catalog. Disable duplicate requests while loading.
Explain that desktop edits require preparing and importing a new catalog.

Back dismisses the sheet and returns to the exact previous player/queue view.
Scrolling lyrics must not move the queue. Dismissing the sheet cancels its request;
there is no background prefetch or retry loop. An actual current-song change
switches the heading, clears the previous text immediately and resets scroll to
the top, then resolves the new song. Pause, seek and position ticks preserve it.

| State | Visible behavior and action |
| --- | --- |
| Loading | Song heading remains visible; compact progress indicator |
| Text ready | Plain lyrics and source line; Refresh available |
| Saved text, desktop unreachable | Keep text visible; “Saved on this phone”; explain failed refresh inline |
| No pairing | “Connect to your desktop to get lyrics”; link to Server settings |
| Unmatched or ambiguous | “Match this song to get desktop lyrics”; link to Matching |
| Matching/catalog temporarily changing | “Checking this song’s match…”; no speculative request |
| Old server without capability | “Update the desktop app to get lyrics”; no legacy-route fallback |
| No text in prepared revision | “No lyrics available for this song”; explain prepare/import after adding desktop lyrics |
| Offline without saved text | “Desktop unavailable. Lyrics haven’t been saved for this song.”; Retry |
| Credentials revoked or scope denied | “Reconnect to your desktop to get lyrics”; Server settings |
| Revision unavailable or lacks lyrics snapshot | “Prepare and import a new desktop catalog to get lyrics” |
| Invalid/oversized reply or timeout | Recoverable error and Retry; keep valid saved text |

Use TalkBack labels, 48dp action targets and scalable text. Review long filenames,
large fonts, RTL lyrics, missing artwork, both palettes and light/dark appearance.
No keyboard, wake lock or keep-screen-on setting is introduced.

## 3. Matching and ownership

Use the existing trusted mapping pipeline (`JournalIdentityProvider` and
`portable/MatchingRepository`), which applies the conservative matching policy.
Only a currently valid `confirmed` or `high_confidence` match may select desktop
lyrics. Never search by filename/title or choose the first ambiguous candidate.
Opening the sheet does not hash music or start matching automatically.

Capture server identity, catalog ID, active full-manifest hash/revision, local
song identity, current mapping generation and portable song ID for each request.
Before display or persistence, verify they still describe the current song and
active mapping. Drop late replies after a song change, rematch, rescan invalidation,
catalog adoption, unpairing or server change. A return to the same song must still
use the current request generation. Do not send local URIs, paths or music bytes.

## 4. Mobile contract

The pre-checkpoint gateway advertises connected recommendations/tuning but has no
lyric route. The catalog metadata allowlist also excludes lyrics. Add an optional
prepared lyrics resource without changing catalog schema v1 or placing long text
inside every manifest.

### Publication

Extend explicit desktop catalog preparation to snapshot existing `songs.lyrics`
for that catalog's songs into a private revision sidecar. Read lyric text and song
metadata from the same SQLite snapshot. Bind the sidecar to catalog ID, revision
and full-manifest SHA-256; publish it before exposing the prepared marker. Never
serve a half-written sidecar or mutate an already published revision.

Lyrics-only changes must result in a new prepared revision even when recommendation
metadata/vectors are unchanged; reuse unchanged vector blobs. The implementation
must account for this in the export revision decision. Keep the optional sidecar
out of existing manifest hash/schema validation so older clients remain compatible.
Old prepared revisions remain usable for ordinary catalog/vector reads. They must
not silently acquire lyrics from a newer revision. They can return
`lyrics_not_prepared`; preparing/importing a new revision enables the feature.

GETs only read prepared data. They never export, ingest, call models, read audio,
or invoke maintenance scripts. Read the existing lyric field without guessing
its external source or claiming it has been verified. Normalize CRLF/CR to LF and
trim surrounding whitespace; preserve internal line/stanza breaks and section
labels. Blank/null text is unavailable. Do not classify missing text as
instrumental. Oversized text is unavailable with reason `too_large`, never cut off.

### Capability and request

The gateway's authenticated `/api/portable/v1/capabilities` adds
`plain_lyrics: true`, `plain_lyrics_version: 1` and
`plain_lyrics_max_text_bytes: 65536` only when the implementation is installed.
Capability availability does not promise that an older prepared revision has a
sidecar. Missing/false capability is a supported old-server state.

```text
GET /api/portable/v1/lyrics?revision=R&manifest_sha256=H&song_id=S
Authorization: Bearer <existing device token>
```

Require `catalog:read`, since this exposes read-only metadata for an already
authorized catalog. No profile or recommendation permission is needed and no
credential rotation is required. Keep the gateway's identity/endpoint lease,
HTTPS policy, remote-host admission, revocation checks and exact route allowlist.
There is no mobile access to `/api/songs` or `/lyrics-timing`.

Accept exactly these three ordered query fields; reject duplicates, extra fields,
invalid escaping and bodies. `R` is a positive revision, `H` a lowercase SHA-256
of the full manifest, and `S` an existing portable song ID in that revision: 1–128
ASCII letters, digits, underscores or hyphens (desktop database IDs are decimal
strings). Cap raw query bytes at 1,024. Never interpret IDs
as filesystem paths. Only GET is allowed; no ranges or redirects.

An available response is JSON:

```json
{
  "schema": "radio.portable.lyrics",
  "version": 1,
  "catalog_id": "<catalog-id>",
  "revision": 12,
  "manifest_sha256": "<full-manifest-sha256>",
  "song_id": "42",
  "status": "available",
  "text": "<plain text with newline characters>",
  "text_sha256": "<sha256 of normalized UTF-8 text>"
}
```

`status: unavailable` returns the same identity envelope, a reason of `missing`
or `too_large`, and null `text`/`text_sha256`. This is HTTP 200, distinct from a
transport failure. Display text as text, never HTML/Markdown or external links.

Enforce 65,536 UTF-8 text bytes and 512 KiB total response bytes on server and
client, including chunked or decompressed bodies. Check identity fields, schema,
version, status and digest before saving. Use `Cache-Control: no-store` for HTTP
intermediaries; the app's deliberate private cache is managed separately.

| HTTP outcome | Meaning |
| --- | --- |
| 400 | Invalid query/request shape |
| 401 / 403 | Existing authentication, scope or admission failure |
| 404 | Revision/song missing, or `lyrics_not_prepared` (stable error code) |
| 409 | Full-manifest hash does not match the named revision |
| 503 | Prepared storage temporarily unavailable |

Bound phone work to one lyric request at a time and 15 seconds total including
body reading, using the existing trusted connection transport. Cancel when hidden;
Retry is explicit. Never log bearer tokens, lyric bodies or local paths.

## 5. Private offline cache

Room v10 adds one `LocalLyricsCache` table and an explicit v9→v10 migration.
Store server identity, catalog ID, full-manifest hash/revision, portable
song ID, normalized text/digest and fetched/last-viewed timestamps. No destructive
fallback. Keep the key independent of a mutable endpoint URL so signed URL rotation
does not lose saved text.

Display a cache entry only through a currently valid local-to-portable mapping
for the same server/catalog/revision. Never carry text across a replacement file,
rejected match or newly adopted revision. Saved text remains usable offline when
the stored pairing and mapping are valid; an explicit unpair/known revocation
clears that server's cache. Transient connection failures do not clear it.

Persist only successful nonempty validated text. Keep an unavailable result in
sheet memory to avoid repeated requests while open; do not persist transient
errors or negative results. Keep at most 100 entries and 5 MiB of text per
installation; evict least recently viewed entries transactionally, preserving the
currently displayed item. Reclaim obsolete revision entries after catalog adoption.
Cache failure must not prevent reading the fetched text or playing music.

## 6. Implementation and review sequence

The user approved implementation November 14 after review of the sheet hierarchy,
fetch-on-open behavior, catalog-bound publication, scope reuse, offline cache and
migration. Backend, Android data and Android UI work were developed on a separate
branch; this primary agent reviewed and verified the integrated result before the
user authorized the commit and merge.

Use Red/Green TDD at each behavior boundary:

| Step | Work | Required evidence |
| --- | --- | --- |
| 1 | Prepared snapshot and read service | Failing tests first: exact song/revision binding, lyrics-only new revision, empty/oversized/Unicode text, atomic failed publication, old revision behavior |
| 2 | Gateway contract | Scope/auth/revocation/lease tests, malformed query/method rejection, response bounds, forbidden legacy routes and no export/model/audio work |
| 3 | Android repository and Room v10 | Mapping/ownership fences, integrity/size limits, timeout/cancellation, cache bounds, URL rotation, offline success and migration preservation |
| 4 | Lyrics sheet and entry points | Radio/normal/queue/minimal reachability, states, scroll/reset/back, song-change races and accessible controls |
| 5 | Full verification and phone build | Guarded backend suite, Android unit/instrumented/lint/debug builds; frontend suite/build if desktop preparation UI code changes |

Likely source areas: `backend/mobile_catalog.py`, `backend/mobile_gateway.py`, the
existing export adapter in `backend/portable_server.py`, a small dedicated lyrics
read service; Android `portable/`, `connection/`, `data/LibraryDatabase.kt`,
`ui/RadioApp.kt`, and a dedicated lyrics sheet. Verify adapter snapshot/publication
boundaries before coding; keep the optional resource distinct from vector storage.

All generated fixtures/results stay in `.test-runtime/`. Use synthetic lyric text
and disposable databases. Do not run the production app or prepare the actual
library as an automated check. Tests must prove opening/refreshing/dismissing
lyrics creates no playback start, feedback event, queue edit, seek or sync.

## 7. Phone acceptance

1. Update without clearing app data; verify existing library, playlists, saved
   queue, feedback and paused restoration survive the migration.
2. Explicitly prepare/import a catalog with lyrics; open a matched song's lyrics
   from Radio and normal playback, including queue/minimal views.
3. Check long text, stanza breaks, selection, large fonts, light/dark and both
   palettes. Return to the prior player/queue position with Back.
4. Advance rapidly between two songs while a request is pending. The heading and
   lyrics must always belong to the same current song.
5. Reopen saved lyrics with desktop unreachable, then try an uncached song.
   Music must continue and each state must explain what is available.
6. Try unmatched/ambiguous songs, missing desktop lyrics, an old server and an old
   catalog. Follow Matching/Server recovery links without replacing playback.
7. Change a match/import a new revision; old text must not appear under the new
   identity. Reconnect after signed endpoint rotation and verify cache reuse.
8. Check Next, seeking, background audio and the sleep timer after dismissing the
   sheet. Lyrics must not create listening starts or change station behavior.

Deliver an installable APK, verification summary and known limits; stop for phone
acceptance before statistics or synchronized lyrics. Existing desktop lyrics may
be incomplete or inaccurate; this increment displays stored text and supplies no
new correctness guarantee.

## 8. Implementation and verification — November 14, 2026

Parallel Luna agents implemented backend, Android data and Android UI work. The
primary agent reviewed the integrated source, corrected issues found during review,
ran verification, and inspected actual emulator captures.

- Backend: the catalog adapter captures normalized lyrics from the same SQLite
  rows as metadata. Immutable `lyrics/index.json` and bounded per-song JSON files
  are published with each revision. Lyric-only changes advance the revision;
  schema v1 manifests omit the text and reuse unchanged vector blobs. The gateway
  exposes the authenticated, exact-query read contract above.
- Android: `data/LyricsRepository.kt` owns validated reads, mapping/owner/request
  fences, cancellation, a cumulative 15-second fetch deadline and private caching.
  Cached text survives endpoint rotation and process counters, while unpairing or
  known revocation purges it. Catalog adoption reclaims obsolete revisions. Room
  v10 and migrations preserve existing library, history, playlists, bookmarks and
  sync ownership/registers/cursors.
- UI: all Now Playing variants expose Lyrics. The expanded sheet preserves stanza
  breaks, allows text selection, keeps Refresh visible and supplies Server/Matching
  recovery actions. Foreground/page/song changes own the request lifecycle. A
  screenshot review caught the original partial-height sheet hiding its actions;
  the expanded state and action-visibility assertions correct that behavior.
- Final review also found a late-401 race in the shared connection helper. Two
  failing regressions reproduced restoration/overwriting of a forgotten or replaced
  pairing. The helper now verifies the current owner and endpoint before blocking
  credentials or purging lyrics. Initial mapping observation was also serialized
  through one watcher to avoid losing a change between startup and subscription.

| Verification | Result |
| --- | --- |
| Guarded full backend suite | 928 passed, 9 expected skips (fixture availability, Windows symlink privileges and opt-in native VLC) |
| Final backend catalog/lyrics regressions | 25 passed; includes test-only additions after the full run |
| Android JVM suite on final code | 150 passed |
| Full API 37 emulator suite | 214 passed, zero failures/skips |
| Final affected emulator classes after ownership/observation fixes | 25 passed, including both new late-401 regressions, pairing, cache, UI and repository races |
| Final direct UI/capture run | 4 passed; actual Ocean/light and Teal/dark captures reviewed |
| Android lint | Zero errors, 54 warnings; existing warning count retained |
| Debug app and test APKs | Built and installed on the isolated emulator |

The full 214-test run preceded the last connection-ownership and observation
fixes; the final 25-test affected-class run, JVM suite and lint verify those fixes.
No frontend source changed, so its suite/build was not required. Initial attempts
also exposed test compile issues and a lint parser crash while a test file was
being corrected; the stable final run passed without suppressing lint checks.
Guarded backend gateway tests required execution outside the Windows sandbox for
asyncio socketpair; storage and external-service guards stayed enabled.

Artifacts and logs: `../.test-runtime/android-lyrics/`. Installable APK:
`radio-checkpoint5-debug.apk`, SHA-256
`DFA464519C0A3108996F76A384B7494CE23C59CDF205D289862C58807EB1B871`.
The archived XML reports distinguish full and final affected-class runs. Final
captures are `final-captures/lyrics-ocean-light.png` and
`final-captures/lyrics-teal-dark.png`.

Phone acceptance remains the checklist in §7. Restart the desktop backend on this
code, explicitly prepare/import a fresh catalog, and refresh matching if needed
before checking lyrics. The old catalog is still usable for playback and
recommendations, but does not acquire a lyrics snapshot retroactively. No physical
phone, production library, database or metadata was changed by automated checks.
