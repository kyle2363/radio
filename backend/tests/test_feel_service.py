"""Checkpoints 9D-9E: desktop Feels panel, feel stations, suggestions, review and history."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import database
from feel_service import FeelService, starter_feels, starter_id
from feels_api import create_feels_router
from portable import feel as feel_rules
from portable_sync import PortableProfileStore

# 2-D toy embeddings: songs 1-6 point "up", 7-12 point "right".
VECTORS = {str(i): [1.0, 0.05 * i] if i <= 6 else [0.05 * i, 1.0] for i in range(1, 13)}


@pytest.fixture
def service():
    return FeelService(PortableProfileStore(), vectors=lambda: VECTORS)


def server_events(kind=None):
    events = [e["event"] for e in PortableProfileStore().events(limit=500)["events"]]
    return [e for e in events if kind is None or e["kind"] == kind]


def test_starter_ids_match_android_name_uuids():
    # java.util.UUID.nameUUIDFromBytes("radio-feel:hype")
    import uuid
    assert uuid.UUID(starter_id("hype")).version == 3 and feel_rules.valid_feel_id(starter_id("hype"))
    assert starter_id("energy") == feel_rules.ENERGY_FEEL_ID
    assert len({s["id"] for s in starter_feels()}) == len(starter_feels())


def test_create_rename_archive_are_server_feel_events(service):
    fid = service.define(None, name="Gym", color="#ff0000")
    assert server_events("feel_defined")[-1]["value"] == {"name": "Gym", "color": "#FF0000", "scale": False,
                                                          "archived": False}
    service.define(fid, name="Gym 2")
    service.define(fid, archived=True)
    feel = next(f for f in service.list_feels(include_archived=True) if f["id"] == fid)
    assert (feel["name"], feel["archived"]) == ("Gym 2", True)
    assert fid not in [f["id"] for f in service.list_feels()]
    with pytest.raises(ValueError):
        service.define(feel_rules.ENERGY_FEEL_ID, archived=True)


def test_energy_is_always_listed_and_starters_adopt_once(service):
    assert service.list_feels()[0]["id"] == feel_rules.ENERGY_FEEL_ID
    hype = starter_id("hype")
    service.adopt_starter("hype")
    service.adopt_starter("hype")
    assert len([e for e in server_events("feel_defined") if e["feel_id"] == hype]) == 1


def test_rating_and_ranking(service):
    fid = service.define(None, name="Gym", color="#FF0000")
    service.rate(fid, 1, 1.0)
    service.rate(fid, 2, 0.0)
    ranking = service.ranking(fid)
    assert [r["song_id"] for r in ranking["songs"]] == [1, 2]
    assert ranking["songs"][0]["member"] and not ranking["songs"][1]["member"]
    assert ranking["songs"][0]["title"] == "Fixture 1"
    assert 0 < ranking["settled"] < 100
    with pytest.raises(ValueError):
        service.rate(fid, 1, 1.5)
    with pytest.raises(ValueError):
        service.rate(fid, 999, 1.0)


def test_energy_rating_updates_user_energy(service):
    service.rate(feel_rules.ENERGY_FEEL_ID, 3, 0.875)
    song = database.get_song(3)
    assert song["user_energy"] == pytest.approx(service.ranking(feel_rules.ENERGY_FEEL_ID)["songs"][0]["score"], abs=1e-4)
    assert song["user_energy_count"] >= 1


def test_comparisons_and_uncertain_pairs(service):
    fid = service.define(None, name="Gym", color="#FF0000")
    service.compare(fid, 1, 2, "a")
    service.compare(fid, 2, 3, "tie")
    kinds = [(e["song_id"], e["other_song_id"], e["value"]) for e in server_events("feel_compared")]
    assert kinds == [("1", "2", "win"), ("2", "3", "tie")]
    service.compare(fid, 4, 5, "b")
    assert server_events("feel_compared")[-1]["song_id"] == "5"
    pairs = service.pairs(fid, 10)
    assert pairs and all(p["a"]["song_id"] != p["b"]["song_id"] for p in pairs)
    with pytest.raises(ValueError):
        service.compare(fid, 1, 1, "a")


def test_doesnt_fit_rates_out(service):
    fid = service.define(None, name="Gym", color="#FF0000")
    service.rate(fid, 1, 1.0)
    service.compare(fid, 1, 2, "doesnt_fit_b")
    assert server_events("feel_rated")[-1]["song_id"] == "2"
    assert server_events("feel_rated")[-1]["value"] == 0.0


def test_sprint_prefers_neighbours_and_skips_rated(service):
    fid = service.define(None, name="Up", color="#FF0000")
    service.rate(fid, 1, 1.0)
    service.rate(fid, 2, 1.0)
    deck = service.sprint(fid, 4, randomness=0.0)
    ids = [s["song_id"] for s in deck]
    assert 1 not in ids and 2 not in ids
    assert set(ids) <= {3, 4, 5, 6}


def test_station_ids_members_then_neighbours(service):
    fid = service.define(None, name="Up", color="#FF0000")
    service.rate(fid, 1, 1.0)
    service.rate(fid, 2, 1.0)
    service.rate(fid, 7, 0.0)
    ids = service.station_ids(fid)
    assert ids[:2] in ([1, 2], [2, 1])
    assert 7 not in ids
    assert {3, 4, 5, 6} <= set(ids)


def test_suggestions_for_song_page(service):
    fid = service.define(None, name="Up", color="#FF0000")
    service.rate(fid, 1, 1.0)
    service.rate(fid, 2, 1.0)
    assert [s["feel"]["id"] for s in service.suggestions(3)] == [fid]
    assert service.suggestions(9) == []
    service.rate(fid, 3, 1.0)
    assert service.suggestions(3) == []  # already rated: nothing to suggest


def test_review_lists_old_members_only(service):
    fid = service.define(None, name="Up", color="#FF0000")
    old = time.time() - 400 * feel_rules.DAY_SECONDS
    service.rate(fid, 1, 1.0, at=old)
    service.rate(fid, 2, 1.0)
    assert [r["song_id"] for r in service.review(fid)] == [1]
    service.rate(fid, 1, 1.0)  # Keep confirms it
    assert service.review(fid) == []


def test_history_on_song_page(service):
    fid = service.define(None, name="Up", color="#FF0000")
    service.rate(fid, 1, 1.0, at=1_700_000_000.0)
    history = service.history(1)
    assert history[0]["feel"]["id"] == fid
    assert history[0]["member"] and history[0]["since"] == 1_700_000_000.0


def test_feel_station_restricts_queue_rebuild(service, monkeypatch):
    fid = service.define(None, name="Up", color="#FF0000")
    service.rate(fid, 1, 1.0)
    service.set_station(fid)
    assert database.get_setting("feel_station") == fid
    assert service.active_station_ids() == service.station_ids(fid)
    service.set_station(None)
    assert service.active_station_ids() is None


def test_router_round_trip(service):
    app = FastAPI()
    rebuilds = []
    app.include_router(create_feels_router(lambda: service, on_station_change=lambda: rebuilds.append(1)))
    client = TestClient(app)
    created = client.post("/api/feels", json={"name": "Gym", "color": "#00FF00"}).json()
    fid = created["id"]
    assert client.post(f"/api/feels/{fid}/rate", json={"song_id": 1, "value": 1.0}).status_code == 200
    assert client.get(f"/api/feels/{fid}").json()["songs"][0]["song_id"] == 1
    assert client.post(f"/api/feels/{fid}/rate", json={"song_id": 1, "value": 3}).status_code == 400
    assert client.put("/api/feels/station", json={"feel_id": fid}).json()["feel_id"] == fid
    assert rebuilds == [1]
    assert client.get("/api/feels/station").json()["feel_id"] == fid
    assert client.put("/api/feels/settings", json={"suggestions": "after_session"}).status_code == 200
    assert client.get("/api/feels/settings").json()["suggestions"] == "after_session"
    assert client.put("/api/feels/settings", json={"suggestions": "loud"}).status_code == 400
    assert client.get("/api/songs/1/feels").json()["history"][0]["feel"]["id"] == fid


def test_numpy_neighbours_match_shared_rules(service):
    states = feel_rules.score_feel([{"song": "1", "value": 1.0, "at": 0}, {"song": "8", "value": 0.0, "at": 0}],
                                   [], time.time())
    expected = feel_rules.neighbour_scores(states, VECTORS)
    got = service._neighbours(states)
    assert set(got) == set(expected)
    for song, state in expected.items():
        assert got[song]["score"] == pytest.approx(state["score"])
        assert got[song]["confidence"] == pytest.approx(state["confidence"])


def test_after_session_suggestions(service):
    fid = service.define(None, name="Up", color="#FF0000")
    service.rate(fid, 1, 1.0)
    service.rate(fid, 2, 1.0)
    with database.get_connection_cm() as conn:
        conn.execute("INSERT INTO sessions(id) VALUES (77)")
        conn.executemany("INSERT INTO play_events(song_id, session_id, signal) VALUES (?, 77, 'playing')",
                         [(3,), (9,)])
        conn.commit()
    assert [(s["song_id"], s["feel"]["id"]) for s in service.session_suggestions()] == [(3, fid)]


def test_queue_restricts_to_station_and_fails_open():
    from types import SimpleNamespace
    from engine.queue import QueueManager
    allowed = QueueManager._feel_station_allowed
    assert allowed(SimpleNamespace(feel_station_ids=None)) is None
    assert allowed(SimpleNamespace(feel_station_ids=lambda: [3, 1])) == frozenset({1, 3})
    assert allowed(SimpleNamespace(feel_station_ids=lambda: [])) is None  # empty station never empties Radio
    def broken():
        raise RuntimeError("chroma down")
    assert allowed(SimpleNamespace(feel_station_ids=broken)) is None
