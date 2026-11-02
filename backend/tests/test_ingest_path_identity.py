from db import database
from ingest import pipeline, scanner, tags
import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('repair_ingest', Path(__file__).resolve().parents[2] / 'scripts' / 'repair_ingest_path_duplicates.py')
repair_ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair_ingest)


@pytest.fixture(autouse=True)
def readonly_test_connection(monkeypatch):
    # The isolation runner validates native SQLite paths, not URI strings.
    def connect(path):
        conn = sqlite3.connect(path)
        conn.execute('PRAGMA query_only=ON')
        return conn
    monkeypatch.setattr(repair_ingest, 'open_readonly', connect)


def test_windows_path_variants_reuse_processed_song():
    original = r'c:\Music\Songs\Track.mp3'
    database.add_song(original, scanner.compute_file_hash(original))
    song = database.get_song_by_filepath(original)
    database.update_song(song['id'], ingest_phase='clustered', play_count=7)
    for variant in [r'C:\Music\Songs\Track.mp3', 'C:/Music/Songs/Track.mp3']:
        found = database.get_song_by_filepath(variant)
        assert found is not None
        assert found['id'] == song['id']
        assert database.add_song(variant, scanner.compute_file_hash(variant)) is False
    assert database.get_song(song['id'])['play_count'] == 7
    assert database.get_song(song['id'])['ingest_phase'] == 'clustered'
    assert database.get_total_song_count() == 29


def test_scan_reports_only_new_additions_and_repeat_scan_adds_nothing(monkeypatch, tmp_path):
    files = [str(tmp_path / name) for name in ['existing.mp3', 'new.mp3']]
    for path in files:
        with open(path, 'wb') as stream:
            stream.write(b'fixture')
    database.add_song(files[0], scanner.compute_file_hash(files[0]))
    existing = database.get_song_by_filepath(files[0])
    database.update_song(existing['id'], ingest_phase='clustered')
    monkeypatch.setattr(scanner, 'find_audio_files', lambda _: files)
    monkeypatch.setattr(tags, 'extract_album_art', lambda *args: None)
    first = pipeline.run_phase_raw()
    assert first['added'] == 1
    assert first['existing'] == 1
    assert pipeline.run_phase_raw()['added'] == 0
    assert database.get_total_song_count() == 30
    assert database.get_song(existing['id'])['ingest_phase'] == 'clustered'


def legacy_duplicate():
    original = r'c:\Music\Track.mp3'
    database.add_song(original, scanner.compute_file_hash(original))
    keep = database.get_song_by_filepath(original)['id']
    database.update_song(keep, ingest_phase='clustered', play_count=7, ai_description='keep metadata')
    with database.get_connection_cm() as conn:
        cursor = conn.execute('INSERT INTO songs(filepath,file_hash) VALUES (?,?)',
                              (r'C:\Music\Track.mp3', 'legacy'))
        remove = cursor.lastrowid
        conn.commit()
    return keep, remove


def test_repair_preserves_original_and_creates_restorable_backup(tmp_path):
    keep, remove = legacy_duplicate()
    dry = repair_ingest.repair(database.DB_PATH, tmp_path)
    assert dry['duplicates'] == 1 and not dry['applied']
    assert database.get_song(remove)
    result = repair_ingest.repair(database.DB_PATH, tmp_path, apply=True)
    assert result['applied']
    assert database.get_song(remove) is None
    assert database.get_song(keep)['play_count'] == 7
    assert database.get_song(keep)['ai_description'] == 'keep metadata'
    with sqlite3.connect(result['backup']) as backup:
        assert backup.execute('SELECT COUNT(*) FROM songs WHERE id=?', (remove,)).fetchone()[0] == 1
    assert repair_ingest.repair(database.DB_PATH, tmp_path)['duplicates'] == 0


def test_repair_refuses_referenced_duplicate_without_deleting_it(tmp_path):
    _, remove = legacy_duplicate()
    with database.get_connection_cm() as conn:
        conn.execute('INSERT INTO play_events(song_id) VALUES (?)', (remove,))
        conn.commit()
    with pytest.raises(ValueError, match='Referenced duplicate'):
        repair_ingest.repair(database.DB_PATH, tmp_path, apply=True)
    assert database.get_song(remove)


def test_repair_refuses_duplicate_with_user_data(tmp_path):
    _, remove = legacy_duplicate()
    database.update_song(remove, user_notes='preserve this')
    with pytest.raises(ValueError, match='history or metadata'):
        repair_ingest.repair(database.DB_PATH, tmp_path, apply=True)
    assert database.get_song(remove)['user_notes'] == 'preserve this'
