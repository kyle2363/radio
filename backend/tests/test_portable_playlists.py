"""Checkpoint 7A: playlists sync as profile events and project onto collections."""
import json
from pathlib import Path

import pytest

from db import database
from portable.playlists import key_between, valid_key
from portable_sync import PortableProfileStore

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "playlist_golden.json").read_text())
PID = "6f1c9a52-0d7e-4a5b-9a43-1f2d3c4b5a60"


def ev(number, kind, device="phone", **changes):
    body = {"protocol_version": 1, "event_id": f"{device}-{number}", "device_id": device,
            "logical_clock": number, "kind": kind, "playlist_id": PID, "occurred_at": 1000.0}
    if "song_id" in changes:
        body["catalog_id"] = PortableProfileStore().catalog_id
    body.update(changes)
    return body


def songs_of(collection_id):
    return [row["id"] for row in database.get_collection_songs(collection_id)]


def linked():
    with database.get_connection_cm() as conn:
        row = conn.execute("SELECT collection_id FROM portable_playlists WHERE playlist_id=?", (PID,)).fetchone()
        return row[0] if row else None


@pytest.mark.parametrize("case", GOLDEN["key_between"])
def test_key_between_matches_golden(case):
    key = key_between(case["a"], case["b"])
    assert key == case["key"]
    assert valid_key(key)
    assert (case["a"] or "") < key and (case["b"] is None or key < case["b"])


def test_repeated_inserts_stay_ordered_and_valid():
    keys = [key_between(None, None)]
    for _ in range(40):
        keys.insert(1, key_between(keys[0], keys[1] if len(keys) > 1 else None))
    assert keys[1:] == sorted(keys[1:]) and all(valid_key(k) for k in keys)


def test_phone_playlist_creates_ordered_desktop_collection_in_any_arrival_order():
    store = PortableProfileStore()
    store.import_events("phone", [
        ev(2, "playlist_member_changed", song_id="2", value=True, position="m"),
        ev(3, "playlist_member_changed", song_id="1", value=True, position="g"),
        ev(4, "playlist_member_changed", local_id="phone-only", value=True, position="t"),
    ])
    assert linked() is None
    store.import_events("phone", [ev(1, "playlist_renamed", value="Road trip")])
    cid = linked()
    assert [c["name"] for c in database.get_collections() if c["id"] == cid] == ["Road trip"]
    assert songs_of(cid) == [1, 2]
    playlists = store.profile()["playlists"]
    assert playlists[PID]["name"] == "Road trip"
    assert [m["key"] for m in playlists[PID]["members"]] == ["1", "2", "local:phone-only"]


def test_last_writer_wins_for_membership_order_and_name():
    store = PortableProfileStore()
    store.import_events("phone", [ev(1, "playlist_renamed", value="A"),
                                  ev(2, "playlist_member_changed", song_id="1", value=True, position="g"),
                                  ev(3, "playlist_member_changed", song_id="2", value=True, position="m")])
    store.import_events("tablet", [ev(2, "playlist_member_changed", device="tablet", song_id="1", value=False, position="g"),
                                   ev(5, "playlist_member_changed", device="tablet", song_id="2", value=True, position="c"),
                                   ev(1, "playlist_renamed", device="tablet", value="older")])
    cid = linked()
    # tablet clock 2 ties phone clock 2: device id "tablet" > "phone" wins the remove.
    assert songs_of(cid) == [2]
    assert database.get_collections()[0]["name"] == "older" or \
        [c["name"] for c in database.get_collections() if c["id"] == cid] == ["older"]


def test_remote_delete_unlinks_but_keeps_desktop_collection():
    store = PortableProfileStore()
    store.import_events("phone", [ev(1, "playlist_renamed", value="Keep"),
                                  ev(2, "playlist_member_changed", song_id="1", value=True, position="g")])
    cid = linked()
    store.import_events("phone", [ev(3, "playlist_deleted", value=True)])
    assert linked() is None
    assert songs_of(cid) == [1]
    assert store.profile()["playlists"][PID]["deleted"] is True


def test_shared_desktop_collection_exports_name_members_and_reorders():
    store = PortableProfileStore()
    cid = database.create_collection("Desk")
    for sid in (1, 2, 3):
        database.add_to_collection(cid, sid)
    pid = store.share_collection(cid)
    store.capture_desktop()
    playlist = store.profile()["playlists"][pid]
    assert playlist["name"] == "Desk" and [m["key"] for m in playlist["members"]] == ["1", "2", "3"]
    before = store.events()["cursor"]
    store.capture_desktop()
    assert store.events(before)["events"] == []
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE collection_songs SET position=-1 WHERE collection_id=? AND song_id=3", (cid,))
        conn.execute("DELETE FROM collection_songs WHERE collection_id=? AND song_id=1", (cid,))
        conn.execute("UPDATE collections SET name='Desk 2' WHERE id=?", (cid,))
        conn.commit()
    store.capture_desktop()
    playlist = store.profile()["playlists"][pid]
    assert playlist["name"] == "Desk 2" and [m["key"] for m in playlist["members"]] == ["3", "2"]
    database.delete_collection(cid)
    store.capture_desktop()
    assert store.profile()["playlists"][pid]["deleted"] is True


def test_imported_changes_are_not_echoed_back_as_desktop_events():
    store = PortableProfileStore()
    store.import_events("phone", [ev(1, "playlist_renamed", value="Echo"),
                                  ev(2, "playlist_member_changed", song_id="1", value=True, position="g")])
    before = store.events()["cursor"]
    store.capture_desktop()
    assert [e for e in store.events(before)["events"] if e["event"]["kind"].startswith("playlist")] == []


@pytest.mark.parametrize("bad", [
    {"kind": "playlist_renamed", "value": ""},
    {"kind": "playlist_renamed", "value": "x" * 81},
    {"kind": "playlist_deleted", "value": "yes"},
    {"kind": "playlist_member_changed", "song_id": "1", "value": True, "position": "A"},
    {"kind": "playlist_member_changed", "song_id": "1", "value": True, "position": "g0"},
    {"kind": "playlist_member_changed", "song_id": "1", "value": True},
    {"kind": "playlist_renamed", "value": "ok", "playlist_id": "not-a-uuid"},
])
def test_invalid_playlist_events_are_rejected(bad):
    with pytest.raises(ValueError):
        PortableProfileStore().import_events("phone", [ev(1, **bad)])


def test_unshared_collection_stops_following_and_reshare_keeps_identity():
    store = PortableProfileStore()
    cid = database.create_collection("Desk")
    database.add_to_collection(cid, 1)
    pid = store.share_collection(cid)
    store.capture_desktop()
    assert store.shared_collection_ids() == {cid}
    store.unshare_collection(cid)
    assert store.shared_collection_ids() == set()
    # Phone edits no longer touch the desktop collection, and desktop edits are not exported.
    store.import_events("phone", [{**ev(50, "playlist_member_changed", song_id="2", value=True, position="z"),
                                   "playlist_id": pid}])
    assert songs_of(cid) == [1]
    database.add_to_collection(cid, 3)
    before = store.events()["cursor"]
    store.capture_desktop()
    assert store.events(before)["events"] == []
    # Sharing again reuses the same playlist id; the desktop's copy wins.
    assert store.share_collection(cid) == pid
    store.capture_desktop()
    assert [m["key"] for m in store.profile()["playlists"][pid]["members"]] == ["1", "3"]


def test_reshare_reconciles_a_phone_rename_made_while_unshared():
    store = PortableProfileStore()
    cid = database.create_collection("Desk")
    database.add_to_collection(cid, 1)
    pid = store.share_collection(cid)
    store.capture_desktop()
    store.unshare_collection(cid)
    store.import_events("phone", [{**ev(50, "playlist_renamed", value="Phone name"), "playlist_id": pid}])
    assert [c["name"] for c in database.get_collections() if c["id"] == cid] == ["Desk"]
    assert store.share_collection(cid) == pid
    store.capture_desktop()
    # The desktop's copy wins on re-share, so the profile matches the desktop again.
    assert store.profile()["playlists"][pid]["name"] == "Desk"
    assert [m["key"] for m in store.profile()["playlists"][pid]["members"]] == ["1"]


def test_unshare_unknown_collection_is_a_no_op_and_ids_need_no_schema():
    assert PortableProfileStore(initialize=False).shared_collection_ids() == set()
    PortableProfileStore().unshare_collection(12345)


def test_collection_sync_routes_toggle_and_list_state():
    from types import SimpleNamespace
    from fastapi import HTTPException
    from tests.test_library_safety import load
    routes = load("list_collections", "set_collection_sync", PortableProfileStore=PortableProfileStore,
                  CollectionSyncRequest=SimpleNamespace)
    cid = database.create_collection("Desk")
    assert [c["synced"] for c in routes["list_collections"]()["collections"]] == [False]
    on = routes["set_collection_sync"](cid, SimpleNamespace(enabled=True))
    assert on["synced"] is True and routes["list_collections"]()["collections"][0]["synced"] is True
    assert routes["set_collection_sync"](cid, SimpleNamespace(enabled=False)) == {"synced": False}
    assert routes["list_collections"]()["collections"][0]["synced"] is False
    with pytest.raises(HTTPException):
        routes["set_collection_sync"](999, SimpleNamespace(enabled=True))
