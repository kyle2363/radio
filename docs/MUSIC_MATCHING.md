# Local music matching protocol v1

Implemented reference: `backend/portable/matching.py`. The phone's selected music
directories are authoritative for playable media. Android directory selection,
MediaStore, permissions and audio playback are future work. Matching never moves,
retags, deletes or uploads files. Audio upload/ingestion requires a separate future
user-authorized operation.

## Inputs, decisions and result contract

`match_catalog(catalog_songs, local_tracks, decisions=())` accepts song records or
an ID-keyed dictionary, local records, and previously persisted decisions. Invoke
it within one `catalog_id` namespace. A future native scanner supplies stable
device-local document identities and fields such as `local_id`, artist/title/album,
duration seconds, basename, actual `content_sha256`, ISRC/recording ID and optional
fingerprint. Server paths/filenames need not match. No audio bytes are required by
the matching algorithm itself.

The read-only `inspect_local_file(path, local_id, **metadata)` helper supports
reference demonstrations using disposable directories. It streams a SHA-256 hash
of file BYTES, reports a basename and, for PCM WAV fixtures, reads duration through
the standard library. It does not implement an Android scanner, decode MP3 tags or
write metadata. Tests show different filenames and paths matching identical bytes.

Persist decisions in a versioned client-owned store keyed by catalog ID and local
ID: `{local_id, song_id, action:'confirm'|'reject', content_sha256?}`. Store the
decision's own stable ID, origin and logical revision in a native implementation
to support auditing/sync. Content anchoring is recommended: replacement of the file
invalidates a decision for different bytes. Without an anchor, the local document
identity must be stable and must not be reused for a replacement file. Rejections
exclude that pair on future searches. Conflicting confirmations/rejections require
review; arrival order does not silently choose a winner. A missing confirmed server
record is reported explicitly and does not trigger automatic remapping.

Output is JSON-compatible:

```json
{
  "schema": "radio.portable.matches", "version": 1,
  "results": [{
    "local_id": "document-7", "song_id": null, "status": "ambiguous",
    "candidates": [{"song_id":"42", "confidence":0.79,
      "reasons":["same_artist","same_title"], "automatic":false}]
  }],
  "counts":{"confirmed":0,"high_confidence":0,"ambiguous":1,"unmatched":0}
}
```

`confirmed` is a prior explicit user decision. `high_confidence` is a unique strong
automatic candidate. `ambiguous` requires user review; `song_id` remains null.
`unmatched` remains a local-only song. Candidate lists include up to ten explanations;
confidence values are deterministic rule scores, not calibrated probabilities.
Optional `warning` identifies contradictory decisions or missing confirmed records.
Only confirmed/high-confidence rows should populate an automatic playable mapping.

## Layered rules and uncertainty

1. A still-valid user confirmation short-circuits all search. Contradictory decisions
   remain ambiguous. Rejected pairs are removed from consideration.
2. Unique equal validated SHA-256 content hashes score 1.0. Duplicate catalog
   records with the same bytes remain ambiguous. Hashing file paths is forbidden.
   Retagging/re-encoding normally changes bytes even for the same recording.
3. Equal fingerprint objects require algorithm, version, value and matching context;
   with duration within two seconds the rule scores 0.98. The reference compares
   exact fingerprints only; tolerant acoustic matching remains future work.
4. Equal namespaced recording IDs/recording UUIDs or well-formed ISRCs plus duration within two seconds score 0.97.
   Missing duration lowers confidence and requires review.
5. Unicode/case/punctuation-normalized artist AND title AND nonempty album, with
   duration within two seconds and matching version markers, score 0.94. Less
   evidence remains ambiguous. Title-only and filename heuristics are weak hints.
6. A duration difference above max(5 seconds, 3% of server duration), or different
   live/remaster/remix/acoustic/edit/cover/etc. markers, caps confidence at 0.6.
   Version years are retained when version markers are present.

Automatic thresholds also require at least 0.05 margin over the next candidate.
Filename matching alone never forces a result. Missing tags, alternative spelling,
duplicate IDs, covers, live recordings and different remasters require review when
strong independent identity evidence is absent. Metadata cannot prove recording
identity: even exact tags can be wrong, and similarly tagged edits can pass a
two-second duration tolerance. These are known conservative heuristics, not a
general acoustic identity solution; clients should allow correcting every mapping.

## Chromaprint assessment

[Chromaprint's official documentation](https://acoustid.org/chromaprint) describes
a native library consuming decoded PCM; `fpcalc` offers JSON and requires FFmpeg
when built as a file-decoding utility. Its [upstream project](https://github.com/acoustid/chromaprint)
targets near-identical recordings and documents a precision/robustness tradeoff.
It is a practical optional fingerprint source, not installed or invoked here.

Future extraction must be cancellable/background work with caching by content hash,
explicit algorithm/library version, sample duration and extraction settings. Cost
includes decoding each file, native dependencies, battery/IO and licensing/build
review; no device benchmark or mandatory library scan is claimed. Matching an
already supplied fingerprint is cheap. Local fingerprint comparison does not require
an AcoustID network lookup or uploading music. Approximate fingerprint distance,
sample alignment and acceptable thresholds require a labeled re-encode/edit/live
corpus before automatic fuzzy identity matching is enabled.

## Local-only music and verification

Unmatched local tracks remain playable and searchable as `local:<local_id>` in the
offline engine, using available tags/energy and neutral fallback without invented
embeddings. They may later join the server catalog through explicit ingestion,
server-side embeddings and a confirmed mapping. Catalog removal never deletes
their files. The current protocol does not upload them automatically.

Run `backend\venv\Scripts\python -B scripts/run_tests.py -q
backend/tests/test_portable_matching.py`. Seven rule-based tests cover disposable
renamed WAVs/content checksums, metadata/ISRC, covers and version/duration conflicts,
duplicate records, missing tags, filename-only suggestions, confirmed/rejected and
stale mappings, removed confirmations and fingerprint version checks. These prove
the documented cases; no real-world matching accuracy percentage is claimed.
