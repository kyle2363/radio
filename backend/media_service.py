"""Private, ID-addressed audio delivery; install ApiSecurity around the application.

This module has no radio runtime imports. Callers supply the song lookup and approved
roots. Generated audio is copied, never moved, into a persistent private catalog.
Leases and quotas survive restarts; open stream references are process-local. Run
one API worker (as required by ApiSecurity) and call cleanup periodically.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import timezone
from email.utils import formatdate, parsedate_to_datetime
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

import anyio
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response, StreamingResponse


_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
         ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".opus": "audio/ogg"}
_RESOURCE_ID = re.compile(r"[a-f0-9]{64}\Z")


def create_media_router(service_factory):
    """Build routes without touching storage; initialize once on authorized use.

    ApiSecurity must wrap the application so rejected requests never reach these
    routes. Synchronous route functions run filesystem/SQLite work in FastAPI's
    worker pool; the resulting response streams asynchronously.
    """
    router = APIRouter(tags=["media"])
    factory_lock = threading.Lock()
    instance = None

    def service():
        nonlocal instance
        with factory_lock:
            if instance is None:
                instance = service_factory()
            return instance

    @router.api_route("/api/media/tracks/{song_id}/stream", methods=["GET", "HEAD"])
    def track(song_id: int, request: Request):
        return service().track_response(song_id, request)

    @router.api_route("/api/media/resources/{resource_id}/stream", methods=["GET", "HEAD"])
    def resource(resource_id: str, request: Request):
        return service().resource_response(resource_id, request)

    return router


class _RootState:
    def __init__(self):
        self.lock = threading.RLock()
        self.resources = Counter()
        self.streams = 0


class _MediaResponse(StreamingResponse):
    """Close even if send/start fails or Starlette cancels on disconnect."""
    def __init__(self, service, handle, length, headers, status, mime, resource_id):
        self._service = service
        self._handle = handle
        self._resource_id = resource_id
        self._closed = False

        async def chunks():
            remaining = length
            while remaining:
                block = await anyio.to_thread.run_sync(handle.read, min(service.chunk_size, remaining))
                if not block:
                    # The source changed after headers; terminate rather than send
                    # a successful truncated response with an incorrect length.
                    raise OSError("Media changed while streaming")
                remaining -= len(block)
                yield block

        super().__init__(chunks(), status_code=status, headers=headers, media_type=mime)

    def close(self):
        with self._service._state.lock:
            if not self._closed:
                self._closed = True
                try:
                    self._handle.close()
                finally:
                    self._service._state.streams -= 1
                    if self._resource_id:
                        self._service._state.resources[self._resource_id] -= 1
                        if not self._service._state.resources[self._resource_id]:
                            del self._service._state.resources[self._resource_id]

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.close()


class MediaService:
    """Serve approved audio using stable IDs, without transcoding or source writes.

    lookup_song(id) returns a mapping containing filepath, or None. Invalid file
    locations/formats are deliberately indistinguishable from missing IDs. The
    router must be protected by the application's API authentication middleware.
    Registration raises ValueError for invalid source/session or quota exhaustion.
    """
    chunk_size = 64 * 1024
    _states_lock = threading.Lock()
    _states = {}

    def __init__(self, music_root, generated_roots, resource_root, lookup_song,
                 max_streams=32, *, lease_seconds=12 * 60 * 60,
                 max_resource_bytes=256 * 1024 * 1024, max_total_bytes=2 * 1024 * 1024 * 1024):
        for name, value in (("max_streams", max_streams), ("lease_seconds", lease_seconds),
                            ("max_resource_bytes", max_resource_bytes), ("max_total_bytes", max_total_bytes)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self.music_root = Path(music_root).resolve()
        self.generated_roots = tuple(Path(root).resolve() for root in generated_roots)
        self.resource_root = Path(resource_root).resolve()
        self.resource_root.mkdir(parents=True, exist_ok=True)
        self.lookup_song = lookup_song
        self.max_streams = max_streams
        self.lease_seconds = lease_seconds
        self.max_resource_bytes = max_resource_bytes
        self.max_total_bytes = max_total_bytes
        self._clock = time.time
        with self._states_lock:
            self._state = self._states.setdefault(self.resource_root, _RootState())
        self._catalog = self.resource_root / "catalog.sqlite3"
        with self._state.lock, self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, extension TEXT NOT NULL,
                    size INTEGER NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leases (
                    resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL, expires REAL NOT NULL,
                    PRIMARY KEY(resource_id, session_id)
                );
                CREATE INDEX IF NOT EXISTS lease_sessions ON leases(session_id);
            """)

    @contextmanager
    def _connection(self):
        # The catalog itself must not be redirected outside private storage.
        if self._catalog.is_symlink() or self._catalog.resolve().parent != self.resource_root:
            raise ValueError("Invalid resource catalog")
        connection = sqlite3.connect(str(self._catalog), timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _approved(path, roots):
        try:
            resolved = Path(path).resolve(strict=True)
            if (resolved.suffix.lower() not in _MIME or not resolved.is_file()
                    or not any(resolved.is_relative_to(root) for root in roots)):
                raise ValueError("Unapproved media file")
            return resolved
        except (OSError, RuntimeError, TypeError) as exc:
            raise ValueError("Unapproved media file") from exc

    @staticmethod
    def _open_checked(path, roots):
        resolved = MediaService._approved(path, roots)
        handle = resolved.open("rb")
        try:
            # Check again after opening to reject a swapped symlink/junction.
            current = MediaService._approved(path, roots)
            if current != resolved or not os.path.samestat(os.fstat(handle.fileno()), current.stat()):
                raise ValueError("Media location changed")
            return handle, resolved
        except BaseException:
            handle.close()
            raise

    def _resource_path(self, resource_id, extension):
        if not _RESOURCE_ID.fullmatch(resource_id) or extension not in _MIME:
            raise ValueError("Invalid resource")
        return self.resource_root / (resource_id + extension)

    def router(self):
        return create_media_router(lambda: self)

    def resolve_track(self, song_id):
        song = self.lookup_song(song_id) if type(song_id) is int and song_id > 0 else None
        if not song or not song.get('filepath'):
            raise ValueError('Unknown track')
        return self._approved(song['filepath'], (self.music_root,))

    def track_response(self, song_id, request):
        song = self.lookup_song(song_id) if song_id > 0 else None
        if not song or not song.get("filepath"):
            raise HTTPException(404, "Media not found")
        try:
            path = self._approved(song["filepath"], (self.music_root,))
        except ValueError:
            raise HTTPException(404, "Media not found") from None
        return self._stream_response(path, request.method, request.headers)

    def resolve_resource(self, resource_id):
        """Resolve a cataloged private copy for trusted native playback only.

        Returns an approved Path, never a source path. The caller must retain its
        session lease for the duration of native playback; this lookup itself does
        not open or pin the file. Do not return this path in a public API payload.
        """
        if not isinstance(resource_id, str) or not _RESOURCE_ID.fullmatch(resource_id):
            raise ValueError("Invalid resource ID")
        with self._state.lock, self._connection() as db:
            row = db.execute("SELECT extension FROM resources WHERE id=?", (resource_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown resource ID")
            return self._approved(self._resource_path(resource_id, row[0]), (self.resource_root,))

    def resource_response(self, resource_id, request):
        with self._state.lock:
            try:
                path = self.resolve_resource(resource_id)
            except ValueError:
                raise HTTPException(404, "Media not found") from None
            # Keep lookup and open-reference acquisition atomic with cleanup.
            return self._stream_response(path, request.method, request.headers, resource_id)

    @staticmethod
    def _range(value, size):
        """Return inclusive bounds, None to ignore, or raise for unsatisfiable bytes."""
        if not value or not value.startswith("bytes="):
            return None
        value = value[6:]
        if "," in value:
            return None  # Multipart is optional; serve the complete representation.
        if len(value) > 128 or not re.fullmatch(r"[0-9]*-[0-9]*", value):
            raise ValueError("Invalid range")
        left, right = value.split("-")
        if not size or not (left or right):
            raise ValueError("Unsatisfiable range")
        if not left:
            suffix = int(right)
            if suffix == 0:
                raise ValueError("Unsatisfiable range")
            return max(0, size - suffix), size - 1
        start = int(left)
        end = min(int(right), size - 1) if right else size - 1
        if start >= size or end < start:
            raise ValueError("Unsatisfiable range")
        return start, end

    @staticmethod
    def _if_range_matches(value, etag, modified):
        if value == etag:
            return True
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return int(modified) <= date.timestamp()
        except (TypeError, ValueError, OverflowError):
            return False

    def _stream_response(self, path, method, request_headers, resource_id=None):
        roots = (self.resource_root,) if resource_id else (self.music_root,)
        with self._state.lock:
            if method != "HEAD" and self._state.streams >= self.max_streams:
                return Response(status_code=503, headers={"Retry-After": "1", "Cache-Control": "no-store"})
            try:
                handle, resolved = self._open_checked(path, roots)
            except (OSError, ValueError):
                raise HTTPException(404, "Media not found") from None
            try:
                stat = os.fstat(handle.fileno())
                size = stat.st_size
                identity = f"{stat.st_dev}:{stat.st_ino}:{size}:{stat.st_mtime_ns}"
                etag = '"' + (resource_id or hashlib.sha256(identity.encode()).hexdigest()) + '"'
                headers = {"Accept-Ranges": "bytes", "ETag": etag,
                           "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
                           "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}
                bounds = None
                if_range = request_headers.get("if-range") or request_headers.get("If-Range")
                # RFC 9110: Range applies only to GET; HEAD describes the full
                # representation and never holds an active streaming reference.
                if method == "GET" and (not if_range or self._if_range_matches(if_range, etag, stat.st_mtime)):
                    try:
                        bounds = self._range(request_headers.get("range") or request_headers.get("Range"), size)
                    except ValueError:
                        headers.update({"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                        return Response(status_code=416, headers=headers, media_type=_MIME[resolved.suffix.lower()])
                start, end = bounds if bounds else (0, size - 1)
                length = max(0, end - start + 1)
                headers["Content-Length"] = str(length)
                status = 206 if bounds else 200
                if bounds:
                    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                mime = _MIME[resolved.suffix.lower()]
                if method == "HEAD":
                    return Response(status_code=status, headers=headers, media_type=mime)
                handle.seek(start)
                response = _MediaResponse(self, handle, length, headers, status, mime, resource_id)
                self._state.streams += 1
                if resource_id:
                    self._state.resources[resource_id] += 1
                handle = None  # Response now owns the handle, including on failure.
                return response
            finally:
                if handle is not None:
                    handle.close()

    @staticmethod
    def _session(session_id):
        if not isinstance(session_id, (str, int)) or isinstance(session_id, bool):
            raise ValueError("Invalid session ID")
        value = str(session_id)
        if not value or len(value) > 256:
            raise ValueError("Invalid session ID")
        return value

    def register_generated(self, path, session_id):
        """Copy approved generated audio and acquire/renew this session's lease."""
        session_id = self._session(session_id)
        try:
            source, resolved = self._open_checked(path, self.generated_roots)
        except (OSError, ValueError) as exc:
            raise ValueError("Unapproved generated audio") from exc
        temporary = self.resource_root / (uuid.uuid4().hex + ".part")
        try:
            extension = resolved.suffix.lower()
            digest = hashlib.sha256(b"radio-media-v1\0" + extension.encode() + b"\0")
            before = os.fstat(source.fileno())
            size = 0
            if before.st_size > self.max_resource_bytes:
                raise ValueError("Generated resource exceeds size limit")
            with temporary.open("xb") as output:
                while True:
                    block = source.read(self.chunk_size)
                    if not block:
                        break
                    size += len(block)
                    if size > self.max_resource_bytes:
                        raise ValueError("Generated resource exceeds size limit")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Generated source changed during copy")
            resource_id = digest.hexdigest()
            target = self._resource_path(resource_id, extension)
            with self._state.lock, self._connection() as db:
                existing = db.execute("SELECT size FROM resources WHERE id=?", (resource_id,)).fetchone()
                if existing is None:
                    total = db.execute("SELECT COALESCE(SUM(size), 0) FROM resources").fetchone()[0]
                    if total + size > self.max_total_bytes:
                        raise ValueError("Generated resource storage quota exceeded")
                # Validate any existing path before replacing it; never write through
                # a symlink or junction. replace affects only our private directory.
                if target.exists() and target.resolve().parent != self.resource_root:
                    raise ValueError("Invalid generated resource location")
                if existing is None or not target.exists():
                    os.replace(temporary, target)
                db.execute("INSERT OR IGNORE INTO resources VALUES (?, ?, ?, ?)",
                           (resource_id, extension, size, self._clock()))
                db.execute("INSERT INTO leases VALUES (?, ?, ?) ON CONFLICT(resource_id, session_id) "
                           "DO UPDATE SET expires=excluded.expires",
                           (resource_id, session_id, self._clock() + self.lease_seconds))
            return resource_id
        finally:
            source.close()
            temporary.unlink(missing_ok=True)

    def renew_session(self, session_id):
        session_id = self._session(session_id)
        with self._state.lock, self._connection() as db:
            db.execute("UPDATE leases SET expires=? WHERE session_id=?",
                       (self._clock() + self.lease_seconds, session_id))

    def release_session(self, session_id):
        session_id = self._session(session_id)
        with self._state.lock, self._connection() as db:
            db.execute("DELETE FROM leases WHERE session_id=?", (session_id,))

    def cleanup(self):
        """Delete only cataloged, unleased, unopened copies; never source audio."""
        removed = 0
        with self._state.lock, self._connection() as db:
            db.execute("DELETE FROM leases WHERE expires<=?", (self._clock(),))
            rows = db.execute("SELECT id, extension FROM resources r WHERE NOT EXISTS "
                              "(SELECT 1 FROM leases l WHERE l.resource_id=r.id)").fetchall()
            for resource_id, extension in rows:
                if self._state.resources[resource_id]:
                    continue
                try:
                    path = self._resource_path(resource_id, extension)
                    # Refuse redirection even for deletion; remove only ordinary
                    # copies belonging to this catalog in the private root.
                    if path.is_symlink() or path.resolve().parent != self.resource_root:
                        continue
                    path.unlink(missing_ok=True)
                except (OSError, ValueError):
                    continue
                db.execute("DELETE FROM resources WHERE id=?", (resource_id,))
                removed += 1
        return removed
