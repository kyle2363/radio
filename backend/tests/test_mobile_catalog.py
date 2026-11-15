import base64
import json
from pathlib import Path
import threading

import pytest

from fastapi import FastAPI

from mobile_catalog import IngestActivity, PreparedCatalog
from mobile_hosting import MobileHosting, create_mobile_hosting_router
from mobile_gateway import create_mobile_gateway
from portable.catalog import canonical_json, sha256
from tests.test_mobile_pairing import request, setup
from tests.test_portable_server import source


def prepared(tmp_path):
    service, _ = source(tmp_path)
    result = PreparedCatalog(lambda: service, root=service.root, ingest_busy=lambda: False)
    return result, service


def finish(job):
    assert job.wait(5), "prepare job did not finish"
    assert job.status()["state"] != "running"


def test_vector_batches_are_revision_bound_ordered_and_bounded(tmp_path, monkeypatch):
    import mobile_catalog
    job, service = prepared(tmp_path)
    job.prepare(); finish(job)
    manifest = job.manifest()
    digests = list(dict.fromkeys(d["sha256"] for d in manifest["embeddings"]))
    assert digests
    expected = b"".join(job.blob(digest) for digest in digests)
    assert job.batch(manifest["revision"], manifest["manifest_sha256"], digests) == expected
    for bad in ([], digests * 65, [digests[0], digests[0]], ["0" * 64]):
        with pytest.raises((ValueError, KeyError)):
            job.batch(manifest["revision"], manifest["manifest_sha256"], bad)
    with pytest.raises(ValueError):
        job.batch(manifest["revision"], "0" * 64, digests)
    monkeypatch.setattr(mobile_catalog, "MOBILE_BATCH_BYTES", len(expected) - 1)
    monkeypatch.setattr(service, "blob", lambda digest: pytest.fail("Oversize batch must reject before reading blobs"))
    with pytest.raises(ValueError):
        job.batch(manifest["revision"], manifest["manifest_sha256"], digests)


def test_prepared_lyrics_are_revision_bound_and_lyrics_only_changes_bump_revision(tmp_path):
    from db import database
    job, service = prepared(tmp_path)
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics=? WHERE id=1", ("  first\r\n\r\nverse  \r",))
        conn.commit()
    job.prepare(); finish(job)
    first = job.manifest()
    resource = job.lyrics(first["revision"], first["manifest_sha256"], "1")
    assert resource["status"] == "available"
    assert resource["text"] == "first\n\nverse"
    assert resource["text_sha256"] == sha256(b"first\n\nverse")
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics=? WHERE id=1", ("second",))
        conn.commit()
    job.prepare(); finish(job)
    second = job.manifest()
    assert second["revision"] == first["revision"] + 1
    assert second["manifest_sha256"] != first["manifest_sha256"]
    assert job.lyrics(first["revision"], first["manifest_sha256"], "1")["text"] == "first\n\nverse"
    assert job.lyrics(second["revision"], second["manifest_sha256"], "1")["text"] == "second"
    assert job.lyrics(second["revision"], second["manifest_sha256"], "2")["reason"] == "missing"
    with pytest.raises(ValueError):
        job.lyrics(first["revision"], "0" * 64, "1")
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics=? WHERE id=1", ("\\" * 65537,))
        conn.commit()
    job.prepare(); finish(job)
    large = job.manifest()
    unavailable = job.lyrics(large["revision"], large["manifest_sha256"], "1")
    assert unavailable["status"] == "unavailable" and unavailable["reason"] == "too_large"
    # Worst case JSON escaping of control characters stays within 512 KiB.
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics=? WHERE id=1", ("\x01" * 65536,))
        conn.commit()
    job.prepare(); finish(job)
    escaped = job.manifest()
    assert len(canonical_json(job.lyrics(escaped["revision"], escaped["manifest_sha256"], "1"))) <= 512 * 1024


def test_gateway_prepared_lyrics_exact_contract_and_auth(tmp_path):
    job, _ = prepared(tmp_path)
    from db import database
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics='lyric text' WHERE id=1")
        conn.commit()
    job.prepare(); finish(job)
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing, catalog=job)
    app.state.admission.enable(1)
    manifest = job.manifest()
    token = store.issue_device("Phone", ["catalog:read"])['token']
    headers = {"Authorization": "Bearer " + token}
    prefix = "/api/portable/v1/lyrics?revision={}&manifest_sha256={}&song_id=1".format(
        manifest["revision"], manifest["manifest_sha256"])
    response = request(app, "GET", prefix, headers)
    assert response.status_code == 200
    assert response.json()["text"] == "lyric text"
    assert response.headers["cache-control"] == "no-store"
    assert request(app, "GET", prefix).status_code == 401
    assert request(app, "GET", prefix, headers, b"unexpected").status_code == 400
    assert request(app, "GET", prefix, dict(headers, Range="bytes=0-10")).status_code == 400
    assert request(app, "GET", "/api/songs/1/lyrics", headers).status_code == 404
    assert request(app, "GET", prefix.replace(manifest["manifest_sha256"], "0" * 64), headers).status_code == 409
    repeated = prefix + "&song_id=1"
    assert request(app, "GET", repeated, headers).status_code == 400
    index_path = job.root / str(manifest["revision"]) / "lyrics" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    resource_path = index_path.parent / index["songs"]["1"]["file"]
    resource_path.write_bytes(b"corrupt")
    assert request(app, "GET", prefix, headers).status_code == 503
    # Restore the immutable fixture resource for the following missing-index check.
    resource_path.write_bytes(canonical_json({"song_id": "1", "text": "lyric text",
                                             "text_sha256": sha256(b"lyric text")}))
    caps = request(app, "GET", "/api/portable/v1/capabilities", headers).json()
    assert (caps["plain_lyrics"], caps["plain_lyrics_version"], caps["plain_lyrics_max_text_bytes"]) == (True, 1, 65536)
    (job.root / str(manifest["revision"]) / "lyrics" / "index.json").unlink()
    missing_snapshot = request(app, "GET", prefix, headers)
    assert missing_snapshot.status_code == 404
    assert missing_snapshot.json()["detail"] == "lyrics_not_prepared"
    for query in ("song_id=1&revision=1&manifest_sha256=" + manifest["manifest_sha256"],
                  "revision=1&manifest_sha256=" + manifest["manifest_sha256"] + "&song_id=1&x=1",
                  "revision=01&manifest_sha256=" + manifest["manifest_sha256"] + "&song_id=1"):
        assert request(app, "GET", "/api/portable/v1/lyrics?" + query, headers).status_code == 400
    assert request(app, "POST", "/api/portable/v1/lyrics?" + prefix.split("?", 1)[1], headers).status_code == 404
    assert request(app, "GET", prefix, {"Authorization": "Bearer " + store.issue_device("Profile", ["profile:read"])['token']}).status_code == 403
    store.revoke_device(store.authorize(token)["device_id"])
    assert request(app, "GET", prefix, headers).status_code == 401


def test_lyrics_legacy_sidecar_is_not_borrowed_and_prepare_repairs_it(tmp_path):
    from mobile_catalog import LyricsNotPrepared
    job, service = prepared(tmp_path)
    from db import database
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics='old revision text' WHERE id=1")
        conn.commit()
    job.prepare(); finish(job)
    old = job.manifest()
    old_index = service.root / str(old["revision"]) / "lyrics" / "index.json"
    old_index.unlink()
    with pytest.raises(LyricsNotPrepared):
        job.lyrics(old["revision"], old["manifest_sha256"], "1")
    # Same catalog source digest still requires a new revision after a missing
    # sidecar, preserving the legacy revision as a read-only prepared snapshot.
    job.prepare(); finish(job)
    current = job.manifest()
    assert current["revision"] == old["revision"] + 1
    assert job.lyrics(current["revision"], current["manifest_sha256"], "1")["text"] == "old revision text"
    with pytest.raises(LyricsNotPrepared):
        job.lyrics(old["revision"], old["manifest_sha256"], "1")


def test_failed_lyrics_sidecar_publication_keeps_previous_prepared_resource(tmp_path, monkeypatch):
    job, service = prepared(tmp_path)
    from db import database
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics='published text' WHERE id=1")
        conn.commit()
    job.prepare(); finish(job)
    prior = job.manifest()
    prior_payload = job.lyrics(prior["revision"], prior["manifest_sha256"], "1")
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET lyrics='unpublished text' WHERE id=1")
        conn.commit()
    monkeypatch.setattr(service, "_write_lyrics", lambda *args: (_ for _ in ()).throw(OSError("sidecar write failed")))
    job.prepare(); finish(job)
    assert job.status()["state"] == "failed"
    assert job.status()["revision"] == prior["revision"]
    assert job.manifest() == prior
    assert job.lyrics(prior["revision"], prior["manifest_sha256"], "1") == prior_payload


def test_gateway_batch_auth_negotiation_and_strict_queries(tmp_path):
    job, _ = prepared(tmp_path)
    job.prepare(); finish(job)
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing, catalog=job)
    app.state.admission.enable(1)
    prefix = "/api/portable/v1"
    headers = {"Authorization": "Bearer " + store.issue_device("Phone", ["catalog:read"])["token"]}
    response = request(app, "GET", prefix + "/catalog", headers)
    assert response.headers["x-radio-catalog-batches"] == "1"
    manifest = response.json()
    digests = list(dict.fromkeys(d["sha256"] for d in manifest["embeddings"]))
    path = prefix + "/vector-batch?revision=" + str(manifest["revision"]) + "&manifest_sha256=" + manifest["manifest_sha256"] + "&digests=" + ",".join(digests)
    assert request(app, "GET", path).status_code == 401
    other = {"Authorization": "Bearer " + store.issue_device("Other", ["profile:read"])["token"]}
    assert request(app, "GET", path, other).status_code == 403
    batch = request(app, "GET", path, headers)
    assert batch.status_code == 200
    assert batch.content == b"".join(job.blob(d) for d in digests)
    assert len(batch.content) == int(batch.headers["content-length"])
    for bad in (path + "&extra=1", path.replace("revision=1", "revision=01"), path + "," + digests[0], path.replace(manifest["manifest_sha256"], "0" * 64)):
        assert request(app, "GET", bad, headers).status_code == 400
    assert request(app, "POST", path, headers).status_code == 404


def test_prepare_is_explicit_persistent_and_delta_is_read_only(tmp_path):
    job, service = prepared(tmp_path)
    assert job.status()["revision"] is None
    with pytest.raises(KeyError):
        job.manifest()
    job.prepare()
    finish(job)
    first = job.manifest()
    assert job.status()["state"] == "ready"
    assert job.status()["song_count"] == 28
    from db import database
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET title='Second' WHERE id=1")
        conn.commit()
    job.prepare()
    finish(job)
    second = job.manifest()
    delta = job.manifest(base_revision=first["revision"])
    assert delta["base_sha256"] == first["manifest_sha256"]
    assert delta["included_blobs"] == []
    assert not list(service.root.glob("delta-*"))
    assert sha256(canonical_json({k: v for k, v in delta.items() if k != "manifest_sha256"})) == delta["manifest_sha256"]
    again = PreparedCatalog(lambda: service, root=service.root, ingest_busy=lambda: False)
    assert again.manifest() == second
    assert again.manifest(first["revision"]) == first


def test_busy_failure_single_job_and_old_revision_retained(tmp_path):
    job, service = prepared(tmp_path)
    job.prepare()
    finish(job)
    first = job.manifest()
    entered, release = threading.Event(), threading.Event()
    original = service.source
    def blocked(**kwargs):
        entered.set()
        release.wait(4)
        raise RuntimeError("C:/private/secret path")
    service.source = blocked
    job.prepare()
    assert entered.wait(2)
    with pytest.raises(ValueError):
        job.prepare()
    assert job.manifest() == first
    release.set()
    finish(job)
    assert job.status()["state"] == "failed"
    assert "private" not in json.dumps(job.status())
    assert job.manifest() == first
    service.source = original
    job.ingest_busy = lambda: True
    with pytest.raises(ValueError, match="ingest"):
        job.prepare()
    assert job.manifest() == first


def test_gateway_catalog_auth_bounds_prepared_only_and_vector_head(tmp_path):
    job, _ = prepared(tmp_path)
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing, catalog=job)
    app.state.admission.enable(1)
    prefix = "/api/portable/v1"
    token = store.issue_device("Phone", ["catalog:read"])["token"]
    headers = {"Authorization": "Bearer " + token}
    assert request(app, "GET", prefix + "/catalog").status_code == 401
    assert request(app, "GET", prefix + "/catalog", headers).status_code == 409
    assert job.status()["state"] == "idle"
    other = store.issue_device("Profile", ["profile:read"])["token"]
    assert request(app, "GET", prefix + "/catalog", {"Authorization": "Bearer " + other}).status_code == 403
    job.prepare()
    finish(job)
    response = request(app, "GET", prefix + "/catalog", headers)
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    manifest = response.json()
    assert response.content == canonical_json(manifest)
    caps = request(app, "GET", prefix + "/capabilities", headers).json()
    assert caps["catalog_import"] is True
    assert caps["catalog_revision"] == manifest["revision"]
    for query in ("revision=0", "revision=-1", "revision=01", "revision=1&revision=1", "force=true", "revision=1%30", "revision=" + "9" * 100):
        assert request(app, "GET", prefix + "/catalog?" + query, headers).status_code == 400
    assert request(app, "GET", prefix + "/catalog?revision=999", headers).status_code == 404
    assert request(app, "GET", prefix + "/catalog?revision=9223372036854775808", headers).status_code == 400
    assert request(app, "GET", prefix + "/catalog?revision=1&base_revision=1", headers).status_code == 400
    assert request(app, "POST", prefix + "/catalog", headers).status_code == 404
    assert request(app, "HEAD", prefix + "/catalog", headers).status_code == 404
    assert request(app, "GET", prefix + "/catalog", headers, b"x").status_code == 400
    for extra in ({"Origin": "null"}, {"Cookie": "secret=x"}, {"Host": "localhost:18766"}, {"X-Forwarded-For": "127.0.0.1"}):
        assert request(app, "GET", prefix + "/catalog", dict(headers, **extra)).status_code in (400, 403)
    desc = manifest["embeddings"][0]
    path = prefix + "/vectors/" + desc["sha256"]
    raw = request(app, "GET", path, headers)
    head = request(app, "HEAD", path, headers)
    assert sha256(raw.content) == desc["sha256"]
    assert head.content == b""
    assert int(head.headers["content-length"]) == len(raw.content) == desc["byte_length"]
    assert request(app, "HEAD", path).status_code == 401
    assert request(app, "GET", prefix + "/vectors/" + "0" * 64, headers).status_code == 404
    assert request(app, "GET", path + "?x=1", headers).status_code == 404
    assert request(app, "GET", prefix + "/catalog", dict(headers, Range="bytes=0-1")).status_code == 400
    store.revoke_device(store.authorize(token)["device_id"])
    assert request(app, "GET", path, headers).status_code == 401


def test_blob_pool_orphans_and_corruption_are_not_served(tmp_path):
    job, service = prepared(tmp_path)
    job.prepare()
    finish(job)
    orphan = b"orphan"
    digest = sha256(orphan)
    (service.root / "blobs" / (digest + ".bin")).write_bytes(orphan)
    with pytest.raises(KeyError):
        job.blob(digest)
    desc = job.manifest()["embeddings"][0]
    (service.root / "blobs" / (desc["sha256"] + ".bin")).write_bytes(b"x" * desc["byte_length"])
    with pytest.raises(ValueError, match="Invalid prepared vector"):
        job.blob(desc["sha256"])


def test_failed_atomic_marker_publication_keeps_previous_revision(tmp_path, monkeypatch):
    import mobile_catalog
    from db import database
    job, service = prepared(tmp_path)
    job.prepare()
    finish(job)
    prior = job.manifest()
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET title='New' WHERE id=1")
        conn.commit()
    def fail_replace(*args):
        raise OSError("private path must not appear in status")
    monkeypatch.setattr(mobile_catalog.os, "replace", fail_replace)
    job.prepare()
    finish(job)
    assert job.status()["error"] == "catalog_prepare_failed"
    assert job.status()["revision"] == prior["revision"]
    assert job.manifest() == prior
    restored = PreparedCatalog(lambda: service, root=service.root)
    assert restored.manifest() == prior


def test_api_ingest_tracker_releases_on_failures_and_tracks_queued_work():
    activity = IngestActivity()
    @activity.track
    def fail():
        assert activity.busy()
        raise ValueError("expected")
    with pytest.raises(ValueError):
        fail()
    assert not activity.busy() and activity.epoch() == 1
    entered, release = threading.Event(), threading.Event()
    def worker():
        entered.set()
        release.wait(2)
    activity.start(worker)
    assert entered.wait(1)
    assert activity.busy() and activity.epoch() == 2
    release.set()
    # A tracked worker releases in finally before its thread exits.
    for worker_thread in threading.enumerate():
        if worker_thread is not threading.current_thread() and worker_thread.name.endswith("(wrapped)"):
            worker_thread.join(2)
    assert not activity.busy()


def test_gateway_rate_limit_has_bounded_retry_after_and_reauthorizes(tmp_path, monkeypatch):
    import mobile_gateway
    store, identity, pairing = setup(tmp_path)
    app = create_mobile_gateway(store, identity, pairing)
    app.state.admission.enable(1)
    token = store.issue_device("Phone", ["catalog:read"])["token"]
    headers = {"Authorization": "Bearer " + token}
    now = [1000.0]
    monkeypatch.setattr(mobile_gateway.time, "monotonic", lambda: now[0])
    path = "/api/portable/v1/capabilities"
    # Prime middleware and inspect its retained rate-window boundary.
    assert request(app, "GET", path, headers).status_code == 200
    boundary = app.middleware_stack.app
    assert isinstance(boundary, mobile_gateway.GatewayBoundary)
    boundary._global = (1000.0, 1000)
    assert request(app, "GET", path, headers).headers["Retry-After"] == "60"
    now[0] = 1016.2
    response = request(app, "GET", path, headers)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "44"
    now[0] = 1059.9
    assert request(app, "GET", path, headers).headers["Retry-After"] == "1"
    store.revoke_device(store.authorize(token)["device_id"])
    now[0] = 1060.0
    assert request(app, "GET", path, headers).status_code == 401


def test_local_prepare_parent_only_and_status_does_not_initialize(tmp_path):
    service, _ = source(tmp_path)
    calls = []
    def factory():
        calls.append(True)
        return service
    hosting = MobileHosting(lambda: service.store, tmp_path / "identity", capability="owner-capability",
                            catalog_factory=factory, catalog_root=service.root)
    app = FastAPI()
    app.include_router(create_mobile_hosting_router(hosting))
    path = "/api/mobile-hosting/catalog"
    assert request(app, "POST", path + "/prepare").status_code == 403
    assert request(app, "GET", path + "/status").status_code == 403
    headers = {"X-Radio-Mobile-Owner": "owner-capability"}
    assert request(app, "GET", path + "/status", headers).json()["state"] == "idle"
    assert calls == []
    assert request(app, "POST", path + "/prepare", headers).status_code == 202
    finish(hosting.catalog)
    assert request(app, "GET", path + "/status", headers).json()["state"] == "ready"
    assert not hosting.status()["enabled"]


def test_ingest_epoch_change_and_deadline_reject_publication(tmp_path):
    job, service = prepared(tmp_path)
    job.prepare()
    finish(job)
    prior = job.manifest()
    activity = IngestActivity()
    job.ingest_busy, job.ingest_epoch = activity.busy, activity.epoch
    original = service.source
    def during_export(**kwargs):
        @activity.track
        def mutate():
            assert activity.busy()
        mutate()
        assert not activity.busy()
        return original(**kwargs)
    service.source = during_export
    job.prepare()
    finish(job)
    assert job.status()["error"] == "ingest_active"
    assert job.manifest() == prior
    service.source = original
    job.deadline = -1
    job.prepare()
    finish(job)
    assert job.status()["error"] == "catalog_prepare_timeout"
    assert job.manifest() == prior


def test_shared_wire_fixtures_python_validation(tmp_path):
    from portable.catalog import CatalogError, read_catalog
    fixture = json.loads((Path(__file__).parent / "fixtures/mobile_catalog_golden.json").read_text(encoding="utf8"))
    accepted = {}
    for case in fixture["cases"]:
        root = tmp_path / case["name"]
        (root / "vectors").mkdir(parents=True)
        wire = case["manifest_utf8"].encode("utf8")
        assert canonical_json(json.loads(wire)) == wire
        (root / "manifest.json").write_bytes(wire)
        for digest, encoded in case["blobs"].items():
            (root / "vectors" / (digest + ".bin")).write_bytes(base64.b64decode(encoded))
        base = accepted.get(case["base_case"])
        if case["valid"]:
            accepted[case["name"]] = read_catalog(root, base=base)
        else:
            with pytest.raises(CatalogError):
                read_catalog(root, base=base)

def test_explicit_prepare_hashes_sources_and_get_never_reads_audio(tmp_path, monkeypatch):
    from db import database
    from portable_identity import ContentIdentityCache
    import config
    monkeypatch.setattr(config, 'MUSIC_DIR', str(tmp_path))
    audio = tmp_path / 'different name.mp3'
    audio.write_bytes(b'exact recording')
    job, service = prepared(tmp_path)
    with database.get_connection_cm() as conn:
        conn.execute('UPDATE songs SET filepath=? WHERE id=1', (str(audio),))
        conn.commit()
    assert service.source()[0][0].get('content_sha256') is None
    job.prepare()
    finish(job)
    assert job.manifest()['songs'][0]['content_sha256'] == sha256(b'exact recording')
    assert job.status()['hash_progress']['completed'] == 28
    monkeypatch.setattr(ContentIdentityCache, '_digest', lambda *args: pytest.fail('GET must never hash'))
    assert job.manifest()['songs'][0]['content_sha256'] == sha256(b'exact recording')
    audio.write_bytes(b'replacement')
    assert service.source()[0][0].get('content_sha256') is None


def test_cancel_hashing_preserves_prepared_revision_and_resumes(tmp_path, monkeypatch):
    from portable_identity import ContentIdentityCache
    job, service = prepared(tmp_path)
    job.prepare(); finish(job)
    prior = job.manifest()
    entered, release = threading.Event(), threading.Event()
    original = service.prepare_identities
    def blocked(cancelled, progress):
        entered.set(); release.wait(3)
        return original(cancelled, progress)
    monkeypatch.setattr(service, 'prepare_identities', blocked)
    job.prepare(); assert entered.wait(2)
    job.cancel(); release.set(); finish(job)
    assert job.status()['error'] == 'catalog_prepare_cancelled'
    assert job.manifest() == prior
    monkeypatch.setattr(service, 'prepare_identities', original)
    job.prepare(); finish(job)
    assert job.status()['state'] == 'ready'


def test_prepare_never_hashes_outside_approved_music_root(tmp_path, monkeypatch):
    import config
    from db import database
    audio = tmp_path / 'outside.mp3'
    audio.write_bytes(b'private')
    monkeypatch.setattr(config, 'MUSIC_DIR', str(tmp_path / 'approved'))
    job, service = prepared(tmp_path)
    with database.get_connection_cm() as conn:
        conn.execute('UPDATE songs SET filepath=? WHERE id=1', (str(audio),))
        conn.commit()
    monkeypatch.setattr(service.identities, 'measure', lambda *a, **k: pytest.fail('Outside root was read'))
    job.prepare(); finish(job)
    assert 'content_sha256' not in job.manifest()['songs'][0]
