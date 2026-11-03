# Portable catalog v1

Implemented reference: `backend/portable/catalog.py`. This package imports only
the Python standard library and NumPy. It never opens Radio's database, ChromaDB,
audio files, playback sessions or network services. The server adapter extracts
source data separately; refer to the portable API documentation for transport and
authentication.

## Android checkpoint 1B transport

The USB gateway now serves explicitly prepared full revisions, pinned historical revisions, exact-base deltas and digest-addressed vector bytes. Preparation is a local desktop action; mobile GETs never run export or ingestion. Checkpoint 1B introduced separate Room v6 metadata tables; the current Room v8 app imports into these catalog tables and private vector storage, validating the package before an atomic active-revision change. See [MOBILE_PAIRING.md](MOBILE_PAIRING.md) for the workflow and limits.

The native transport requires the gateway's raw canonical Python JSON bytes and preserves numeric tokens while validating sorted Unicode keys and string escaping. It stores a full manifest as the active revision identity; a delta has a different self-hash even at the same target revision and is validated separately as a transfer plan. Provenance remains per descriptor, including when identical stored bytes are reused by compatible references. All descriptors are validated, even if their bytes share a cache entry.

### Bounded mobile transfers

The future per-transfer ceiling is **100 MB (100,000,000 bytes)**. Vector batches
use a deliberately smaller **8,000,000-byte / 64-vector maximum** to limit phone
memory and request size. Raw binary is used without base64 or archive overhead.
The existing canonical manifest limit remains 16 MiB and individual vectors are
at most 256 KiB, so legacy transfers also remain below the future ceiling.
These are per-request limits; a complete catalog may span many requests.

The prepared manifest response advertises `X-Radio-Catalog-Batches: 1`. Android
then requests the gateway-only endpoint:

```text
GET /api/portable/v1/vector-batch?revision=R&manifest_sha256=H&digests=D1,D2,...
```

The query uses that exact field order, an immutable full-manifest hash and up to
64 unique lowercase SHA-256 digests. The server requires `catalog:read`, verifies
membership in the named prepared revision, checks total descriptor lengths before
reading, and returns concatenated blobs in request order. The canonical manifest
defines each blob's byte length and checksum; there is no independent framing or
mutable batch index. Batch query bytes are capped at 4,500. Redirects, byte ranges
and request bodies remain prohibited.

Android requests only missing/corrupt blobs and caps the response at their exact
combined length. It validates the entire batch, including every vector checksum,
before publishing its private content-addressed blobs. Truncated, extra or corrupt
bytes cannot activate a partial revision. Cancellation/restart reuses previously
verified blobs; a partially received batch is downloaded again. The active catalog
changes only after all vectors validate and the existing Room transaction commits.
Servers without the advertisement retain the individual-vector import path.

Android cache reclamation removes private inactive data while retaining every active catalog and the in-progress import. It does not delete source audio or change the Media3 timeline. Local content hashing and matching are implemented in checkpoint 1C; offline recommendation use of imported vectors is implemented in 2B, with phone review pending. See [ANDROID_APP_PLAN.md](ANDROID_APP_PLAN.md).

## Identity and song records

`catalog_id` identifies one server library independently of a host path. A song's
portable server identity is the pair `(catalog_id, songs[].id)`. `id` is a string;
integer database IDs are encoded as decimal strings. `recording_id`, `isrc`,
`content_sha256` and optional acoustic `fingerprint` describe media identity.
Database `file_hash` is a path hash in the existing app and MUST NOT be exported
as a content checksum. A client keeps its own `local_id` and document/URI handle.
The package does not contain music or require media transfer.

Song metadata is explicitly allowlisted: artist/title/album/album_artist,
duration in seconds, track/disc numbers, genres/tags, energy/valence in [0,1], BPM,
cluster ID, relative folder labels and collections, description, basename,
availability and optional behavioral priors. The exporter omits unrecognized
fields, including database filesystem paths and credentials. Folder labels may
contain relative subcategories; absolute paths, drive prefixes and traversal are
rejected. No server media grant, token or authentication state belongs here.

`available:false` is catalog information, not an instruction to delete local
music. The device's own playable tracks determine offline eligibility. A removed
server record can remain a local-only playable/searchable item.

## Package and binary encoding

Each immutable revision is a directory containing `manifest.json` and a
`vectors/` directory. JSON is UTF-8, compact, sorted-key JSON with finite numbers;
schema is `radio.portable.catalog`, integer `version` is 1. Unknown top-level
additive fields may be ignored after checksum validation; unknown schema versions
must be rejected. Required structures and descriptors remain strict.

Manifest fields:

| Field | Meaning |
| --- | --- |
| `schema`, `version` | Protocol identity and major schema |
| `catalog_id`, `revision` | Stable library ID and increasing positive revision |
| `base_revision`, `base_sha256` | Exact required base, or both null for full export |
| `songs` | Complete metadata snapshot, sorted by string ID |
| `removed_song_ids` | Tombstones relative to prior snapshot; never delete media |
| `embeddings` | Complete vector descriptor snapshot |
| `included_blobs` | Digests actually supplied in this package |
| `manifest_sha256` | SHA-256 of canonical manifest with this field omitted |

Each descriptor has `song_id`, `space` (`text` or `audio`), explicit `model` and
`model_version`, `dimension`, `metric` (`cosine`, `l2`, `ip`), `normalization`
(`none`, `l2`, `unknown`), `dtype` (`float32`, optional `float16`), `byte_length`,
`sha256` and `blob`. The canonical blob path is `vectors/{sha256}.bin`.

Blobs contain exactly `dimension` IEEE-754 floats in LITTLE-ENDIAN byte order,
with no header, padding or compression. `byte_length` is dimension times 4 or 2.
Default export is Float32. Checksums cover stored bytes before conversion.
Imports convert to read-only Float32 arrays. Missing embeddings are represented by
absence of a descriptor, never zeros, invented vectors or a dimension placeholder.
There is at most one current descriptor per song/space. Different songs may use
different dimensions and model versions in the same revision.

Provenance is per vector, not assumed from current server configuration. Historical
vectors without recorded provenance must explicitly say legacy/unknown, preserving
observed dimensions and collection metric. They cannot retrospectively establish
which model produced them. Future ingestion should store that provenance at write
time. The reference ranks cosine-normalized copies and preserves original exported
values/metric; Android must deliberately select the same algorithm.

Vectors are only comparable inside `(space, model, model_version, dimension)`.
Equal dimensions do not imply compatible models. A model upgrade can coexist with
legacy records. Without feedback in the new model's group, those songs use metadata
fallback until a compatible taste signal becomes available.

## Revisions, failure and publication

`write_catalog(destination, songs, embeddings, *, catalog_id, revision,
previous=None, dtype='float32', removed_song_ids=())` receives a COMPLETE next
snapshot. It constructs a temporary sibling directory, writes/fsyncs all blobs
and the manifest, and atomically renames it into a new destination. It refuses to
overwrite an existing revision. On Windows a bounded retry handles temporary
sharing violations without replacing the atomic rename with a partial copy.
This is atomic visibility, not a claim about directory-entry power-loss durability
on every filesystem. Source media/embeddings are never modified.

With a validated `previous` Catalog, the package includes only blob digests absent
from that base. Metadata and all references remain complete. Identical stored
vectors share one digest even across spaces/models; provenance stays in descriptors.
An unchanged vector incurs no additional binary transfer. A changed vector,
precision or model descriptor produces the appropriate new reference. Tombstones
are derived from missing IDs and optional explicit removals.

`read_catalog(path, *, base=None)` validates schema, manifest digest, duplicates,
IDs, descriptors, size limits, finite/nonzero vectors, declared normalization,
canonical paths, blob byte lengths and SHA-256 checksums BEFORE returning a new
Catalog. A delta requires the exact catalog/revision/manifest digest of its base.
The caller retains its active Catalog if import fails and retries/resumes missing
content-addressed blobs. A checksum provides corruption detection; trust still
comes from the authenticated transport, not an unsigned manifest's self-hash.
Never install partial state. Periodic full snapshots let clients avoid retaining
an unbounded delta chain. This reference has no archive extraction surface.

`Catalog(manifest, songs, vectors, descriptors, root=None)` uses dictionaries keyed
by song ID and `(song_id, space)` for vectors/descriptors. The imported data can be
copied to native storage without importing any Radio internals.

## Verification and precision

Run from the checkout:

```powershell
backend\venv\Scripts\python -B scripts/run_tests.py -q backend/tests/test_portable_catalog.py
```

Synthetic round trips cover both spaces, heterogeneous models/dimensions, missing
vectors, deterministic ordering, immutable publication, incremental reuse/removals,
model changes, corrupted/missing data and hostile path/shape/metric descriptors.
All generated artifacts stay under `.test-runtime/` through the guarded runner.

The Float16 experiment uses seed 448, 2,300 normalized synthetic 128D vectors and
20 queries. Tests require maximum cosine error below 0.0003 and mean top-20 set
overlap at least 99%. Float16 halves raw vector bytes but remains explicitly opt-in;
this synthetic check cannot establish ranking preservation for actual music or
every historical model. Float32 remains the production/reference default.

The fixture `backend/tests/fixtures/portable_golden.json` is a small, fully synthetic
catalog with both spaces, local availability, feedback, settings, seed, scores and
queue expectations. Focused tests construct/import its binary package in disposable
storage; production exports are not required to reproduce it.
