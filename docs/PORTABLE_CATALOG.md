# Portable catalog v1

Implemented reference: `backend/portable/catalog.py`. This package imports only
the Python standard library and NumPy. It never opens Radio's database, ChromaDB,
audio files, playback sessions or network services. The server adapter extracts
source data separately; refer to the portable API documentation for transport and
authentication.

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
