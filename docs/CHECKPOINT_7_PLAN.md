# Checkpoint 7 — playlist sync, listening stats, sync on open

Status: 7A merged into `main` from `android-7-playlist-sync`; 7B moved to checkpoint 8 (done); the desktop
"Sync to phone" toggle and 7C sync on open were finished November 27, 2026 on `finish-open-items`. Awaiting phone test
(including `connectedDebugAndroidTest` for 7A on a device).

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
- **Server (done November 17, 2026).** No new endpoints: three event kinds ride
  the existing `/events` exchange and outbox — `playlist_renamed` (value: name,
  1–80 chars), `playlist_deleted` (value: bool) and `playlist_member_changed`
  (`song_id` or `local_id`, value: present bool, `position` key). Each needs a
  UUID `playlist_id`. Registers are last-writer-wins on (clock, device, event id);
  `GET /profile` returns `playlists: {id: {name, deleted, members[]}}`.
  Capabilities advertise `playlist_schema: 1`. Rules and position keys live in
  `backend/portable/playlists.py`; parity cases in
  `backend/tests/fixtures/playlist_golden.json`. Desktop collections opt in via
  `PortableProfileStore.share_collection` (UI pending); linked collections are
  rebuilt from registers after import and diffed at capture, so imports never
  echo. A remote delete unlinks and keeps the desktop collection.
  Desktop toggle (November 27, 2026): Library → Collections → a collection → **Sync to phone**
  (`PUT /api/collections/{id}/sync`); synced collections show "on phone" in the list. **Stop syncing** on the desktop
  marks the link unshared (`portable_playlists.deleted = 2`): phone edits stop reaching the collection and desktop edits
  stop being exported, nothing is deleted, and sharing again keeps the playlist id (the desktop's copy wins).
- **Android (implemented November 17, 2026; not phone-tested).** Room v11 adds
  `synced`/`remoteDeleted` to playlists and `positionKey` to entries
  (`LIBRARY_MIGRATION_10_11`). `PlaylistSyncRules` mirrors the server rules and
  reads the same golden file. Playlist menu: **Sync with desktop** / **Stop
  syncing** (shown once paired). Edits to a synced playlist queue only the changed
  members in the profile outbox; **Sync now** merges them with downloaded events
  and rebuilds synced playlists. Desktop-shared playlists appear under **From your
  desktop** with **Add**. A playlist deleted elsewhere shows a card to remove it or
  keep it locally. Older apps reject playlist events, so update the APK before
  sharing a desktop collection. `connectedDebugAndroidTest` not yet run (no device).

Tests: merge golden parity (Python + JVM), concurrent edit conflicts, unmatched
members, migration, process loss mid-sync, permission revoked.

Phone checks: sync a phone playlist to desktop; edit it on desktop and sync
back; reorder on both sides before syncing; delete on one side.

## 7B — Listening stats (Android, local only)

> Moved November 17, 2026: superseded by checkpoints 8A–8C in
> `docs/CHECKPOINT_8_PLAN.md`, which first records listening context (Room v12).
> The text below is kept for history.

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

As built (November 27, 2026): Settings → Server → Sync → **Sync when the app opens** (default off, `sync_on_open` in
`radio_shell`). `data/SyncOnOpen.kt` decides on resume after pairing and discovery refresh: paired, loaded, not
pending/quarantined/blocked/busy, no sync running, and at least 15 minutes since the last attempt (a clock moved
backwards counts as due). It runs the same `syncNow()`; errors show only in the sync status row and trigger a discovery
re-read. Unit-tested in `SyncOnOpenTest`.

## Deferred

Synchronized lyrics, on-phone speech, downloads and background sync. (Crossfade shipped later in `playback/Crossfade.kt`;
connected talk uses desktop voices, checkpoint 13.)
