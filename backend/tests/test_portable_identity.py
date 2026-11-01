import hashlib
from pathlib import Path

from portable_identity import ContentIdentityCache


def test_cache_reuses_unchanged_bytes_and_invalidates_replacement(tmp_path, monkeypatch):
    audio = tmp_path / 'renamed.mp3'
    audio.write_bytes(b'original audio')
    cache = ContentIdentityCache(tmp_path / 'cache.sqlite3')
    assert cache.cached(audio) is None
    digest = cache.measure(audio)
    assert digest == hashlib.sha256(b'original audio').hexdigest()
    assert ContentIdentityCache(cache.path).cached(audio) == digest
    original_open = Path.open
    def no_read(path, *args, **kwargs):
        if path == audio:
            raise AssertionError('cached file was read')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_read)
    assert cache.measure(audio) == digest
    monkeypatch.setattr(Path, 'open', original_open)
    audio.write_bytes(b'replacement audio')
    assert cache.cached(audio) is None
    assert cache.measure(audio) == hashlib.sha256(b'replacement audio').hexdigest()


def test_changing_read_does_not_publish_or_replace_cache(tmp_path, monkeypatch):
    audio = tmp_path / 'song.mp3'
    audio.write_bytes(b'old')
    cache = ContentIdentityCache(tmp_path / 'cache.sqlite3')
    cache.measure(audio)
    audio.write_bytes(b'new content')
    original = cache._digest
    def changed(path, cancelled):
        result = original(path, cancelled)
        path.write_bytes(b'changed during read')
        return result
    monkeypatch.setattr(cache, '_digest', changed)
    assert cache.measure(audio) is None
    assert cache.cached(audio) is None


def test_cancelled_read_is_not_cached(tmp_path):
    audio = tmp_path / 'song.mp3'
    audio.write_bytes(b'content')
    cache = ContentIdentityCache(tmp_path / 'cache.sqlite3')
    assert cache.measure(audio, cancelled=lambda: True) is None
    assert cache.cached(audio) is None


def test_same_size_edit_with_restored_mtime_is_invalidated(tmp_path):
    import os
    audio = tmp_path / 'song.mp3'
    audio.write_bytes(b'old')
    cache = ContentIdentityCache(tmp_path / 'cache.sqlite3')
    cache.measure(audio)
    before = audio.stat()
    audio.write_bytes(b'new')
    os.utime(audio, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert cache.cached(audio) is None


def test_invalid_cached_digest_is_ignored(tmp_path):
    import sqlite3
    audio = tmp_path / 'song.mp3'
    audio.write_bytes(b'old')
    cache = ContentIdentityCache(tmp_path / 'cache.sqlite3')
    cache.measure(audio)
    with sqlite3.connect(cache.path) as conn:
        conn.execute("UPDATE identities_v2 SET digest='corrupt'")
    assert cache.cached(audio) is None
