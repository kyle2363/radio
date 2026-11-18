"""Checkpoint 9A: feel events travel through the profile exchange; desktop energy migrates."""
import pytest

from db import database
from portable.feel import ENERGY_FEEL_ID
from portable_sync import PortableProfileStore

FID = "3b9e4a7c-1f2d-4c5b-8a69-0e1d2c3b4a59"


def ev(number, kind, device="phone", **changes):
    body = {"protocol_version": 1, "event_id": f"{device}-{number}", "device_id": device,
            "logical_clock": number, "kind": kind, "feel_id": FID, "occurred_at": 1000.0 + number}
    if "song_id" in changes or "other_song_id" in changes:
        body["catalog_id"] = PortableProfileStore().catalog_id
    body.update(changes)
    return body


def evidence():
    with database.get_connection_cm() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT feel_id,kind,song_key,other_key,value,tie,weight FROM portable_feel_evidence ORDER BY occurred_at")]


def define(number, name="Wind down", device="phone", **value):
    return ev(number, "feel_defined", device=device, value={"name": name, "color": "#0F766E", "scale": False,
                                              "archived": False, **value})


def test_feel_definitions_are_last_writer_wins_in_profile():
    store = PortableProfileStore()
    store.import_events("phone", [define(2, "Late"), define(1, "Early")])
    assert store.profile()["feels"][FID]["name"] == "Late"
    store.import_events("tablet", [define(3, "Archived", device="tablet", archived=True)])
    assert store.profile()["feels"][FID]["archived"] is True


def test_ratings_and_comparisons_are_append_only_evidence():
    store = PortableProfileStore()
    store.import_events("phone", [
        ev(1, "feel_rated", song_id="1", value=1.0),
        ev(2, "feel_rated", song_id="1", value=0.0),
        ev(3, "feel_compared", song_id="2", other_local_id="phone-x", value="win"),
        ev(4, "feel_compared", local_id="phone-y", other_song_id="1", value="tie"),
    ])
    rows = evidence()
    assert [(r["kind"], r["song_key"], r["other_key"], r["value"], r["tie"]) for r in rows] == [
        ("rated", "1", None, 1.0, 0), ("rated", "1", None, 0.0, 0),
        ("compared", "2", "local:phone-x", None, 0), ("compared", "local:phone-y", "1", None, 1)]
    # Re-sending the same batch is a duplicate, not new evidence.
    store.import_events("phone", [ev(1, "feel_rated", song_id="1", value=1.0)])
    assert len(evidence()) == 4


def test_feel_events_round_trip_to_other_devices():
    store = PortableProfileStore()
    store.import_events("phone", [ev(1, "feel_rated", song_id="1", value=0.875)])
    kinds = [e["event"]["kind"] for e in store.events()["events"]]
    assert "feel_rated" in kinds


@pytest.mark.parametrize("bad", [
    {"kind": "feel_rated", "song_id": "1", "value": 1.5},
    {"kind": "feel_rated", "song_id": "1", "value": True},
    {"kind": "feel_rated", "value": 0.5},
    {"kind": "feel_rated", "song_id": "1", "value": 0.5, "feel_id": "nope"},
    {"kind": "feel_rated", "song_id": "1", "value": 0.5, "weight": 0},
    {"kind": "feel_compared", "song_id": "1", "value": "win"},
    {"kind": "feel_compared", "song_id": "1", "other_song_id": "1", "value": "win"},
    {"kind": "feel_compared", "song_id": "1", "other_song_id": "2", "value": "lose"},
    {"kind": "feel_defined", "value": {"name": "", "color": "#000000", "scale": False, "archived": False}},
    {"kind": "feel_defined", "value": {"name": "x", "color": "red", "scale": False, "archived": False}},
    {"kind": "feel_defined", "value": {"name": "x", "color": "#000000", "scale": False}},
    {"kind": "feel_defined", "feel_id": ENERGY_FEEL_ID,
     "value": {"name": "Energy", "color": "#000000", "scale": True, "archived": True}},
    {"kind": "song_started", "song_id": "1", "attempt_id": "a", "feel_id": FID},
])
def test_invalid_feel_events_are_rejected(bad):
    body = ev(1, **bad)
    if "feel_id" not in bad and not bad["kind"].startswith("feel_"):
        body.pop("feel_id")
    with pytest.raises(ValueError):
        PortableProfileStore().import_events("phone", [body])


def test_legacy_user_energy_migrates_once_as_weighted_server_ratings():
    with database.get_connection_cm() as conn:
        conn.execute("UPDATE songs SET user_energy=0.8,user_energy_count=3,user_energy_updated_at=1500 WHERE id=1")
        conn.execute("UPDATE songs SET user_energy=NULL,user_energy_count=0 WHERE id!=1")
        conn.commit()
    store = PortableProfileStore()
    PortableProfileStore()  # a second start must not migrate again
    rated = [e["event"] for e in store.events()["events"] if e["event"]["kind"] == "feel_rated"]
    assert len(rated) == 1
    assert rated[0] | {"event_id": None} == {
        "protocol_version": 1, "device_id": "server", "event_id": None, "logical_clock": rated[0]["logical_clock"],
        "kind": "feel_rated", "feel_id": ENERGY_FEEL_ID, "song_id": "1", "catalog_id": store.catalog_id,
        "value": 0.8, "weight": 3.0, "occurred_at": 1500.0}
    assert evidence() == [{"feel_id": ENERGY_FEEL_ID, "kind": "rated", "song_key": "1", "other_key": None,
                           "value": 0.8, "tie": 0, "weight": 3.0}]
