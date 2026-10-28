"""Media contract tests: disposable files and authenticated in-process ASGI only."""
import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from api_security import ApiSecurity, SecuritySettings
from media_service import MediaService, create_media_router


@pytest.fixture
def media(tmp_path):
    music, generated, private = (tmp_path / name for name in ("music", "generated", "private"))
    music.mkdir()
    generated.mkdir()
    track = music / "track.mp3"
    track.write_bytes(b"0123456789")
    songs = {1: {"filepath": str(track)}}
    service = MediaService(music, [generated], private, songs.get)
    app = FastAPI()
    app.include_router(service.router())
    ApiSecurity(SecuritySettings(password="test-password", signing_secret="x" * 32,
                                 allowed_hosts=("radio.test",))).install(app)
    return service, app, songs, music, generated, private


def request(app, path="/api/media/tracks/1/stream", method="GET", headers=None, auth=True):
    async def run():
        transport = httpx.ASGITransport(app=app, client=("192.0.2.8", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://radio.test") as client:
            if auth:
                login = await client.post("/api/auth/login", json={"password": "test-password"},
                                          headers={"Origin": "http://radio.test"})
                assert login.status_code == 200
            return await client.request(method, path, headers=headers)
    return asyncio.run(run())


def test_media_requires_authentication(media):
    service, app, _, _, generated, _ = media
    source = generated / "voice.wav"
    source.write_bytes(b"voice")
    resource = service.register_generated(source, "session")
    for path in ("/api/media/tracks/1/stream", f"/api/media/resources/{resource}/stream"):
        assert request(app, path, auth=False).status_code == 401
        assert request(app, path, method="HEAD", auth=False).status_code == 401


def test_native_resolution_uses_same_track_allowlist(media):
    service, _, songs, music, generated, _ = media
    assert service.resolve_track(1) == (music / 'track.mp3').resolve()
    outside = generated / 'outside.wav'
    outside.write_bytes(b'outside')
    songs[2] = {'filepath': str(outside)}
    with pytest.raises(ValueError):
        service.resolve_track(2)


@pytest.mark.parametrize("range_value,status,body,content_range", [
    (None, 200, b"0123456789", None),
    ("bytes=0-2", 206, b"012", "bytes 0-2/10"),
    ("bytes=3-6", 206, b"3456", "bytes 3-6/10"),
    ("bytes=7-", 206, b"789", "bytes 7-9/10"),
    ("bytes=-3", 206, b"789", "bytes 7-9/10"),
    ("bytes=-999", 206, b"0123456789", "bytes 0-9/10"),
    ("bytes=8-999", 206, b"89", "bytes 8-9/10"),
    ("bytes=10-", 416, b"", "bytes */10"),
    ("bytes=4-2", 416, b"", "bytes */10"),
    ("bytes=-0", 416, b"", "bytes */10"),
    ("bytes=no", 416, b"", "bytes */10"),
    ("bytes=0-1,4-5", 200, b"0123456789", None),
    ("items=1-2", 200, b"0123456789", None),
])
def test_ranges_and_headers(media, range_value, status, body, content_range):
    _, app, *_ = media
    headers = {"Range": range_value} if range_value else {}
    response = request(app, headers=headers)
    assert response.status_code == status
    assert response.content == body
    assert response.headers.get("content-range") == content_range
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(body))
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "etag" in response.headers


def test_head_and_if_range(media):
    _, app, *_ = media
    full = request(app)
    head = request(app, method="HEAD", headers={"Range": "bytes=2-5"})
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == "10"
    assert "content-range" not in head.headers
    assert head.headers["etag"] == full.headers["etag"]
    match = request(app, headers={"Range": "bytes=2-5", "If-Range": full.headers["etag"]})
    assert match.status_code == 206 and match.content == b"2345"
    stale = request(app, headers={"Range": "bytes=2-5", "If-Range": '"stale"'})
    assert stale.status_code == 200 and stale.content == full.content
    date_match = request(app, headers={"Range": "bytes=2-5", "If-Range": full.headers["last-modified"]})
    assert date_match.status_code == 206


@pytest.mark.parametrize("extension,mime", [
    ("mp3", "audio/mpeg"), ("wav", "audio/wav"), ("flac", "audio/flac"),
    ("ogg", "audio/ogg"), ("m4a", "audio/mp4"), ("aac", "audio/aac"), ("opus", "audio/ogg"),
])
def test_supported_formats_and_empty_files(media, extension, mime):
    _, app, songs, music, _, _ = media
    empty = music / ("empty." + extension)
    empty.write_bytes(b"")
    songs[2] = {"filepath": str(empty)}
    response = request(app, "/api/media/tracks/2/stream")
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-type"] == mime
    response = request(app, "/api/media/tracks/2/stream", headers={"Range": "bytes=0-"})
    assert response.status_code == 416 and response.headers["content-range"] == "bytes */0"


def test_paths_are_resolved_from_ids_and_jailed(media, tmp_path):
    _, app, songs, music, _, _ = media
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"outside")
    unsupported = music / "script.html"
    unsupported.write_text("not media")
    songs.update({2: {"filepath": str(outside)}, 3: {"filepath": str(music / "missing.mp3")},
                  4: {"filepath": str(unsupported)}, 5: {"filepath": str(music / ".." / "outside.mp3")}})
    for song_id in (2, 3, 4, 5, 999):
        assert request(app, f"/api/media/tracks/{song_id}/stream").status_code == 404
    for resource in ("not-an-id", "..%2Foutside.mp3", "0" * 64):
        assert request(app, f"/api/media/resources/{resource}/stream").status_code == 404
    # Query strings never select an arbitrary path.
    assert request(app, "/api/media/tracks/1/stream?path=" + str(outside)).content == b"0123456789"


def test_symlink_escape_is_rejected(media, tmp_path):
    service, app, songs, music, generated, _ = media
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    link = music / "escape.wav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    songs[2] = {"filepath": str(link)}
    assert request(app, "/api/media/tracks/2/stream").status_code == 404
    generated_link = generated / "escape.wav"
    generated_link.symlink_to(outside)
    with pytest.raises(ValueError):
        service.register_generated(generated_link, "session")


def test_generated_copy_is_stable_persisted_and_survives_source_cleanup(media):
    service, app, songs, music, generated, private = media
    source = generated / "voice.wav"
    source.write_bytes(b"voice audio")
    first = service.register_generated(source, "session-a")
    assert service.register_generated(source, "session-b") == first
    source.unlink()
    assert request(app, f"/api/media/resources/{first}/stream").content == b"voice audio"
    restarted = MediaService(music, [generated], private, songs.get)
    restarted.cleanup()
    assert request(app, f"/api/media/resources/{first}/stream").status_code == 200
    restarted.release_session("session-a")
    restarted.cleanup()
    assert request(app, f"/api/media/resources/{first}/stream").status_code == 200
    restarted.release_session("session-b")
    restarted.cleanup()
    assert request(app, f"/api/media/resources/{first}/stream").status_code == 404


def test_generated_validation_and_quota(media, tmp_path):
    _, _, songs, music, generated, _ = media
    service = MediaService(music, [generated], tmp_path / "limited", songs.get,
                           max_resource_bytes=5, max_total_bytes=8)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"abc")
    unsupported = generated / "bad.exe"
    unsupported.write_bytes(b"abc")
    for path in (outside, unsupported, generated / "missing.wav"):
        with pytest.raises(ValueError):
            service.register_generated(path, "session")
    source = generated / "large.wav"
    source.write_bytes(b"123456")
    with pytest.raises(ValueError):
        service.register_generated(source, "session")
    source.write_bytes(b"12345")
    service.register_generated(source, "session")
    source.write_bytes(b"abcde")
    with pytest.raises(ValueError):
        service.register_generated(source, "session")
    assert source.read_bytes() == b"abcde"


def test_expired_leases_and_renewal(media, monkeypatch):
    service, app, _, _, generated, _ = media
    clock = [100.0]
    monkeypatch.setattr(service, "_clock", lambda: clock[0])
    source = generated / "voice.wav"
    source.write_bytes(b"voice")
    resource = service.register_generated(source, "session")
    clock[0] += service.lease_seconds - 1
    service.renew_session("session")
    clock[0] += 2
    service.cleanup()
    assert request(app, f"/api/media/resources/{resource}/stream").status_code == 200
    clock[0] += service.lease_seconds
    service.cleanup()
    assert request(app, f"/api/media/resources/{resource}/stream").status_code == 404


def test_active_stream_prevents_cleanup_and_closes_on_disconnect(media):
    service, _, songs, music, generated, private = media
    source = generated / "voice.wav"
    source.write_bytes(b"x" * (service.chunk_size * 2 + 1))
    resource = service.register_generated(source, "session")
    same_root = MediaService(music, [generated], private, songs.get)
    # Exercise the real ASGI response with an explicit send-side disconnect.
    app = FastAPI()
    app.include_router(service.router())
    ApiSecurity(SecuritySettings(allowed_hosts=("radio.test",))).install(app)
    chunks = []

    async def run():
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": f"/api/media/resources/{resource}/stream", "query_string": b"",
                 "headers": [(b"host", b"radio.test")], "client": ("127.0.0.1", 1234),
                 "server": ("radio.test", 80)}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                chunks.append(message["body"])
                service.release_session("session")
                same_root.cleanup()
                assert any(private.glob("*.wav"))
                raise OSError("client disconnected")
        with pytest.raises(Exception):
            await app(scope, receive, send)
    asyncio.run(run())
    assert len(chunks) == 1 and len(chunks[0]) <= service.chunk_size
    service.cleanup()
    assert not list(private.glob("*.wav"))


def test_stream_cap_is_global_and_released_on_failure(media):
    service, _, _, _, _, _ = media
    service.max_streams = 1
    # Reserve one response without consuming it, then run a second through auth.
    first = service._stream_response(Path(service.music_root) / "track.mp3", "GET", {})
    try:
        second = service._stream_response(Path(service.music_root) / "track.mp3", "GET", {})
        assert second.status_code == 503
    finally:
        first.close()
    next_response = service._stream_response(Path(service.music_root) / "track.mp3", "GET", {})
    assert next_response.status_code == 200
    next_response.close()


def test_global_cap_includes_other_instances_with_same_private_root(media):
    service, _, songs, music, generated, private = media
    other = MediaService(music, [generated], private, songs.get, max_streams=1)
    held = service._stream_response(music / "track.mp3", "GET", {})
    try:
        denied = other._stream_response(music / "track.mp3", "GET", {})
        assert denied.status_code == 503
    finally:
        held.close()


def test_file_closes_if_response_headers_cannot_be_sent(media):
    service, _, _, music, _, _ = media
    service.max_streams = 1
    response = service._stream_response(music / "track.mp3", "GET", {})

    async def run():
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            raise OSError("disconnected before headers")
        with pytest.raises(Exception):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    asyncio.run(run())
    following = service._stream_response(music / "track.mp3", "GET", {})
    assert following.status_code == 200
    following.close()


def test_router_factory_is_lazy_and_unauthorized_requests_do_not_create_catalog(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    track = music / "track.mp3"
    track.write_bytes(b"audio")
    private = tmp_path / "private"
    calls = []

    def factory():
        calls.append(True)
        return MediaService(music, [], private, lambda song_id: {"filepath": str(track)} if song_id == 1 else None)

    app = FastAPI()
    app.include_router(create_media_router(factory))
    ApiSecurity(SecuritySettings(password="test-password", signing_secret="x" * 32,
                                 allowed_hosts=("radio.test",))).install(app)
    assert not private.exists() and not calls
    for path in ("/api/media/tracks/1/stream", "/api/media/resources/" + "0" * 64 + "/stream"):
        assert request(app, path, auth=False).status_code == 401
    assert not private.exists() and not calls
    assert request(app).content == b"audio"
    assert request(app, method="HEAD").status_code == 200
    assert len(calls) == 1
    assert (private / "catalog.sqlite3").exists()


def test_resolve_resource_only_returns_cataloged_approved_private_copies(media):
    service, _, _, _, generated, private = media
    source = generated / "voice.wav"
    source.write_bytes(b"voice")
    resource = service.register_generated(source, "session")
    resolved = service.resolve_resource(resource)
    assert isinstance(resolved, Path)
    assert resolved.is_relative_to(private) and resolved.read_bytes() == b"voice"
    assert resolved != source
    for invalid in ("../voice.wav", "0" * 64, str(source), "not-an-id"):
        with pytest.raises(ValueError):
            service.resolve_resource(invalid)
    resolved.unlink()
    with pytest.raises(ValueError):
        service.resolve_resource(resource)
