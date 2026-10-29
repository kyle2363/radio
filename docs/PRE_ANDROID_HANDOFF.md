# Pre-Android server foundation — October 28, 2026

## Scope and starting checkpoint

Work started on `stability-server-architecture` at `c596895602e4890b6690b8525988f18d1d44abd5`
with a clean working tree. Local rollback tag:
`codex/pre-android-baseline-20261028`. No Android application, Stash fork, Kotlin,
media transfer, model install, public listener, or playback architecture rewrite
is part of this change. Original music, metadata, production SQLite and existing
Chroma vectors were not opened for automated validation or modified by tests.

Luna performed file/default/logging inventory, Sol implemented desktop settings
and the portable core, and Astra advised on architecture and reviewed critical
contracts. The parent integrated the native API, sync, simulator and verification.

## Current-state audit and implementation plan

| Boundary | Existing dependencies | Additive preparation |
| --- | --- | --- |
| Queue/retrieval | Chroma, SQLite, NumPy, session mood/taste, random jitter, wall time | Gate only final LLM ranking; portable exact-search reference beside it |
| Reranker | Final chat service and deterministic assignment | Explicit global default and persistent session override |
| Taste | Four weighted windows, Chroma lookups, SQLite history, per-instance cache | Portable dual-space memory; imported shared feedback in desktop taste |
| Slots/energy | Existing slot rules, DB energy corrections, archetypes/guests | Language-neutral reference rules; golden comparisons and stated differences |
| Playback | Existing coordinator, browser producer permits, VLC adapter | Fresh Electron sessions use browser; restored explicit selections remain |
| Catalog | Numeric DB row IDs; path hashes; historical vectors lack model versions | Source UUID + row ID; metadata-only identity; content-addressed vector exports |
| Matching | Existing library reconciliation assumes server files | Separate conservative local-record protocol and persistent decisions |
| Sync | Shared server history; independent active contexts | Additive event ledger, scoped native tokens, transactional projections |

The existing `normal` mode skips the final reranker but still uses Chroma,
SQLite, optional constellations, time/randomness and announcement/talk machinery.
It is not an offline engine. Portable code imports none of those runtime modules.
Desktop constellations remain unchanged and are excluded from the offline target.
Existing `file_hash` hashes paths, so it is never exported as a content checksum.

The smallest implementation leaves playback/session coordination in place and
adds explicit settings, a public portable package, read-only source adapters,
versioned HTTP contracts and isolated tests. Native profile projection never
routes through an active player's commands or changes its current queue.

## Delivered contracts

- [Portable catalog and vectors](PORTABLE_CATALOG.md)
- [Reranking, browser defaults and quieter logging](RERANKING_AND_DESKTOP_DEFAULTS.md)
- [Matching protocol and confidence](MUSIC_MATCHING.md)
- [Offline scoring, golden fixtures and measured parity](OFFLINE_RECOMMENDATIONS.md)
- [Authentication, manual sync, conflicts and errors](PORTABLE_SYNC_API.md)
- [Connected/offline optional speech](PORTABLE_MEDIA_PLAN.md)

Fresh Electron sessions now start with browser audio. Existing explicit output
restoration remains supported. Successful routine requests are quiet by default;
errors remain visible and access-log query strings are removed. Set
`RADIO_QUIET_ACCESS_LOGS=false` for full sanitized request logging. Browser-local
position requests use the current local audio snapshot instead of redundant HTTP
polling; producer permit cadence and expiry fencing remain intact.

The reranking setting defaults enabled. The UI separately exposes the active
session control and the default for new sessions. It preserves current music,
prepared queue, session identity and unrelated AI features. A separate ranking
epoch rejects obsolete selection work without invalidating existing speech/media.

## Verification and remaining release gates

Focused tests cover export checksums/base versions, mixed dimensions and absent
vectors, matching ambiguity, portable feedback and queue preservation, scoped
tokens, wrong-catalog rejection, import retries/out-of-order events, mutable
conflicts, desktop taste integration, optional speech and the independent HTTP
simulator. The full backend, frontend, build and native smoke results are recorded
below after final verification. All generated fixtures remain inside `.test-runtime/`.

Manual profile capture at 2,300 synthetic songs measured approximately 42 ms for
first capture and 27 ms steady state in one local run; steady capture caused no
database writes. These measurements are local observations, not service-level
guarantees. Portable recommendation latency/precision measurements are documented
with their synthetic fixture dimensions in OFFLINE_RECOMMENDATIONS.md.

Known limits: full desktop/portable ranking parity and live recommendation quality
are not claimed; legacy model provenance is unknown; Chromaprint generation is
optional and not installed; cross-store export during ingestion lacks a shared
transaction; already-synchronized legacy skip undo has no retraction protocol;
native speech has a tested contract but no new production generation endpoint.
Existing real-audio, TTS/GPU, Electron tray/media keys, OS sleep and LAN/TLS manual
acceptance gates remain open. The server's old listener/queue does not become a
future phone's offline player simply by using this API.

GitHub's public API confirms starting-commit run
[40786056132](https://github.com/kyle2363/radio/actions/runs/40786056132) completed
successfully. The HTML page initially showed stale in-progress status, so the API
result was verified separately. This local change has no hosted result until it is
pushed; local checks do not constitute hosted CI acceptance.

## First Android phase and rollback

Start with user-selected directory access, persistent opaque local IDs, the
matching-decision store, validated catalog imports and the golden offline scorer.
Use local audio in place; keep unmatched music searchable/playable. Persist the
outbox and Lamport clock transactionally. Implement the manual sync contract with
scoped tokens before adding automatic sync. Add playback/media integration and
optional connected speech only after independent music and learning work offline.
Choose a dedicated UI or Stash integration later; none of these contracts requires
either. Resolve the documented retraction/export-concurrency limitations before
claiming complete multi-device production acceptance.

Rollback source using normal revert(s) of this feature's commits, or create a
recovery branch at the checkpoint tag after preserving work. Do not reset/clean
runtime data. The portable schema is additive and installed with a verified backup;
source rollback need not delete it. Reverting source does not undo imported history.
If a separate data rollback is necessary, stop the app, preserve all current data
and pending device outboxes, then choose an explicitly verified backup. Never
blindly restore a production DB or remove media/embeddings.

## Final verification

Unchanged starting-commit verification from a disposable source archive passed
460 backend tests, with 10 skips (the usual nine environment/opt-in cases plus
the client-mount check because the archive has no ignored frontend build).
The initial frontend baseline passed all 102 tests. A local rollback tag retains
the original commit.

Final complete-tree verification:

- Guarded full backend suite: **537 passed, 9 skipped**, in 64.17 seconds.
- Frontend suite: **104 passed**; production Vite build passed.
- Opt-in native VLC synthetic/dummy-output smoke: **4 passed**.
- Final expanded workflow test selection using isolated lean dependencies:
  **317 passed, 1 skipped**, in 76.80 seconds. Provider dependencies remain lazy
  in the deterministic reranker helper, so this check needs no model installation.
- Core portable suite: **35 passed**. Slot/allocation parity was exact over 120
  deterministic cases; full retrieval/music-quality parity remains unclaimed.
- Float16 synthetic maximum cosine error: **0.00008374**, mean top-20 overlap
  **100%** over the tested corpus. Production export stays Float32 by default.
- Matching tests cover renamed disposable WAVs, duplicates, missing tags,
  recording/version distinctions and persistent decisions; no real-library
  accuracy percentage is asserted.
- HTTP simulator covers lost acknowledgements, multiple 500-event batches,
  deleted catalog records and device-local identity isolation. The 501-event
  retry test verifies exactly 501 replays after a lost second acknowledgement.

Existing build warnings remain for duplicate `border` declarations in
FiltersPanel and bundle size. Skips are two Windows symlink-privilege cases,
three unavailable private energy fixtures and four opt-in native tests (run
separately above). No production listening session or original library was
modified by these automated checks. Audible live acceptance is still manual.
