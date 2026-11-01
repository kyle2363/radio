"""Explicit local catalog preparation, separate from all mobile GET requests.

One worker is admitted, with no queue. The deadline fences publication; Python
cannot cancel an in-progress storage call, so admission stays occupied until that
worker exits. Ingest is checked before and after export, not locked: SQLite and
Chroma are separate stores and this is not a cross-store transactional snapshot.
"""
import copy
from collections import OrderedDict
from functools import wraps
import json
import logging
import os
from pathlib import Path
import threading
import time

import config
from portable.catalog import canonical_json, sha256

MAX_VECTOR_BYTES = 65536 * 4
MAX_REVISION = 9223372036854775807
MOBILE_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MOBILE_MAX_VECTOR_TOTAL = 256 * 1024 * 1024
logger = logging.getLogger(__name__)


class IngestActivity:
    """Track API-owned work even while legacy phases reset progress.running."""
    def __init__(self):
        self._lock, self._count, self._epoch = threading.Lock(), 0, 0

    def busy(self):
        with self._lock:
            return self._count > 0

    def _change(self, delta):
        with self._lock:
            self._count += delta
            if delta > 0:
                self._epoch += 1

    def epoch(self):
        with self._lock:
            return self._epoch

    def track(self, function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            self._change(1)
            try:
                return function(*args, **kwargs)
            finally:
                self._change(-1)
        return wrapped

    def start(self, function):
        # Reserve before launching, eliminating the queued-thread blind spot.
        self._change(1)
        def wrapped():
            try:
                function()
            finally:
                self._change(-1)
        try:
            threading.Thread(target=wrapped, daemon=True).start()
        except BaseException:
            self._change(-1)
            raise


class PreparedCatalog:
    def __init__(self, catalog_factory, *, root=None, ingest_busy=lambda: False, ingest_epoch=lambda: 0, deadline=120):
        self.catalog_factory = catalog_factory
        self.root = Path(root or Path(config.DATA_DIR) / "portable_catalogs")
        self.ingest_busy, self.deadline = ingest_busy, deadline
        self.ingest_epoch = ingest_epoch
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._done.set()
        self._worker = None
        self._service = None
        self._catalogs = OrderedDict()
        self._state, self._error = "idle", None
        self._prepared = None
        self._cancel = threading.Event()
        self._hash_progress = None

    @property
    def marker(self):
        return self.root / "mobile-prepared.json"

    def _restore(self):
        if self._prepared is not None or not self.marker.is_file():
            return
        try:
            if self.marker.stat().st_size > 1024 * 1024 or self.marker.is_symlink():
                raise ValueError()
            record = json.loads(self.marker.read_bytes())
            revisions = record["revisions"]
            if (not isinstance(revisions, list) or not revisions or len(revisions) > 10000
                    or any(type(r) is not int or not 1 <= r <= MAX_REVISION for r in revisions)
                    or revisions != sorted(set(revisions))
                    or record["revision"] != revisions[-1]
                    or any(type(record[k]) is not int or record[k] < 0 for k in ("song_count", "vector_count"))):
                raise ValueError()
            self._prepared = record
            self._state = "ready"
        except (OSError, ValueError, KeyError, TypeError):
            self._state, self._error = "failed", "catalog_prepare_failed"

    def status(self):
        with self._lock:
            self._restore()
            prior = self._prepared or {}
            return {"state": self._state, "revision": prior.get("revision"),
                    "song_count": prior.get("song_count", 0), "vector_count": prior.get("vector_count", 0),
                    "error": self._error, "hash_progress": copy.deepcopy(self._hash_progress)}

    def prepare(self):
        with self._lock:
            self._restore()
            if self._worker and self._worker.is_alive():
                raise ValueError("Catalog preparation already running")
            if self.ingest_busy():
                self._state, self._error = "failed", "ingest_active"
                raise ValueError("Wait until ingest has settled before preparing catalog")
            self._state, self._error = "running", None
            self._epoch_at_start = self.ingest_epoch()
            self._cancel.clear()
            self._hash_progress = None
            self._done.clear()
            self._worker = threading.Thread(target=self._run, name="mobile-catalog-prepare", daemon=True)
            self._worker.start()
            return self.status()

    def cancel(self):
        self._cancel.set()
        return self.status()

    def _progress(self, completed, total, hashed, skipped):
        with self._lock:
            self._hash_progress = dict(completed=completed, total=total, hashed=hashed, skipped=skipped)

    def _run(self):
        started = time.monotonic()
        error = "catalog_prepare_failed"
        try:
            epoch = self._epoch_at_start
            if self.ingest_busy() or self.ingest_epoch() != epoch:
                error = "ingest_active"
                raise ValueError()
            service = self._service or self.catalog_factory()
            # A supplied adapter's root is also the marker/pool owner.
            self.root = service.root
            # Hashing can take hours on a large first library. Its resumable,
            # cancellable pass is outside the bounded catalog publication budget.
            service.prepare_identities(self._cancel.is_set, self._progress)
            if self._cancel.is_set():
                error = "catalog_prepare_cancelled"
                raise ValueError()
            started = time.monotonic()
            catalog = service.snapshot()
            if self.ingest_busy() or self.ingest_epoch() != epoch:
                error = "ingest_active"
                raise ValueError()
            if time.monotonic() - started > self.deadline:
                error = "catalog_prepare_timeout"
                raise ValueError()
            total = {d["sha256"]: d["byte_length"] for d in catalog.descriptors.values()}
            if (len(canonical_json(catalog.manifest)) > MOBILE_MAX_MANIFEST_BYTES
                    or len(catalog.songs) > 50000 or len(catalog.descriptors) > 100000
                    or sum(total.values()) > MOBILE_MAX_VECTOR_TOTAL):
                raise ValueError()
            if self._cancel.is_set():
                error = "catalog_prepare_cancelled"
                raise ValueError()
            with self._lock:
                revisions = sorted(set((self._prepared or {}).get("revisions", []) + [catalog.manifest["revision"]]))
                if len(revisions) > 10000:
                    raise ValueError()
                record = {"revision": catalog.manifest["revision"], "song_count": len(catalog.songs),
                          "vector_count": len(catalog.descriptors), "revisions": revisions}
                pending = self.root / ".mobile-prepared.pending"
                with pending.open("wb") as stream:
                    stream.write(canonical_json(record))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(pending, self.marker)
                self._service = service
                self._cache(catalog)
                self._prepared = record
                self._state, self._error = "ready", None
        except Exception:
            logger.exception("Mobile catalog preparation failed")
            with self._lock:
                self._state, self._error = "failed", error
        finally:
            self._done.set()

    def wait(self, timeout):
        return self._done.wait(timeout)

    def _catalog(self, revision=None):
        self._restore()
        if not self._prepared:
            raise KeyError("Catalog not prepared")
        revision = self._prepared["revision"] if revision is None else revision
        if type(revision) is not int or revision not in self._prepared["revisions"]:
            raise KeyError("Prepared revision not found")
        if revision not in self._catalogs:
            self._service = self._service or self.catalog_factory()
            self._cache(self._service.load(revision))
        self._catalogs.move_to_end(revision)
        return self._catalogs[revision]

    def _cache(self, catalog):
        self._catalogs[catalog.manifest["revision"]] = catalog
        while len(self._catalogs) > 2:
            self._catalogs.popitem(last=False)

    def manifest(self, revision=None, base_revision=None):
        with self._lock:
            catalog = self._catalog(revision)
            manifest = copy.deepcopy(catalog.manifest)
            if base_revision is not None:
                base = self._catalog(base_revision)
                if base.manifest["revision"] >= manifest["revision"]:
                    raise ValueError("Base must precede target revision")
                manifest["base_revision"] = base.manifest["revision"]
                manifest["base_sha256"] = base.manifest["manifest_sha256"]
                manifest["removed_song_ids"] = sorted(set(base.songs) - set(catalog.songs))
                inherited = {d["sha256"] for d in base.descriptors.values()}
                manifest["included_blobs"] = sorted({d["sha256"] for d in catalog.descriptors.values()} - inherited)
                del manifest["manifest_sha256"]
                manifest["manifest_sha256"] = sha256(canonical_json(manifest))
            return manifest

    def blob(self, digest):
        with self._lock:
            self._restore()
            if not self._prepared:
                raise KeyError("Catalog not prepared")
            # Never serve an orphan in the pool from an unsuccessful export.
            for revision in reversed(self._prepared["revisions"]):
                catalog = self._catalog(revision)
                desc = next((d for d in catalog.descriptors.values() if d["sha256"] == digest), None)
                if desc:
                    path = self._service.blob(digest)
                    size = path.stat().st_size
                    if path.is_symlink() or not 0 < size <= MAX_VECTOR_BYTES or size != desc["byte_length"]:
                        raise ValueError("Invalid prepared vector")
                    data = path.read_bytes()
                    if sha256(data) != digest:
                        raise ValueError("Invalid prepared vector")
                    return data
            raise KeyError("Prepared vector not found")
