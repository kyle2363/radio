# Portable catalog and manual profile sync, version 1

This protocol prepares Radio for independently operating clients. It does not
transfer active playback, implement Android, upload music, or delete local media.
The implementation is `portable_api.py`, `portable_server.py`, and
`portable_sync.py`. Run one API worker per instance, as for existing playback.

## Authentication

Existing cookies and trusted-loopback policy still protect administration.
An authenticated administrator calls `POST /api/portable/v1/devices` with
`{"name":"My device","scopes":["catalog:read","profile:read","profile:write"]}`.
The response returns a device ID, expiry and random bearer token **once**. Store
the token privately on the client and send it in `Authorization: Bearer ...`.
The server persists only its SHA-256 digest. `DELETE /devices/{device_id}` revokes
it; bearer tokens cannot provision/revoke tokens or control existing playback.

Scopes are `catalog:read`, `profile:read`, `profile:write`, and reserved `dj:read`.
Native requests can omit Origin; explicit browser origins and Fetch Metadata
continue through existing checks. Host validation remains mandatory. Tokens work
only within `/api/portable/v1/`. They do not authorize arbitrary library, settings,
playback, WebSocket, or legacy media routes. Unrecognized or revoked credentials
do not bypass remote authentication. Use the existing TLS/deployment policy;
this change does not enable a LAN/public listener.

## HTTP surface

All paths below are relative to `/api/portable/v1`.

| Method/path | Scope | Result |
| --- | --- | --- |
| GET `/capabilities` | Any authenticated device | Protocol versions and limits |
| GET `/catalog` | catalog:read | Current complete immutable catalog manifest |
| GET `/catalog?revision=N` | catalog:read | Previously published revision |
| GET `/catalog?revision=N&base_revision=M` | catalog:read | Exact-base incremental manifest |
| GET/HEAD `/vectors/{sha256}` | catalog:read | Little-endian vector bytes; immutable ETag |
| GET `/events?cursor=N&limit=100` | profile:read | Revision-ordered events; next cursor and has_more |
| POST `/events` | profile:write | Transactional import and accepted/duplicate event IDs |
| GET `/profile` | profile:read | Materialized shared mutable preferences |
| POST `/devices` | Existing admin authentication | Issue scoped device token |
| DELETE `/devices/{id}` | Existing admin authentication | Revoke token |

Catalog manifests include all metadata for a snapshot; deltas omit unchanged
vector bytes and identify an exact base revision/checksum. Follow
[PORTABLE_CATALOG.md](PORTABLE_CATALOG.md) for validation. Download to staging,
validate every byte and manifest, then atomically adopt the new revision. A failed
download never advances the local catalog revision. Blob requests can be retried
independently; old immutable revisions remain available. Removed server records
do not delete device audio. The server reuses unchanged blobs through hardlinks
in its generated export directory; original vectors and media are untouched.

Export reads existing collections in batches of known catalog IDs. It never
calls embedding models or creates a missing collection. A missing collection is
valid; a storage error or collection-count change aborts export. SQLite metadata
and Chroma are separate stores, so export after ingestion finishes. Simultaneous
same-count vector replacements are not a transactional cross-store snapshot.
Published packages are nevertheless complete and checksum validated.

## Event envelope

POST body is `{"protocol_version":1,"events":[...]}`. A native token fixes the
origin device; a caller cannot submit another device's events. Existing admin
callers additionally supply `device_id` in the body. Each event contains:

```json
{
  "protocol_version": 1,
  "device_id": "assigned-device-id",
  "event_id": "stable-device-event-id",
  "logical_clock": 42,
  "catalog_id": "source-library-uuid",
  "song_id": "17",
  "attempt_id": "local-playback-attempt-id",
  "occurred_at": 1793220000,
  "kind": "song_completed",
  "completion": 1
}
```

`catalog_id` plus `song_id` identifies a server track; a different library is
rejected even when its row IDs coincide. Phone-only tracks use `song_id:null` and
an opaque `local_id`; their events survive without guessing a server mapping.
Local-only profile keys use `local:` followed by the canonical JSON array
`[device_id,local_id]`; clients apply that namespace only to its owning device.
Identical opaque filenames/IDs on separate devices never imply a shared recording.
No media path is an event identity. Event IDs and attempt IDs are 1–128 characters.
Events for a removed track in the correct source catalog remain in the immutable
ledger; absent relational song rows simply receive no counter/history projection.
Kinds are `song_started`, `song_completed`, `song_skipped`, `song_replayed`,
`favorite_changed`, `dislike_changed`, `blacklist_changed`, `energy_changed`, and
`preference_changed`. Preparation, decoding and prefetch are not accepted events.

Playback events require `attempt_id`. Actual starts increment play count once per
device/attempt. A finish is completed or skipped, never both. Completion may
arrive before its start. Distinct replays remain distinct events. Optional skip
`reason` uses the existing five reason names. Boolean changes carry `value`;
energy accepts a value in [0,1] or null to clear. Shared defaults currently accept
only `exploration_level` and `llm_reranking_enabled`. They affect future sessions;
active mood, trajectory, queue, current song, position, DJ and timers stay local.

Every event identity is immutable. Same identity/same payload is an acknowledged
duplicate; same identity/different payload is HTTP 409. Entire batches commit or
roll back together, including history, counters, mutable projections and receipts.
Imports are limited to 500 events, 4 KiB per event, and 2 MiB per HTTP body. Server
history and imported history are tracked separately to prevent echo duplicates.

## Ordering, retries and conflicts

Persist a device Lamport clock. After reading a page, advance it to at least the
returned `logical_clock`, then increment it for each new local event. Mutable
winners use lexicographic `(logical_clock,device_id,event_id)` ordering. Wall time
is diagnostic/listening history time and never determines the mutable winner.
Concurrent same-clock edits therefore have a stable documented tie-break; all
unique events remain in the ledger for diagnostics. A stale offline action can
lose to an action made after a later observed clock. This is deliberately a small
event protocol, not a general distributed database.

Keep the outbox until accepted or duplicate IDs are acknowledged. If an ACK is
lost, resend exactly the same events. Split arbitrarily long offline outboxes into
batches of at most 500 and persist acknowledgements after every successful batch.
Keep the download cursor separate from the
import response cursor: an import ACK does not prove all prior remote events
have been downloaded. Page until `has_more` is false; adopt each page's returned
cursor only after local persistence. No automatic sync is scheduled.

Before serving/importing a manual sync, the server captures accepted desktop
history and changed mutable state. Its additive schema is installed lazily after
a verified SQLite backup; the schema and source-library UUID commit together.
Unchanged profile captures do not rewrite thousands of rows. Imported positive
feedback enters desktop taste queries through a narrow shared-history option and
invalidates cached vectors; ordinary session-specific history APIs stay scoped.

The desktop history predates per-attempt portable IDs. Legacy exported start/end
rows retain distinct `legacy:rowid` attempts rather than inventing correlations.
An undo performed before first export is excluded. Undo of an immutable skip
already downloaded is **not yet synchronized as a retraction**; clients must not
claim this capability. Favorites/blacklist/energy changes do converge through
materialized registers. Device data removal, token rotation UI, export retention,
and automatic sync remain follow-up work. Events for newly matched phone-only
tracks are not retroactively projected onto a guessed server song.

## Errors and verification

400: invalid protocol, identity, catalog or value; 401: missing/expired/revoked
remote authentication; 403: origin or scope denied; 404: absent revision/blob;
409: conflicting event identity/attempt; 413: oversized body; 422: malformed HTTP
schema. Storage/export failures fail the request without publishing a new catalog.
Do not advance client cursors or overwrite a valid local catalog on failure.

`backend/tests/portable_client_simulator.py` imports only the public portable
package and exchanges data through HTTP. Its integration test downloads vectors,
matches renamed songs, learns while disconnected, retries a lost acknowledgement,
preserves local queues and includes phone-only tracks without vectors. All data
is disposable under `.test-runtime/`.
