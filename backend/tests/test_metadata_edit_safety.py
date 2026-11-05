import json
import sys
from pathlib import Path
from types import SimpleNamespace

from ingest import metadata, metadata_writer


def test_filename_never_treats_metadata_as_a_path():
    name = metadata_writer.build_filename('../AC/DC', r'..\Song')
    assert '/' not in name and '\\' not in name
    assert Path(name).name == name


def test_explicit_blank_tag_removes_existing_id3_value(monkeypatch):
    audio = SimpleNamespace(tags={'artist': ['old'], 'title': ['keep']}, save=lambda: None)
    monkeypatch.setitem(sys.modules, 'mutagen', SimpleNamespace(File=lambda *args, **kwargs: audio))
    result = metadata_writer.write_id3_tags('fixture.mp3', artist='')
    assert 'artist' not in audio.tags
    assert audio.tags['title'] == ['keep']
    assert result['artist'] == 'ok'


def test_rename_keeps_path_hashed_metadata_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata.config, 'ACESTEP_JSON_DIR', str(tmp_path / 'metadata'))
    old = tmp_path / 'music' / 'Artist - Original.mp3'
    old.parent.mkdir()
    old.write_bytes(b'fixture')
    source = Path(metadata.metadata_path(str(old)))
    source.parent.mkdir()
    source.write_text(json.dumps([{'caption': 'keep me'}]), encoding='utf-8')
    result = metadata_writer.rename_song_files(str(old), 'Artist', 'New')
    renamed = result['mp3']['new_path']
    expected = Path(metadata.metadata_path(renamed))
    assert Path(result['json']['new_path']) == expected
    assert metadata.find_matching_json(renamed, metadata.build_json_index(str(source.parent))) == str(expected)
    assert json.loads(expected.read_text())[0]['caption'] == 'keep me'
