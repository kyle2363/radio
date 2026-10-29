"""Immutable catalog revisions with transparent little-endian vector blobs."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

import numpy as np

VERSION = 1
SCHEMA = "radio.portable.catalog"
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_DIMENSION = 65536
# Explicit allowlist: never accidentally serialize a database path, grant or token.
SONG_FIELDS = frozenset({
    "id", "server_song_id", "recording_id", "isrc", "content_sha256", "fingerprint",
    "artist", "title", "album", "album_artist", "duration", "track_number", "disc_number",
    "genres", "genre_tags", "emotional_tags", "instrument_tags", "tags", "energy", "valence",
    "bpm", "cluster_id", "folder", "collections", "description", "filename", "available",
    "play_count", "skip_count", "complete_count", "avg_completion", "favorite", "user_energy",
})


class CatalogError(ValueError):
    """Untrusted or incompatible portable input; caller should retain its prior revision."""


def canonical_json(value):
    """Canonical wire encoding (UTF-8, sorted keys, compact, finite JSON numbers)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class Catalog:
    """Validated snapshot. vectors[(song_id, space)] contains a float32 ndarray.

    descriptors[(song_id, space)] records model/version/dimension/metric provenance.
    `root` is optional and is used only to resolve inherited blobs during export.
    """
    manifest: dict
    songs: dict
    vectors: dict
    descriptors: dict
    root: Path | None = None


def _song(source):
    if not isinstance(source, dict) or source.get("id") is None or not str(source.get("id", "")):
        raise CatalogError("Every song requires a stable id")
    result = {key: value for key, value in source.items() if key in SONG_FIELDS}
    result["id"] = str(result["id"])
    # Category strings may be relative labels, never an absolute/private host path.
    for key in ("folder", "filename"):
        value = result.get(key)
        if value and (":" in str(value) or str(value).startswith(("/", "\\")) or
                      ".." in str(value).replace("\\", "/").split("/")):
            raise CatalogError(f"{key} must be a portable relative label")
    if result.get("filename") and any(c in result["filename"] for c in ("/", "\\")):
        raise CatalogError("filename must be a basename")
    canonical_json(result)
    return result


def _descriptor(entry, dtype):
    space = entry.get("space")
    if space not in ("text", "audio"):
        raise CatalogError("Unknown embedding space")
    for field in ("model", "model_version"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise CatalogError(f"Embedding requires explicit {field} (use 'unknown' if unavailable)")
    metric = entry.get("metric", "cosine")
    norm = entry.get("normalization", "none")
    if metric not in ("cosine", "l2", "ip") or norm not in ("none", "l2", "unknown"):
        raise CatalogError("Unsupported metric or normalization")
    vector = np.asarray(entry["vector"], dtype=np.float32)
    if vector.ndim != 1 or not 0 < vector.size <= MAX_DIMENSION or not np.isfinite(vector).all():
        raise CatalogError("Invalid vector shape or non-finite values")
    if np.linalg.norm(vector) == 0:
        raise CatalogError("Zero vectors cannot represent similarity")
    if norm == "l2" and abs(float(np.linalg.norm(vector)) - 1) > 0.01:
        raise CatalogError("Vector declared l2-normalized is not unit length")
    encoded = vector.astype("<f4" if dtype == "float32" else "<f2").tobytes()
    if not np.isfinite(np.frombuffer(encoded, dtype="<f4" if dtype == "float32" else "<f2")).all():
        raise CatalogError("Vector overflow after storage conversion")
    digest = sha256(encoded)
    return {"song_id": str(entry["song_id"]), "space": space, "model": entry["model"],
            "model_version": entry["model_version"], "dimension": int(vector.size),
            "metric": metric, "normalization": norm, "dtype": dtype,
            "byte_length": len(encoded), "sha256": digest,
            "blob": f"vectors/{digest}.bin"}, encoded


def write_catalog(destination, songs, embeddings, *, catalog_id, revision, previous=None,
                  dtype="float32", removed_song_ids=()):
    """Atomically create a NEW directory; source data is never modified.

    songs/embeddings describe the COMPLETE new snapshot. previous is a validated
    Catalog or package path; unchanged blobs are omitted and resolved from that
    exact base on import. Pass previous=None for a self-contained full export.
    revision is a monotonically increasing integer within a stable catalog_id.
    """
    if dtype not in ("float32", "float16"):
        raise CatalogError("dtype must be float32 or float16")
    if type(revision) is not int or revision < 1 or not isinstance(catalog_id, str) or not catalog_id:
        raise CatalogError("Invalid catalog identity or revision")
    if previous is not None and not isinstance(previous, Catalog):
        previous = read_catalog(previous)
    if previous and (previous.manifest["catalog_id"] != catalog_id or
                     previous.manifest["revision"] >= revision):
        raise CatalogError("Incremental export requires the same catalog and a newer revision")
    destination = Path(destination)
    if destination.exists():
        raise CatalogError("Destination already exists; published revisions are immutable")
    rows = [_song(row) for row in songs]
    rows.sort(key=lambda row: row["id"])
    ids = {row["id"] for row in rows}
    if len(ids) != len(rows):
        raise CatalogError("Duplicate song id")
    removed = sorted(set(map(str, removed_song_ids)))
    if set(removed) & ids:
        raise CatalogError("Removed songs cannot be present in the new snapshot")
    if previous:
        removed = sorted(set(removed) | (set(previous.songs) - ids))
    descriptors, blobs, seen = [], {}, set()
    for entry in embeddings:
        desc, data = _descriptor(entry, dtype)
        key = (desc["song_id"], desc["space"])
        if key in seen or key[0] not in ids:
            raise CatalogError("Duplicate embedding or embedding for unknown song")
        seen.add(key)
        descriptors.append(desc)
        blobs[desc["sha256"]] = data
    descriptors.sort(key=lambda desc: (desc["song_id"], desc["space"]))
    inherited = {desc["sha256"] for desc in previous.descriptors.values()} if previous else set()
    manifest = {"schema": SCHEMA, "version": VERSION, "catalog_id": catalog_id,
                "revision": revision, "base_revision": previous.manifest["revision"] if previous else None,
                "base_sha256": previous.manifest["manifest_sha256"] if previous else None,
                "songs": rows, "removed_song_ids": removed, "embeddings": descriptors,
                "included_blobs": sorted(set(blobs) - inherited)}
    manifest["manifest_sha256"] = sha256(canonical_json(manifest))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".catalog-", dir=destination.parent))
    try:
        (staging / "vectors").mkdir()
        for digest in manifest["included_blobs"]:
            with (staging / "vectors" / f"{digest}.bin").open("wb") as stream:
                stream.write(blobs[digest])
                stream.flush()
                os.fsync(stream.fileno())
        with (staging / "manifest.json").open("wb") as stream:
            stream.write(canonical_json(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        # Rename publishes a complete directory, never a partial manifest.
        for attempt in range(6):
            try:
                os.rename(staging, destination)
                break
            except PermissionError as exc:
                # Windows indexers/virus scanners can briefly hold closed files.
                # Retry the SAME atomic rename; never degrade to a partial copy.
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 5 or destination.exists():
                    raise
                time.sleep(.01 * 2 ** attempt)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def read_catalog(path, *, base=None):
    """Validate package and every vector before returning a new Catalog.

    A delta requires its exact validated base Catalog; callers retain the old
    Catalog if validation fails. No extraction, source mutation or media deletion.
    """
    root = Path(path).resolve()
    try:
        manifest_file = root / "manifest.json"
        if manifest_file.stat().st_size > MAX_MANIFEST_BYTES:
            raise CatalogError("Manifest exceeds size limit")
        manifest = json.loads(manifest_file.read_bytes())
        if manifest.get("schema") != SCHEMA or type(manifest.get("version")) is not int or manifest["version"] != VERSION:
            raise CatalogError("Unsupported catalog schema/version")
        digest = manifest.get("manifest_sha256")
        unsigned = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        if digest != sha256(canonical_json(unsigned)):
            raise CatalogError("Manifest checksum mismatch")
        if type(manifest.get("revision")) is not int or manifest["revision"] < 1:
            raise CatalogError("Invalid revision")
        if not isinstance(manifest.get("catalog_id"), str) or not manifest["catalog_id"]:
            raise CatalogError("Invalid catalog identity")
        if manifest.get("base_revision") is not None:
            if not isinstance(base, Catalog) or base.manifest["catalog_id"] != manifest["catalog_id"] or \
                    base.manifest["revision"] != manifest["base_revision"] or \
                    base.manifest["manifest_sha256"] != manifest["base_sha256"] or \
                    manifest["revision"] <= manifest["base_revision"]:
                raise CatalogError("Exact base revision required")
        else:
            if manifest.get("base_sha256") is not None:
                raise CatalogError("Full snapshot cannot require a base checksum")
            base = None
        rows = [_song(row) for row in manifest["songs"]]
        songs = {row["id"]: row for row in rows}
        if len(songs) != len(rows) or set(map(str, manifest["removed_song_ids"])) & set(songs):
            raise CatalogError("Duplicate or removed song in snapshot")
        vectors, descriptors = {}, {}
        included = set(manifest["included_blobs"])
        referenced = set()
        for desc in manifest["embeddings"]:
            key = (str(desc["song_id"]), desc["space"])
            if key in vectors or key[0] not in songs:
                raise CatalogError("Duplicate or unknown vector reference")
            digest = desc["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise CatalogError("Invalid blob digest")
            if desc["blob"] != f"vectors/{digest}.bin":
                raise CatalogError("Noncanonical blob path")
            dimension = desc["dimension"]
            dtype = desc["dtype"]
            if type(dimension) is not int or not 0 < dimension <= MAX_DIMENSION or dtype not in ("float32", "float16"):
                raise CatalogError("Invalid vector encoding")
            expected = dimension * (4 if dtype == "float32" else 2)
            if desc["byte_length"] != expected:
                raise CatalogError("Invalid vector length")
            if digest in included:
                blob = root / desc["blob"]
                if blob.is_symlink() or not blob.resolve().is_relative_to(root) or blob.stat().st_size != expected:
                    raise CatalogError("Unsafe blob path or wrong size")
                data = blob.read_bytes()
            else:
                matching = next((k for k, v in (base.descriptors.items() if base else [])
                                 if v["sha256"] == digest and v["dtype"] == dtype), None)
                if matching is None:
                    raise CatalogError("Missing vector blob")
                data = base.vectors[matching].astype("<f4" if dtype == "float32" else "<f2").tobytes()
            if sha256(data) != digest:
                raise CatalogError("Vector checksum mismatch")
            vector = np.frombuffer(data, dtype="<f4" if dtype == "float32" else "<f2").astype(np.float32)
            # Reuse all provenance/shape/normalization validation on imports too.
            validated, _ = _descriptor({**desc, "vector": vector}, dtype)
            if any(validated[k] != desc[k] for k in validated):
                raise CatalogError("Inconsistent vector descriptor")
            vector.setflags(write=False)
            vectors[key], descriptors[key] = vector, dict(desc)
            referenced.add(digest)
        if not included <= referenced:
            raise CatalogError("Unreferenced included blob")
        return Catalog(manifest, songs, vectors, descriptors, root)
    except CatalogError:
        raise
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        raise CatalogError(f"Invalid catalog: {exc}") from exc
