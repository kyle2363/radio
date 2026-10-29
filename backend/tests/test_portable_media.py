import hashlib

from portable_media import make_media_plan, resolve_media_plan


def test_offline_skips_missing_partial_expired_or_wrong_cached_speech():
    data = b"complete synthetic speech"
    speech = {"resource_id": "speech-1", "sha256": hashlib.sha256(data).hexdigest(),
              "byte_length": len(data), "expires_at": 200, "context_id": "queue-context"}
    plan = make_media_plan("queue-context", "device-file-1", speech=speech)
    for cached in ({}, {"speech-1": b"partial"}, {"speech-1": b"x" * len(data)}):
        assert resolve_media_plan(plan, now=100, online=False, cached_bytes=cached) == [plan["segments"][-1]]
    assert len(resolve_media_plan(plan, now=100, online=False, cached_bytes={"speech-1": data})) == 2
    assert len(resolve_media_plan(plan, now=201, online=True, cached_bytes={"speech-1": data})) == 1


def test_connected_remote_speech_is_optional_and_music_only_always_works():
    plan = make_media_plan("context", "phone-file")
    assert resolve_media_plan(plan, now=0, online=False)[0]["local_id"] == "phone-file"
    assert plan["segments"] == [{"type": "local_song", "local_id": "phone-file"}]
