"""Destructive library/settings routes must fail closed and stay recoverable."""
import ast
import json
import logging
import math
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException, Query

import config as cfg
from db import database
from tests.test_queue_lifecycle import queue_module, manager  # noqa: F401 (fixtures)

MAIN_SOURCE = Path(__file__).resolve().parents[1] / "main.py"


def load(*names, **namespace):
    """Execute only the named top-level functions/constants from main.py."""
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in names for target in node.targets):
            nodes.append(node)
    env = dict(os=os, config=cfg, database=database, HTTPException=HTTPException, Query=Query,
               logger=logging.getLogger(__name__), datetime=datetime, json=json, re=re, math=math,
               **namespace)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(MAIN_SOURCE), "exec"), env)
    assert not set(names) - set(env), f"main.py is missing {sorted(set(names) - set(env))}"
    return env


def song_count():
    with database.get_connection_cm() as conn:
        return conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]


def no_chroma():
    raise RuntimeError("Chroma is not available in this test")


@pytest.fixture
def music(tmp_path, monkeypatch):
    folder = tmp_path / "audio"
    folder.mkdir()
    monkeypatch.setattr(cfg, "MUSIC_DIR", str(folder))
    return folder


# ── Music directory containment ──────────────────────────────

def test_music_dir_check_rejects_sibling_folder_with_same_prefix(music):
    sibling = music.parent / "audio_old"
    sibling.mkdir()
    inside, outside = music / "a.mp3", sibling / "a.mp3"
    inside.write_bytes(b"fixture")
    outside.write_bytes(b"fixture")
    check = load("_validate_filepath_in_music_dir")["_validate_filepath_in_music_dir"]

    assert check(str(inside)) == os.path.realpath(inside)
    for escaped in (str(outside), str(music / ".." / "audio_old" / "a.mp3"), str(music.parent)):
        with pytest.raises(HTTPException) as denied:
            check(escaped)
        assert denied.value.status_code == 403


# ── Delete all orphaned ──────────────────────────────────────

def delete_orphans():
    return load("_music_dir_has_audio", "library_delete_all_orphaned",
                get_chroma_client=no_chroma)["library_delete_all_orphaned"]


def test_delete_all_orphaned_refuses_when_music_folder_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "MUSIC_DIR", str(tmp_path / "unmounted"))
    with pytest.raises(HTTPException) as refused:
        delete_orphans()(expected_count=None)
    assert refused.value.status_code == 409
    assert song_count() == 28


def test_delete_all_orphaned_refuses_when_music_folder_has_no_audio(music):
    (music / "notes.txt").write_text("not music", encoding="utf-8")
    with pytest.raises(HTTPException) as refused:
        delete_orphans()(expected_count=None)
    assert refused.value.status_code == 409
    assert song_count() == 28


def test_delete_all_orphaned_refuses_a_stale_count_then_deletes_the_confirmed_one(music):
    (music / "Someone - Else.mp3").write_bytes(b"fixture")
    route = delete_orphans()
    with pytest.raises(HTTPException) as stale:
        route(expected_count=3)
    assert stale.value.status_code == 409
    assert song_count() == 28

    assert route(expected_count=28)["deleted"] == 28
    assert song_count() == 0


# ── Auto-match ───────────────────────────────────────────────

def test_auto_match_only_proposes_and_never_guesses_between_same_named_files(music):
    for folder in ("new", "a", "b"):
        (music / folder).mkdir()
    target = music / "new" / "Artist - Song.mp3"
    target.write_bytes(b"fixture")
    (music / "a" / "Dup - Track.mp3").write_bytes(b"fixture")
    (music / "b" / "Dup - Track.mp3").write_bytes(b"fixture")
    moved_from = str(music / "old" / "Artist - Song.mp3")
    ambiguous_from = str(music / "gone" / "Dup - Track.mp3")
    database.update_song(1, filepath=moved_from, title=None)
    database.update_song(2, filepath=ambiguous_from, title=None)

    result = load("library_auto_match")["library_auto_match"]()

    assert [(m["orphan_id"], m["new_filepath"]) for m in result["matched"]] == [(1, str(target))]
    assert 2 in {orphan["id"] for orphan in result["unmatched_orphans"]}
    # A proposal is not a write: the user confirms each match separately.
    assert database.get_song(1)["filepath"] == moved_from
    assert database.get_song(2)["filepath"] == ambiguous_from


# ── Settings validation ──────────────────────────────────────

SETTING_NAMES = ("_SETTING_KEY_RE", "_SETTING_MAX_LENGTH", "_SETTING_RANGES", "_validated_setting")


def test_update_setting_validates_key_value_and_known_ranges():
    update = load(*SETTING_NAMES, "update_setting", _session_database=lambda: database,
                  _get_queue_manager=None)["update_setting"]

    assert update("theme", {"value": "light"})["value"] == "light"
    assert database.get_setting("theme") == "light"
    update("show_lyrics", {"value": False})
    assert database.get_setting("show_lyrics") == "false"
    update("jitter_strength", {"value": 0.5})
    assert database.get_setting("jitter_strength") == "0.5"

    for key, value in (("Bad Key", "x"), ("../etc", "x"), ("theme", {"nested": 1}), ("theme", ["a"]),
                       ("theme", "x" * 30000), ("jitter_strength", "7"), ("jitter_strength", "loud"),
                       ("jitter_strength", "nan")):
        with pytest.raises(HTTPException) as invalid:
            update(key, {"value": value})
        assert invalid.value.status_code == 422, (key, value)
    assert database.get_setting("theme") == "light"
    assert database.get_setting("jitter_strength") == "0.5"


def test_import_settings_skips_invalid_entries_instead_of_writing_them():
    import_settings = load(*SETTING_NAMES, "import_settings")["import_settings"]
    result = import_settings({"data": {
        "settings": {"theme": "light", "Bad Key": "x", "nested": {"a": 1}, "exploration_level": "9"},
        "collections": [{"name": "Mix", "song_ids": [1, "two", 999999]}, "not a collection"],
        "song_metadata": {"1": {"user_notes": "kept"}, "x": {"user_notes": "bad id"}, "2": "not a dict"},
    }})

    assert result["settings_count"] == 1
    assert sorted(result["skipped_settings"]) == ["Bad Key", "exploration_level", "nested"]
    assert database.get_setting("theme") == "light"
    assert database.get_setting("exploration_level") == "0.3"
    assert database.get_setting("Bad Key") is None
    assert result["collections_count"] == 1
    mix = database.get_collection_by_name("Mix")
    assert [song["id"] for song in database.get_collection_songs(mix["id"])] == [1]
    assert result["song_metadata_count"] == 1
    assert database.get_song(1)["user_notes"] == "kept"


# ── Wipe database ────────────────────────────────────────────

@pytest.fixture
def stores(tmp_path, monkeypatch):
    data = tmp_path / "data"
    paths = {"CHROMA_PATH": data / "chroma", "ART_DIR": data / "art", "TTS_CACHE_DIR": data / "tts"}
    monkeypatch.setattr(cfg, "DATA_DIR", str(data))
    for name, path in paths.items():
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"fixture")
        monkeypatch.setattr(cfg, name, str(path))
    return data


def test_wipe_moves_database_and_vectors_into_a_backup(stores):
    result = load("wipe_database")["wipe_database"]()

    assert result["status"] == "wiped"
    backup = Path(result["backup"])
    assert backup.parent == stores
    assert not os.path.exists(cfg.DB_PATH) and not os.path.exists(cfg.CHROMA_PATH)
    assert (backup / "radio.db").is_file()
    assert (backup / "chroma" / "payload.bin").is_file()
    assert not os.path.exists(cfg.ART_DIR) and not os.path.exists(cfg.TTS_CACHE_DIR)


def test_wipe_that_cannot_move_the_database_restores_everything(stores, monkeypatch):
    real_rename = os.rename

    def rename(source, destination):
        if os.path.normcase(source) == os.path.normcase(cfg.DB_PATH):
            raise PermissionError("database is in use")
        return real_rename(source, destination)

    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(HTTPException) as failed:
        load("wipe_database")["wipe_database"]()

    assert failed.value.status_code == 409
    assert song_count() == 28
    assert (Path(cfg.CHROMA_PATH) / "payload.bin").is_file()
    assert (Path(cfg.ART_DIR) / "payload.bin").is_file()
    assert [entry.name for entry in stores.iterdir() if entry.name.startswith("wiped-")] == []


# ── Startup filter reset ─────────────────────────────────────

def test_startup_clears_any_persisted_session_only_filter():
    database.set_setting("genre_filter", "rock")
    database.set_setting("artist_filter", "someone")
    load("_clear_stale_session_filters")["_clear_stale_session_filters"]()
    assert database.get_setting("genre_filter") == ""
    assert database.get_setting("artist_filter") == ""


# ── Recently recommended buffer ──────────────────────────────

def test_recent_recommendations_evict_the_oldest_and_refresh_repeats(manager):
    manager._max_recent_history = 3
    manager._recently_recommended_ids = set()
    for song_id in (10, 3, 7, 1):
        manager._track_recommended({"song_id": song_id})
    assert manager._recently_recommended_ids == {3, 7, 1}

    manager._track_recommended({"song_id": 3})   # heard again: now the newest
    manager._track_recommended({"song_id": 8})
    assert manager._recently_recommended_ids == {1, 3, 8}

    # Callers reset the buffer by assigning a new set; ids placed there directly count as oldest.
    manager._recently_recommended_ids = {50, 51, 52}
    manager._track_recommended({"song_id": 4})
    manager._track_recommended({"song_id": 5})
    assert {4, 5} <= manager._recently_recommended_ids and len(manager._recently_recommended_ids) == 3
