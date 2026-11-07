# Checkpoint 4 — connected recommendations plan

Date: November 7, 2026 (revision 2). Branch: `android-4-connected-recommendations`, cut from `main` at `938ba35`.
Status: **implemented November 7, 2026; awaiting phone acceptance (§5).** Decisions: Q1 current-song anchor, D1 (a), D2 (a), D3 (a). Routes are POST-only (§8).

Source contracts: [MOBILE_CONNECTION_PLAN.md](MOBILE_CONNECTION_PLAN.md) §5 (API sketch), §6 (connected AI rules) and §11 (checkpoint 4 gate). Differences from that sketch are listed in §3.5.

Revision 2 follows user review:

- **Slow server.** Server replies take 20–30 s because of the LLM, so a short deadline would almost never get an LLM result.
- **Different queue models.** Desktop and phone Radio deliberately behave differently. The phone continues with similar songs. The desktop offers a varied set of five options per song and auto-plays the first.
- **Similar picks are auto-added; the rest are offered.** Only the desktop's `exploit` (similar) picks are added to the phone's queue automatically. `explore`, `wildcard` and `context` picks are suggestions the listener chooses from, never auto-queued.
- **Hidden when unavailable.** The options card is hidden when the server is unavailable. There are no phone-generated options.

## 1. Goal

When the phone can reach the paired desktop server, it asks the desktop's full recommender for one option set per song. The recommender covers taste memory, Chroma retrieval, slots, the LLM reranker and transitions. The phone uses that set in two ways:

1. **Continuation.** The `exploit` picks feed the existing phone Radio refill ahead of offline picks, so the phone's "similar next song" behavior is kept but ranked by the desktop.
2. **Options.** The `explore`, `wildcard` and `context` picks appear in an **Up next options** card with their reasons. Tapping one makes it play next. Ignoring them changes nothing.

The phone remains the only owner of its playback. Without a usable server reply, phone Radio behaves exactly like checkpoint 2B and the options card is hidden.

Acceptance gate from §11: availability snapshots, independent context, shared ranking reuse, LLM fallback, stale-response fencing, no double history. Late responses or reconnection never replace or reorder committed/manual queue entries and never change the current song or position. Refill stays explicit and bounded. Music-quality and simultaneous desktop/phone acceptance happen on the real phone.

### Out of scope

- Speech, DJ announcements and talk shows (5A/5B).
- Automatic profile sync. Manual sync (2C) stays the way phone history reaches the shared profile.
- A broader Radio UI redesign. This checkpoint adds one compact card. A later redesign can revisit it after real use, for example lock-screen choice or a different Radio layout.
- Choosing options from the notification or lock screen. With the phone in a pocket, playback simply continues.
- Treating a chosen option as a taste signal beyond its normal actual-start/completion events.
- Any change to desktop playback, desktop queue, desktop sessions or `/ws/events`.
- Streaming audio from the server. Every suggestion must be a song the phone already has.
- New Android dependencies.

## 2. Current state this builds on

| Piece | Where | Relevance |
| --- | --- | --- |
| Desktop pipeline | `engine/queue.py` `_rebuild_preview` (~L575–1020) | Retriever → slots → reranker → transitions run inline in `QueueManager`, reading desktop settings, mood, energy trajectory, skip streak, vibe lock and `_recently_recommended_ids`. It builds five options for "after the current song" and auto-plays the first. Must be extracted, not called. |
| Slots | `engine/slots.py` → `portable/slots.py` `allocate_ranked_slots` | Normal: 2 `exploit`, 1 `explore`, 1 `wildcard`, 1 `context`. Vibe lock: 4 `exploit`, 1 `explore`. |
| Reranker | `engine/reranker.py` `rerank`, `fallback_assignment`, `deterministic_assignment` | Returns `{id, slot, reason}` picks. Has a deterministic fallback. |
| Retriever | `engine/retriever.py` `build_candidate_list` | Explicit vectors, mood ranges, exclusions, filters and vibe lock. No allow-list parameter yet. |
| Shared taste | `engine/taste.py` + `portable_sync.py` projection | Phone events uploaded by manual sync are already projected into `play_events`/`blacklist`. |
| Gateway | `backend/mobile_gateway.py` | Exact route allow-list, scoped device bearers, body limits, per-device rate limits, USB and remote ingress. |
| Phone Radio | `recommendation/RadioRepository.kt`, `OfflineRecommender.kt` | `RadioInputToken` fences a batch against library, mappings, feedback, mood, exploration, vibe, current song, queue revision, play order and sync epoch. |
| Phone refill | `playback/RadioPlaybackService.kt` L81–104 | When Radio is active, playing and ≤2 entries remain, it appends up to 5 − upcoming offline picks through `engine.appendRadio`. |
| Mappings | `JournalIdentityProvider.MappingSnapshot`, `MatchingRepository.kt` | Local song ⇄ server song ID; the availability set comes from here. |

## 3. Design

### 3.1 Timing: one option set per song, prepared early

The desktop already builds its option set for "after the current song" while that song plays. The phone does the same:

- **Request at song start.** When a new song actually starts while Radio is active and Connected Radio is on, the phone requests an option set anchored on that song. Exclusions are the current song, everything queued and recently played songs. At 20–30 s for a reply and ~3–4 min per song, the result is normally ready with minutes to spare.
- **No waiting on playback.** Nothing ever waits on the network to keep music going.
- **Deadlines.** Server work stops at **60 s**. The phone's HTTP timeout is **75 s**, which stays under Cloudflare's ~100 s proxied response limit for Quick Tunnel use.
- **Expiry.** A set is valid only while its anchor song is current and its fences match (§3.4). When the song changes, the old set expires, its card disappears and a new request starts.
- **Rapid skipping.** Each device has at most one running and one pending request on the server. A newer request replaces the pending one. The running LLM call cannot be cancelled, so its result is discarded by the phone if it is stale.

### 3.2 Refill: exploit picks first, offline as the safety net

The existing refill trigger (Radio active, playing, ≤2 upcoming, no sleep timer) and its 5-entry bound stay. Only the choice of source and its timing change:

- **Connected set ready.** If a valid set for the current song exists, its unused `exploit` picks fill the refill first: up to 2 normally, up to 4 under Vibe lock, never more than the 5 − upcoming bound. Offline tops up any remaining room, as today.
- **Connected set still pending, at least 1 upcoming entry.** Refill waits for the set, but at most until the deadline, then uses offline. Upcoming entries mean several minutes of music remain, so the wait is harmless. In the common case, the request at song start and the refill trigger (upcoming 3 → 2) happen together. This short wait is what lets connected picks be used at all.
- **No upcoming entries** (the current song is last). Offline refills immediately, as today. A connected set that arrives later still shows its options card. Its exploit picks are not appended, because the queue was already refilled.
- **Connected unavailable, off, failing or not permitted.** Behavior is exactly 2B.

Exploit picks are anchored on the current song but appended after the upcoming entries. That matches how offline refill already anchors on the queue tail closely enough for a first version. Anchoring on the tail instead is a tuning question for phone acceptance (§7, Q1).

### 3.3 Options card

- **What it shows.** It appears on the Radio screen and in Now Playing only when a valid connected set exists. It shows the non-exploit picks (`explore`, `wildcard`, `context`; `explore` only under Vibe lock), each with artist/title, a small slot label and the server's reason.
- **Play next.** Tapping an option inserts it directly after the current song through the existing queue-edit path. It is a manual queue entry, not a Radio entry: refill never removes it and later results never move it. The option disappears from the card.
- **Ignoring it.** Ignoring the card changes nothing. Playback continues with whatever is queued, just as the desktop auto-chooses its first (similar) pick.
- **Hidden.** The card is hidden when Connected Radio is off, the server is unreachable, a request is pending, a set has expired, or every option was filtered out (unmapped, unavailable, disliked or already queued). There are no phone-generated options.
- **No history.** Choosing an option records nothing by itself. Its play counts once, when it actually starts (2A journal), and reaches the desktop through manual sync.

### 3.4 Backend

**a. Extract `engine/recommendation_service.py`.** A pure orchestration function with every input explicit:

```
recommend(RecommendationInputs) -> RecommendationResult
  inputs: taste/repulsion vectors, anchor song id, mood state + ranges, target energy,
          exploration, vibe lock, exclusions, allowed_ids (optional), filters,
          reranking_enabled, deadline
  result: picks [{song_id, slot, reason}] in slot order, ranked_by ("llm" | "fallback" | "deterministic")
```

`QueueManager._rebuild_preview` builds those inputs from its own state and calls this function. Generation checks, broadcasts, talk/announcement scheduling and `_recently_recommended_ids` stay in `QueueManager`. Unchanged desktop tests guard the extraction.

**b. Availability in retrieval.** Add `allowed_ids` to `build_candidate_list` and every search it makes (text, audio, centroid, neutral, vibe-lock and the top-up search). Use a Chroma `where` restriction when practical, otherwise over-fetch and filter with bounded widening. Apply it again to reranker output and every fallback, so a partially matched phone library still yields a full slot set.

**c. `backend/mobile_recommendations.py`.** In memory, with no new tables:

- **Availability snapshots.** Catalog ID/revision, mapping revision, sorted server song IDs and the SHA-256 of the canonical list. The phone uploads only when the digest changes. Unknown IDs are rejected.
- **Contexts.** At most 2 per device, 30-minute idle expiry and a monotonic `epoch` bumped on every PATCH. Each holds mood, exploration, Vibe lock song (server ID, resolved to embeddings server-side) and the phone's recent-session list (≤20 server IDs with `completed`/`skipped`). The list shapes this request's immediate/session taste and exclusions only. It never writes `play_events`.
- **Taste.** The shared long-term/daily profile from `taste.py` plus the context's session list. The desktop listener's session memory is left untouched.
- **Work admission.** One running and one pending request per device; a newer pending request replaces the older one. LLM calls share a priority gate where desktop rebuilds go first. If the LLM is unavailable or the 60 s deadline passes, the deterministic fallback ranks the set, and `ranked_by` reports it.
- **Isolation.** Never calls `QueueManager`, `playback_sessions`, `MediaService`, the coordinator or `_broadcast`.

**d. Gateway routes.** New, explicit and scoped:

| Method/path | Scope | Notes |
| --- | --- | --- |
| `PUT /api/portable/v1/availability` | `recommend:read` | Idempotent by digest; body ≤ 256 KiB. |
| `POST /api/portable/v1/contexts` | `recommend:read` | Returns context ID + epoch. |
| `PATCH`/`DELETE /api/portable/v1/contexts/{id}` | `recommend:read`, owner | PATCH bumps epoch. |
| `POST /api/portable/v1/contexts/{id}/recommendations` | `recommend:read`, owner | Request: request ID, epoch, availability digest, catalog revision, anchor (current) server song ID, exclusions (≤200). Reply echoes every fence plus result ID, `ranked_by` and slot-tagged picks with reasons. A stale epoch, digest or revision returns 409 with the specific reason. A superseded request returns 409 `superseded`. |

Capabilities advertise `connected_recommendations: true` only once integrated. `recommend:read` is granted to an existing paired device by a desktop admin action, as profile sync was in 2C, with no re-pairing. A **Connected Radio** toggle is added to the device row in Electron Settings.

### 3.5 Android

- **`recommendation/ConnectedRadioSource.kt`** maintains and uploads the availability digest from `MappingSnapshot`, creates/patches the context, and issues the per-song request. It maps replies back to local songs and splits them into `continuation` (exploit) and `options` (the rest). It drops anything unmapped, unavailable, disliked, queued or current, and records a reason for every failure.
- **Fencing.** Each set carries the existing `RadioInputToken` plus `anchorLocalId`, `connectedEpoch` and `availabilityDigest`. Refill and the options card both go through `accept()`, so a set is unusable once its anchor, mood, vibe, mappings, catalog or sync epoch changed. Late sets are dropped, never merged.
- **`RadioRepository`** holds the current connected set and exposes it to refill and the UI. **`RadioPlaybackService`** refill gains the bounded wait from §3.2. Its trigger, bound and `appendRadio` path are unchanged. Manual queues, playlists and folder queues never turn into Radio and never get connected picks.
- **UI:** the options card (§3.3); a small "Server AI" marker on queue rows that came from connected exploit picks; and in Settings → Server, a **Connected Radio** toggle (default off, SharedPreferences), permission state ("Not permitted on desktop") and the last result/reason.
- **No Room migration.** Sets and contexts are ephemeral and recreated after process death. The journal/outbox are untouched.

### 3.6 Differences from the Phase 0 sketch

- **No `/commits` endpoint.** Exclusions in each request already cover queued and recently accepted songs. A commit record would add server state and a second place where double history could creep in.
- **The recent-session list travels in the context,** so picks follow what the phone just played without writing history.
- **Availability is `PUT` by digest,** so it is naturally idempotent.
- **Results are split by slot.** Only `exploit` picks may enter the queue automatically; other slots are opt-in options. The Phase 0 sketch treated all results as queue suggestions.
- **The deadline is 60 s, not a short timeout,** because results are prefetched per song instead of fetched at refill time.

## 4. Work order (Red/Green TDD throughout)

| Step | Work | Proof |
| --- | --- | --- |
| 1 | Extract `recommendation_service.recommend()`; `QueueManager` delegates | Existing queue/reranker/slots tests unchanged + pure-function tests with fake Chroma/LLM |
| 2 | `allowed_ids` through retriever, reranker output and all fallbacks | 10 % availability still fills every slot; no unavailable ID at any stage; empty allow-list → explicit `insufficient` |
| 3 | `mobile_recommendations.py`: availability, contexts, admission, deadline/fallback | 409 for epoch/digest/revision/superseded; slow fake LLM → `fallback` at 60 s; desktop priority; expiry; **`play_events`, `queue_history` and the desktop queue unchanged** |
| 4 | Gateway routes, `recommend:read`, admin grant, capabilities | Missing/expired/revoked/wrong-scope tokens, another device's context, path/method variants, body limits, USB and remote ingress |
| 5 | Electron device-row toggle | Frontend tests for grant/revoke IPC |
| 6 | Android `ConnectedRadioSource` + client calls | Fake-server JVM tests: slot split, mapping back, drops, 75 s timeout, every failure reason, digest upload only on change |
| 7 | Refill integration: exploit-first, bounded wait, offline top-up | Exploit used when ready; wait ends at deadline → offline; no wait when queue would run dry; late set after skip/mood/vibe/queue edit/mapping change discarded; bound never exceeded; manual queue never extended; current song/position unchanged |
| 8 | Options card, Play next, Settings toggle, source marker | Card hidden in every unavailable state; Play next inserts after current and survives later refills; existing Radio/Now Playing/Server UI regressions |
| 9 | Full verification + APK + phone acceptance list | See §5 |

## 5. Verification and acceptance

Automated, matching earlier checkpoints:

- Guarded backend suite: `backend\venv\Scripts\python -B scripts/run_tests.py -q`.
- Frontend: `npm test` and `npm run build`.
- Android: `testDebugUnitTest`, `connectedDebugAndroidTest` (API 37 emulator) and `lintDebug` with no new errors.
- Fixtures/logs in `.test-runtime/android-checkpoint4/`. Use synthetic catalogs and a fake LLM/Chroma with configurable delay. No production DB, no real library and no normal app start used as a fixture.

Phone acceptance list (drafted with the build):

1. With Connected Radio off, Radio behaves exactly as 2B and no options card appears.
2. Turn it on (USB, then remote). Within about 30 s of a song starting, the options card appears with 2–3 choices and reasons, all songs on the phone.
3. Later refills include "Server AI" similar picks. Ignoring the card never changes what plays.
4. Tap an option. It plays right after the current song, and later refills don't move it.
5. Turn Vibe lock on. Refills draw more server similar picks, and the card shows only the explore option.
6. Desktop plays its own Radio at the same time. Neither device's queue, song or position changes.
7. Stop the desktop backend mid-song. Music continues, the card disappears and refill falls back to offline.
8. Skip quickly through several songs. No stale card and no duplicate or out-of-order appends.
9. Run manual sync afterwards. Each play, including chosen options, appears once on the desktop.
10. Over ~30 minutes, judge listening quality and whether the options feel like the desktop's.

## 6. Risks

- **Extraction regressions.** `_rebuild_preview` is long and stateful. Only the pure ranking part moves; desktop tests are the guard.
- **Chroma allow-list.** Measure a `$in` restriction versus over-fetch on a synthetic 5k collection before choosing.
- **LLM contention.** One llama.cpp chat server is shared. Desktop rebuilds take priority; under load the phone more often receives `fallback`-ranked sets, reported in Settings.
- **Fast skipping wastes LLM work.** A running call can't be cancelled. One running plus one pending per device bounds the waste.
- **Stale long-term taste.** Phone listening reaches the long-term profile only after a manual sync. The per-request session list covers the current session.
- **Quick Tunnel limits.** The 60 s server deadline keeps responses under Cloudflare's ~100 s proxy limit. Restrictive networks still fall back to offline.

## 7. Open questions and decisions

Decided November 7, 2026:

- **Q1 — anchor for exploit picks: current song**, matching desktop options. Revisit during phone acceptance if refills drift; switching to the queue tail is a small change.
- **D1 — one checkpoint, one review at the end.**
- **D2 — phone requests follow the shared `llm_reranking_enabled` preference.**
- **D3 — Connected Radio is off until turned on in Settings.**

Rollback: turn Connected Radio off on the phone or revoke `recommend:read` on the desktop. Local Radio, the journal and sync are unaffected. No schema migration is planned, so reverting the code needs no data change.

## 8. Wire contract (frozen for implementation)

The existing Android `RadioTransport` sends only GET (no body) and POST (JSON body), so every new route is `POST` with `Content-Type: application/json`. This replaces the PUT/PATCH/DELETE forms in §3.4d. Contexts are not deleted explicitly; they expire after 30 minutes idle, and creating a third context for a device evicts that device's least recently used one.

Common rules: all bodies carry `"protocol_version": 1` and reject unknown fields. Song IDs are the catalog's string `songs[].id`. Every route requires a device bearer with `recommend:read`, otherwise 403 `"Device token lacks recommend:read scope"`. Another device's or an expired context returns 404 `"context_not_found"`. Errors use the gateway's existing `{"detail": ...}` shape; 409 bodies are `{"detail": {"reason": R}}` with R one of `epoch_stale`, `availability_stale`, `catalog_mismatch`, `superseded`, `no_availability`.

**Availability digest.** Lowercase hex SHA-256 of the UTF-8 bytes of the distinct song IDs sorted by their UTF-8 byte sequences and joined with `
` (no trailing newline). Both sides compute it the same way; the server rejects a mismatching digest with 400.

### `POST /api/portable/v1/availability` (body ≤ 256 KiB)

```json
{"protocol_version":1,"catalog_id":"…","catalog_revision":12,"song_ids":["…"],"digest":"<hex>"}
```

`song_ids`: 1–20000 distinct IDs. 400 for unknown IDs (not in the desktop library), digest mismatch or duplicates; 409 `catalog_mismatch` when `catalog_id` is not this server's catalog. Uploading the same digest again is a no-op. One snapshot is kept per device, replacing the previous one.
Reply: `{"digest":"<hex>","count":N,"catalog_id":"…","catalog_revision":12}`.

### `POST /api/portable/v1/contexts` (create) and `POST /api/portable/v1/contexts/{context_id}` (update; body ≤ 8 KiB)

```json
{"protocol_version":1,"mood":"neutral","exploration":0.3,"vibe_lock_song_id":null,
 "recent":[{"song_id":"…","outcome":"completed"}]}
```

`mood` ∈ `chill|neutral|energetic|focus|melancholy|discovery`; `exploration` 0–1; `vibe_lock_song_id` null or a known ID; `recent` ≤ 20 entries, newest last, `outcome` ∈ `completed|skipped`. `context_id` matches `[A-Za-z0-9_-]{16,64}`. Create returns epoch 1; each update increments it.
Reply: `{"context_id":"…","epoch":1,"expires_in":1800}`.

### `POST /api/portable/v1/contexts/{context_id}/recommendations` (body ≤ 16 KiB)

```json
{"protocol_version":1,"request_id":"<uuid>","epoch":3,"availability_digest":"<hex>",
 "catalog_revision":12,"anchor_song_id":"…","exclude_song_ids":["…"]}
```

`exclude_song_ids` ≤ 200. 409 `epoch_stale` (epoch ≠ context epoch), `availability_stale` (digest/revision ≠ this device's snapshot), `no_availability` (no snapshot yet), `superseded` (a newer request for this device replaced this one while pending, or arrived while it ran). The server spends at most 60 s; past that, or when the LLM is unavailable or reranking is disabled, picks are deterministic.
Reply (200):

```json
{"request_id":"…","result_id":"…","context_id":"…","epoch":3,"availability_digest":"<hex>",
 "catalog_revision":12,"anchor_song_id":"…","status":"ok","ranked_by":"llm","vibe_locked":false,
 "picks":[{"song_id":"…","slot":"exploit","reason":"…"}]}
```

`status` is `ok` or `insufficient` (no eligible available candidates; `picks` empty). `ranked_by` ∈ `llm|fallback|deterministic`. `slot` ∈ `exploit|explore|wildcard|context`; picks are in slot order, every pick is in the availability snapshot and not excluded, `reason` ≤ 200 characters. Normal sets aim for 2/1/1/1, Vibe lock for 4/1/0/0.

### Capabilities and admin

`GET /capabilities` adds `"connected_recommendations": true`. The desktop admin router (the one serving `POST /devices/{id}/profile-sync`) adds `POST /devices/{id}/connected-radio` (grant `recommend:read`) and `DELETE /devices/{id}/connected-radio` (remove it), and its device listing exposes scopes so Electron can show the state. Phone timeout for the recommendations call: 75 s; other new calls use the normal client deadline.

## 9. Implementation notes (November 7, 2026)

Steps 1–8 are implemented; step 9 phone acceptance is pending. Differences from the text above:

- **Extraction source.** The desktop pipeline lived in `QueueManager._rebuild_queue`, not `_rebuild_preview`. `engine/recommendation_service.recommend()` now holds retrieval through the final top-up; `QueueManager` keeps all state, broadcasts and scheduling.
- **Allow-list.** Chroma `where={"song_id": {"$in": [...]}}` pre-filter (≈16 ms at 5k IDs on a synthetic 5k collection), with bounded over-fetch (×1/×3/×8, ≤1000) when the filter is rejected or returns nothing. Chroma's `ids=` argument is not used because it fails on IDs missing from a collection.
- **Slot counts.** The shared allocator enables `wildcard` only above exploration 0.4, so the default 0.3 gives 3 exploit / 1 explore / 0 wildcard / 1 context. The phone uses every surviving exploit pick (≤4, within the refill bound) and the card shows 1–3 options.
- **LLM gate.** `LlmPriorityGate` serialises desktop and phone LLM calls, admitting desktop first but never pre-empting a running call. With both devices on Radio, a desktop rebuild can wait behind one phone call (~20–30 s).
- **Errors.** Shape errors return 422, semantic errors 400, ranker/Chroma failures 503. The phone records each as a reason and falls back to offline; it retries once only for `epoch_stale`, `availability_stale`, `no_availability` and a 404 context.
- **Availability re-upload** with the same digest but a new `catalog_revision` updates the stored revision.
- **Phone fence.** A held set is fenced on anchor, mood, exploration, vibe, mapping generation, sync epoch and context epoch, not the full `RadioInputToken` (queue revision and journal history change on every pause/seek/refill). The full token still guards each refill commit.
- **Start grace.** A song becomes current just before its actual-start callback marks the request pending, so refill waits up to 3 s for that mark when songs remain queued.
- **Admin routes.** `POST`/`DELETE /api/mobile-hosting/devices/{id}/connected-radio`; revoking the scope or the device also drops that device's in-memory snapshot and contexts.

Verification: backend 854 passed / 9 skipped; frontend 182/182 and build; Android 120 JVM tests, `lintDebug` 0 errors, `assembleDebug`, and the instrumented suite on an API 37 emulator. `RadioShellTest` failed intermittently with `CalledFromWrongThreadException` while the emulator was under load; it passed 3/3 isolated runs on this branch and 2/2 on `main`. The final full run was 171 tests with 1 failure, a 10 s timeout in `RadioRepositoryTest.legacyDesktopVectorsRequireSnapshotOptInWithoutInventedProvenance`, which passed 2/2 when rerun alone.
