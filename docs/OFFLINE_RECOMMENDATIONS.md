# Offline recommendation reference v1

`backend/portable/recommender.py` is a standalone deterministic Python/NumPy
reference for a future Kotlin implementation. It imports no application config,
database, ChromaDB, LLM, VLC, browser audio or network client. Taste constellations
are deliberately excluded; the desktop implementation retains them.

## Public contract and state

```python
engine = OfflineRecommender(catalog, local_tracks, mappings=None,
                            state=None, settings=None, seed=0)
ranked = engine.rank(now=unix_seconds)
picks = engine.recommend(count=5, now=unix_seconds)
changed = engine.feedback(event)
engine.refresh_upcoming(count=5, now=unix_seconds)
engine.enqueue(song_id)
next_id = engine.advance()
replay_id = engine.replay()
saved_json = engine.export_state()
local_results = engine.search("artist title")
```

`catalog` is a validated portable `Catalog`. `local_tracks` contain stable
`local_id`, optional matched `song_id`, optional `playable` (default true for a
scanner-provided present file) and local metadata. Passing `playable:false`
excludes a track. `mappings` may map local IDs to server IDs or be a v1 matching
report; only confirmed/high-confidence results are accepted from reports. The
caller must supply current local availability and rebuild on permission/file
changes. Multiple local copies of one catalog song collapse to one stable local
choice. Unmapped songs receive `local:<local_id>` and remain searchable/playable.

State is JSON, `version:1`: event history, hearts, blacklist, user energy,
settings, device-local mood/skip streak/last-five energy values, `current`,
committed `queue` and separate recommendation preview `upcoming`. Inputs and exports
are copied. Persist snapshots/events atomically in the native client; this reference
does not implement a mobile storage/database layer. Queue/session/mood/trajectory
stay device-local during shared-profile sync.

`rank` and `recommend` do not mutate state or record listening. `refresh_upcoming`
replaces only the preview. `enqueue` adds to the committed queue; `advance` chooses
committed entries first, then previews, skipping unavailable/blacklisted records.
`replay` returns the current playable ID without consuming either queue. Preparing,
selecting, opening or prefetching a file does not record a play. Only the actual
audio-start callback should emit a `started` event.

## Feedback and learning

Events use `{event_id?, type, song_id?, timestamp, value?, reason?}` with explicit
finite Unix timestamp seconds and string song ID. Supported types are `started`,
`completed`, `skipped`, `replayed`, `hearted`, `unhearted`, `disliked`, `blacklisted`,
`unblacklisted`, `energy`, `preference`, `mood`. An explicit stable event ID is
recommended for persistence/sync; repeated IDs return false without reapplying.
For local convenience the reference generates a deterministic ID when omitted.
Sync adapters translate these types into the public versioned event envelope.
This local method applies device action order. Import shared mutable state only
after the sync protocol's conflict resolver has materialized it; calling feedback
in network arrival order is not a replacement for distributed conflict handling.

Energy feedback requires `value` in [0,1]. Preference feedback has a dictionary of
known settings in `value`; mood feedback names one supported mood. Hearts remain a
durable positive signal; unheart removes the favorite boost. Dislike/blacklist is a
hard exclusion until explicitly reversed. Skips immediately penalize the song and
contribute negative taste. After three consecutive skips, mood shifts to chill
for `too_intense`, energetic for `too_slow`, otherwise discovery. Completion resets
the streak. Learning, filters and feedback operate entirely offline without an
expiry requiring server contact. Event storage grows with use; native clients need
durable event retention/compaction compatible with synchronization acknowledgments.

## Language-neutral algorithm

Use Float32 vectors and normalize working copies for cosine similarity; exported
original data remains unchanged. Vectors may only be combined within exact
`(space, model, model_version, dimension)` groups. Each song can have zero, one or
both spaces. No query in a song's model group means metadata fallback for that
space. Never invent a vector for a phone-only song. Unknown legacy provenance
cannot prove model compatibility and must remain visibly labeled.

Taste uses the latest 2,000 taste-relevant events, ordered by timestamp then event
ID. Signals: completed +1, skipped -0.6, replayed +1.3, hearted +1.5, unhearted -0.3,
disliked -1. Each group computes four windows:

| Layer | Event window | Blend weight | Position decay |
| --- | ---: | ---: | ---: |
| Immediate | 10 | 0.40 | 0.85 |
| Session | 50 | 0.25 | 0.92 |
| Daily | 200 | 0.20 | 0.96 |
| Long term | 2,000 | 0.15 | 0.99 |

For each layer, sum each compatible unit vector times signal times
`decay**position`, divide by the sum of absolute contributing weights, then blend
the four layer results. For three or more consecutive skips the four weights become
0.15/0.20/0.25/0.40. Add 0.15 times the mean current-favorite vector and normalize the
result. Layers are event-count timescales, not claims about calendar-day boundaries.
The retained event log can be longer than the taste window. All operations are local.

A vibe-lock song blends 80% anchor unit vector with 20% current taste and normalizes.
Missing/incompatible anchors leave that group's taste unchanged. The caller sets
and clears `vibe_lock`; device timer/song-limit management belongs to the future
player. It must not require a server or block local playback.

Exact matrix multiplication computes every compatible local track's cosine score.
With both spaces use `(1-audio_blend)*text + audio_blend*audio` (default audio blend
0.65). With one usable space use that score without inventing the missing one.
With neither use zero vector contribution plus metadata terms.

Effective energy is base energy when no user rating exists; otherwise
`0.7*base + 0.3*user`. A missing/NULL rating never masks actual base energy. Target
energy comes from explicit `energy_target` or mood, adds +0.15/-0.15 for rising/
falling direction, clamps to within 0.25 of the last played energy, then [0,1].
The engine retains the last five actual starts. Mood targets (energy,valence):
chill (.25,.55), neutral (.5,.5), energetic (.8,.65), focus (.4,.5), melancholy
(.3,.25), discovery (.55,.55).

Candidate score:

```text
0.60 * blended cosine
+ 0.20 * (1 - abs(effective energy - target energy))
+ 0.10 * (1 - abs(song valence - target valence))
+ 0.10 if favorite
- 0.35 * linear recency penalty over recent_window (default 1,800 seconds)
- 0.55 * linear skip/dislike penalty over skip_window (default 86,400 seconds)
- artist_penalty (default 0.12) for the current artist
- 0.50 for current song when more than one local track exists
```

Negative scores remain eligible. Hard exclusions are unavailable files, blacklist,
favorite-only mode, genre filters, folder filters and collection filters. Filters
are never silently relaxed. Recency/current/skip penalties are soft so a small
playable library can continue. An empty result from explicit hard constraints is
reported honestly; it does not override dislikes or claim nonexistent media.

Scores round to eight decimal places. Sort by descending score, then lowercase hex
SHA-256 of UTF-8 `"{seed}:{song_id}"`, then song ID. This avoids language-specific
random generators and unordered tie behavior. Golden checks allow small Float32
dot-product tolerance; Kotlin implementations should use the same precision and
rounding before tie-breaking.

`allocate_portable_slots` reproduces desktop's pure slot stage for supplied ranked
candidates with no persona/archetype/constellation. Vibe lock selects four exploit
and one explore. Normal allocation uses desktop's exploration formula (default
0.3 produces one exploit), different-cluster explore/wildcard, and current-energy
context, with deterministic rare-cluster/fallback rules. The existing desktop
wildcard loop yields a wildcard even when its nominal count is zero; the reference
preserves this observed behavior for stage parity. It does not claim that the
desktop's nominal 2/1/1/1 description matches every actual setting.

After slot allocation the reference fills remaining requested picks with unused
ranked playable tracks. It substitutes an unused different artist for repeats
when that alternative is within 0.12 score of the original slot choice. Explicit
slots/reasons and full component scores are exposed for inspection. Small
libraries return fewer unique tracks without duplicates; callers may refill after
the next actual playback event. No speech, DJ segment or network result gates this
algorithm.

## Golden tests, parity and measurements

Run the guarded suite:

```powershell
backend\venv\Scripts\python -B scripts/run_tests.py -q -s backend/tests/test_portable_catalog.py backend/tests/test_portable_matching.py backend/tests/test_portable_recommender.py
```

The checked-in `portable_golden.json` includes catalog songs, 3D text and 2D audio
vectors, actual-play history, device availability including a local-only track,
settings, seed 17, expected component-derived scores, pick IDs/slots and replay
queue expectations. Tests prove switching audio blend changes the top candidate,
feedback immediately changes taste, duplicate IDs are ignored and restored state
reproduces the same results. Model-identity, missing-vector, unavailable-file,
hard-filter, user-energy, mood, favorite, skip, vibe and queue invariants are covered.

Desktop parity is **stage-specific**, not full engine parity. The tests compare
every slot's full candidate dictionaries and final deterministic `(song_id,slot)`
assignment for 120 seeded cases: candidate sizes 1/3/5/40/100, current clusters
None/0/4, exploration 0/.3/.7/1, and vibe off/on. The same ranked candidate inputs
produce exact parity in those stages. The package never imports desktop modules;
only this comparison test imports them. This is stronger than comparing a few final
IDs but does not validate desktop retrieval, ANN quality, all profile scores,
transitions, constellation behavior, archetypes or AI reranking.

The offline score is a documented portable equivalent. Desktop uses additional
retrieval/behavioral state, SQLite filters, transition learning, constellation,
persona/archetype adjustments and optionally LLM ordering. This reference uses
exact cosine search and the formula above. No end-to-end desktop-vs-offline score
or music preference parity percentage is claimed. Real music judgments and actual
ANN-vs-exact recall remain unmeasured, and removed constellation differences cannot
be summarized honestly as a measured quality delta from synthetic fixtures.

The 2,300-song benchmark uses seed 322, normalized synthetic 768D text plus 512D
audio vectors, 600 artist labels, 28 clusters, and 100 completion events. One
guarded Windows run measured rank+select at 14.77/13.86/12.83/14.69 ms. Forty-six
independent scalar cosine checks matched matrix results within 1e-6. Times vary by
host/BLAS and are not Android battery/latency claims. Float32 raw vectors at this
size total about 11.2 MiB; simple exact search is adequate for this reference and
does not justify adding ANN infrastructure. The benchmark writes its detailed
result in disposable `.test-runtime/` and prints it with `-s`; no production
library/model/embedding was modified or required.

## First Android implementation

Implement catalog validation and local matching before playback. Keep native URI
availability separate from server identity. Port the documented pure functions
and replay queue semantics; run the golden vectors/scores and stage comparisons
before integrating a native player. Persist actual listening events independently
of prefetch; apply synchronization conflict rules to the shared profile while
preserving each device's active queue, mood, current track and position. Add
device benchmarks, larger labeled matching/quality datasets and interrupted-write
tests before selecting optional Float16 or acoustic matching. Server AI DJ remains
an optional connected capability; offline recommendations never wait for it.
