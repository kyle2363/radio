# Checkpoint 7 — playlist sync, listening stats, sync on open

Status: planned November 17, 2026. Not started. Branch: `android-7-playlist-sync`
from `main` after checkpoint 6 merges.

Scope is deliberately three increments. Each lands with its own tests and phone
check list; a passing build is not approval for the next one.

## 7A — Playlist sync (Android playlists ↔ desktop collections)

Two-way sync between Android `LocalPlaylist`/`LocalPlaylistSong` and desktop
`collections`/`collection_songs`, carried over the existing manual profile sync
(`docs/MOBILE_SYNC.md`, `docs/PORTABLE_SYNC_API.md`).

- **Identity.** Each synced playlist gets a stable UUID shared by both sides.
  Members are referenced by catalog song ID; Android maps through saved
  `PortableMatchDecision`s. Unmatched local songs stay in the phone playlist and
  are reported as "phone only", never dropped.
- **Opt-in per playlist.** Existing playlists stay local until marked "Sync".
  Desktop collections appear on the phone only after the user picks them.
- **Events, not snapshots.** Create, rename, delete, add, remove and reorder are
  immutable events in the existing outbox with Lamport clocks. Membership uses
  add/remove sets (add wins over a concurrent remove); order uses fractional
  position keys, ties broken by device ID. Rename is last-writer-wins.
- **Delete is soft.** A remote delete unlinks the playlist and asks before
  removing it locally. No audio, tags or desktop song rows are ever touched.
- **Playback independent.** Sync never edits an active queue; queues remain
  snapshots.
- **Storage.** Room v11: sync UUID, position key and tombstone columns, plus a
  playlist event table. Explicit `v10→v11` migration, exported schema, migration
  test with existing playlists. Desktop: additive columns on `collections` /
  `collection_songs` after a verified backup.
- **Server.** New `/api/portable/v1/playlists` event endpoints behind the
  existing profile-sync permission; reference merge in
  `backend/portable/playlists.py` with golden fixtures shared with Android.

Tests: merge golden parity (Python + JVM), concurrent edit conflicts, unmatched
members, migration, process loss mid-sync, permission revoked.

Phone checks: sync a phone playlist to desktop; edit it on desktop and sync
back; reorder on both sides before syncing; delete on one side.

## 7B — Listening stats (Android, local only)

A Stats view in Settings (or Library) computed from the local journal: top songs
and folders by week/month/all time, listening time, skip rate, mood/Radio share,
and a simple daily listening chart. Read-only queries over existing tables; no
schema change, no network. Includes synced remote events when present, labelled.

Phone checks: numbers match a few known plays; view stays fast with full history.

## 7C — Sync on open

Optional (default off) automatic run of the existing manual sync when the app
comes to the foreground and the paired server is reachable, at most once per
15 minutes. Foreground only: no WorkManager, background polling or playback
impact. Failures are silent apart from the existing status row.

Phone checks: enable, open the app with the server up and down, confirm play is
never delayed.

## Deferred

Synchronized lyrics, speech, crossfade, downloads and background sync.
