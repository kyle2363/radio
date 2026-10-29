"""Matching examples never mutate original music; temporary WAV fixtures only."""
import wave

from portable.matching import inspect_local_file, match_catalog


def status(songs, local, decisions=()):
    return match_catalog(songs, [local], decisions)["results"][0]


def test_content_hash_reads_bytes_and_ignores_name_and_location(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "renamed.wav"
    with wave.open(str(a), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000)
    b.write_bytes(a.read_bytes())
    first, second = inspect_local_file(a, "a"), inspect_local_file(b, "b")
    assert first["content_sha256"] == second["content_sha256"]
    assert first["duration"] == second["duration"] == 1
    result = status([{**first,"id":"server-1"}], second)
    assert result["status"] == "high_confidence" and result["song_id"] == "server-1"
    assert result["candidates"][0]["reasons"] == ["exact_content_sha256"]


def test_retagged_metadata_and_isrc_do_not_require_same_filename():
    song = {"id":"1","artist":"ARTIST","title":"Song!","album":"Record","duration":200,"isrc":"USABC1234567"}
    local = {"local_id":"one","artist":"Artist","title":"Song","album":"Record","duration":201,"filename":"foo.mp3"}
    assert status([song], local)["status"] == "high_confidence"
    isrc = {"local_id":"two","isrc":"USABC1234567","duration":200}
    assert status([song], isrc)["status"] == "high_confidence"


def test_versions_covers_duration_and_duplicates_require_review():
    song = {"id":"1","artist":"Artist","title":"Song","album":"Record","duration":200}
    live = {"local_id":"one","artist":"Artist","title":"Song (Live)","album":"Record","duration":200}
    assert status([song], live)["status"] in ("ambiguous", "unmatched")
    cover = {**live,"title":"Song","artist":"Another"}
    assert status([song], cover)["status"] == "ambiguous"
    wrong_duration = {**live,"title":"Song","duration":250}
    assert status([song], wrong_duration)["status"] == "ambiguous"
    same = {**song,"local_id":"same"}
    assert status([song,{**song,"id":"duplicate"}], same)["status"] == "ambiguous"


def test_missing_tags_filename_is_weak_and_missing_everything_unmatched():
    song = {"id":"1","artist":"Artist","title":"Song","duration":200}
    result = status([song], {"local_id":"file","filename":"Artist - Song.mp3"})
    assert result["status"] == "ambiguous" and result["song_id"] is None
    assert status([song], {"local_id":"empty"})["status"] == "unmatched"


def test_confirm_reject_stale_and_conflicting_decisions():
    song = {"id":"1","artist":"Artist","title":"Song","duration":200}
    local = {"local_id":"one","filename":"unknown.mp3","content_sha256":"a"*64}
    confirm = {"local_id":"one","song_id":"1","action":"confirm","content_sha256":"a"*64}
    assert status([song], local, [confirm])["status"] == "confirmed"
    assert status([song], {**local,"content_sha256":"b"*64}, [confirm])["status"] == "unmatched"
    reject = {**confirm,"action":"reject"}
    assert status([song], local, [confirm,reject])["status"] == "ambiguous"
    tagged = {**song,"local_id":"one"}
    assert status([song], tagged, [{"local_id":"one","song_id":"1","action":"reject"}])["status"] == "unmatched"
    removed = status([song], tagged, [{"local_id":"one","song_id":"old-id","action":"confirm"}])
    assert removed["status"] == "unmatched" and removed["warning"] == "confirmed_catalog_record_missing"


def test_fingerprint_algorithm_version_required_and_duration_checked():
    fingerprint = {"algorithm":"chromaprint","version":"1","value":"abc","sample_seconds":120}
    song = {"id":"1","duration":200,"fingerprint":fingerprint}
    local = {"local_id":"a","duration":200,"fingerprint":fingerprint}
    assert status([song], local)["status"] == "high_confidence"
    assert status([song], {**local,"duration":300})["status"] == "ambiguous"
    assert status([song], {**local,"fingerprint":{**fingerprint,"version":"2"}})["status"] == "unmatched"


def test_malformed_identifiers_are_not_automatic_identity_evidence():
    assert status([{"id":"1","recording_id":"1","duration":200}],
                  {"local_id":"a","recording_id":"1","duration":200})["status"] == "unmatched"
    assert status([{"id":"1","isrc":"bad-id","duration":200}],
                  {"local_id":"a","isrc":"bad-id","duration":200})["status"] == "unmatched"
    assert status([{"id":"1","recording_id":"musicbrainz:recording-id","duration":200}],
                  {"local_id":"a","recording_id":"musicbrainz:recording-id","duration":200})["status"] == "high_confidence"
