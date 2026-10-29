"""Read-only catalog source adapter. No model calls or collection creation."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

import config
from db import database
from portable.catalog import canonical_json, read_catalog, write_catalog

logger = logging.getLogger(__name__)


def _tags(value):
    if isinstance(value, list):
        return value
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


class ServerCatalog:
    def __init__(self, store, *, root=None, client_factory=None):
        self.store = store
        self.root = Path(root or Path(config.DATA_DIR) / "portable_catalogs")
        self.client_factory = client_factory
        self._lock = threading.Lock()

    def source(self):
        with database.get_connection_cm() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM songs ORDER BY id")]
            memberships = {}
            for row in conn.execute("SELECT cs.song_id,c.name FROM collection_songs cs JOIN collections c ON c.id=cs.collection_id ORDER BY c.name"):
                memberships.setdefault(str(row[0]), []).append(row[1])
        songs = []
        for row in rows:
            source = {key: row.get(key) for key in ("artist", "title", "album", "bpm", "energy", "valence", "cluster_id", "user_energy")}
            filepath = Path(row["filepath"])
            try:
                folder = filepath.parent.resolve().relative_to(Path(config.MUSIC_DIR).resolve()).as_posix()
            except ValueError:
                folder = ""
            source.update(id=str(row["id"]), server_song_id=row["id"], duration=row.get("duration_secs"),
                          filename=filepath.name, folder=folder if folder != "." else "", collections=memberships.get(str(row["id"]), []),
                          genres=_tags(row.get("genre_tags")), emotional_tags=_tags(row.get("emotional_tags")),
                          instrument_tags=_tags(row.get("instrument_tags")), available=filepath.is_file())
            songs.append(source)
        if self.client_factory is None:
            # The existing helper constructs PersistentClient and may create a
            # database. Never open it when no existing database is present.
            if not (Path(config.CHROMA_PATH) / "chroma.sqlite3").is_file():
                return songs, []
            from db.chroma import get_chroma_client
            factory = get_chroma_client
        else:
            factory = self.client_factory
        client = factory()
        embeddings = []
        known_ids = {song["id"] for song in songs}
        for space, name in (("text", "songs"), ("audio", config.MUQ_AUDIO_COLLECTION)):
            try:
                collection = client.get_collection(name)
            except Exception as exc:
                # Missing collections are a valid catalog capability. Unexpected
                # storage errors abort instead of publishing vector tombstones.
                if type(exc).__name__ in ("NotFoundError", "InvalidCollectionException") or isinstance(exc, KeyError):
                    continue
                raise RuntimeError("Cannot inspect existing embedding collection") from exc
            metadata = collection.metadata or {}
            metric = metadata.get("hnsw:space", "l2")
            configuration = getattr(collection, "configuration", None)
            if isinstance(configuration, dict):
                metric = configuration.get("hnsw", {}).get("space", metric)
            count_before = collection.count()
            stable_ids = sorted(known_ids)
            for offset in range(0, len(stable_ids), 256):
                # Enumerate catalog IDs, not a shifting ANN collection offset.
                batch = collection.get(ids=stable_ids[offset:offset + 256], include=["embeddings", "metadatas"])
                vectors = batch.get("embeddings")
                if vectors is None:
                    continue
                descriptors = batch.get("metadatas") or [{}] * len(batch["ids"])
                for song_id, vector, meta in zip(batch["ids"], vectors, descriptors):
                    if song_id not in known_ids or vector is None:
                        continue
                    meta = meta or {}
                    embeddings.append({"song_id": song_id, "space": space, "vector": vector,
                                       "model": meta.get("embedding_model", "legacy_unknown:" + name),
                                       "model_version": meta.get("embedding_model_version", "unknown"),
                                       "metric": metric, "normalization": meta.get("embedding_normalization", "unknown")})
            if collection.count() != count_before:
                raise RuntimeError("Embedding collection changed during export; retry after ingestion settles")
        return songs, embeddings

    def snapshot(self):
        """Produce/reuse a complete immutable revision outside playback locks."""
        with self._lock:
            songs, embeddings = self.source()
            # Manifest hash is based on source values, including actual float32
            # bytes; provider configuration never substitutes for provenance.
            import numpy as np
            fingerprints = [{**entry, "vector": hashlib.sha256(np.asarray(entry["vector"], dtype="<f4").tobytes()).hexdigest()}
                            for entry in embeddings]
            digest = hashlib.sha256(canonical_json({"songs": songs, "vectors": sorted(fingerprints, key=lambda row: (row["song_id"], row["space"]))})).hexdigest()
            with database.get_connection_cm() as conn:
                meta = dict(conn.execute("SELECT key,value FROM portable_meta"))
            revision = int(meta.get("catalog_revision", 0))
            if digest == meta.get("catalog_digest") and (self.root / str(revision) / "manifest.json").is_file():
                return read_catalog(self.root / str(revision))
            self.root.mkdir(parents=True, exist_ok=True)
            # Recover a crash after publication but before metadata commit by
            # allocating after all published directories.
            revision = max([revision, *[int(path.name) for path in self.root.iterdir() if path.is_dir() and path.name.isdecimal()]]) + 1
            staging = self.root / (".build-" + uuid4().hex)
            write_catalog(staging, songs, embeddings, catalog_id=self.store.catalog_id, revision=revision)
            pool = self.root / "blobs"
            pool.mkdir(exist_ok=True)
            for blob in (staging / "vectors").iterdir():
                pooled = pool / blob.name
                if not pooled.exists():
                    os.link(blob, pooled)
                else:
                    if hashlib.sha256(pooled.read_bytes()).hexdigest() != blob.stem:
                        raise ValueError("Existing export vector pool checksum mismatch")
                    # Both names are owned content-addressed export artifacts.
                    # Source music, metadata, and Chroma are never touched.
                    blob.unlink()
                    os.link(pooled, blob)
            os.rename(staging, self.root / str(revision))
            with self.store.transaction() as conn:
                conn.executemany("INSERT OR REPLACE INTO portable_meta VALUES (?,?)",
                                 [("catalog_revision", str(revision)), ("catalog_digest", digest)])
            return read_catalog(self.root / str(revision))

    def manifest(self, revision=None, base_revision=None):
        catalog = self.snapshot() if revision is None else self.load(revision)
        if base_revision is None:
            return catalog.manifest
        base = self.load(base_revision)
        if base.manifest["revision"] >= catalog.manifest["revision"]:
            raise ValueError("Base must precede target revision")
        delta_path = self.root / f"delta-{base_revision}-{catalog.manifest['revision']}"
        with self._lock:
            if not delta_path.exists():
                entries = [{**desc, "vector": catalog.vectors[key]} for key, desc in catalog.descriptors.items()]
                write_catalog(delta_path, list(catalog.songs.values()), entries,
                              catalog_id=self.store.catalog_id, revision=catalog.manifest["revision"], previous=base)
            return json.loads((delta_path / "manifest.json").read_text(encoding="utf-8"))

    def load(self, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("Invalid catalog revision")
        if not (self.root / str(revision) / "manifest.json").is_file():
            raise KeyError("Catalog revision not found")
        return read_catalog(self.root / str(revision))

    def blob(self, digest):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid blob checksum")
        path = self.root / "blobs" / (digest + ".bin")
        if not path.is_file():
            raise KeyError("Vector blob not found")
        if not path.resolve().is_relative_to((self.root / "blobs").resolve()):
            raise ValueError("Invalid vector blob location")
        return path
