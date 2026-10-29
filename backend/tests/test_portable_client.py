import asyncio

import httpx
import pytest
from fastapi import FastAPI

from api_security import ApiSecurity, SecuritySettings
from db import database
from portable_api import create_portable_router, native_authorizer
from portable_server import ServerCatalog
from portable_sync import PortableProfileStore
from tests.portable_client_simulator import ClientSimulator
from tests.test_portable_server import source


def test_public_protocol_roundtrip_offline_feedback_lost_ack_and_next_recommendations(tmp_path):
    async def run():
        catalog, _ = source(tmp_path / "server")
        store = catalog.store
        grant = store.issue_device("independent fixture phone", ["catalog:read", "profile:read", "profile:write"])
        app = FastAPI()
        app.include_router(create_portable_router(lambda: store, lambda: catalog))
        ApiSecurity(SecuritySettings(), native_authorizer=native_authorizer(lambda: store)).install(app)
        tracks = [{"local_id": "renamed-on-phone", "title": "Fixture 1", "playable": True},
                  {"local_id": "second", "title": "Fixture 2", "playable": True},
                  {"local_id": "phone-only", "title": "Additional song", "playable": True}]
        decisions = [{"local_id": "renamed-on-phone", "song_id": "1", "action": "confirm"},
                     {"local_id": "second", "song_id": "2", "action": "confirm"}]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                                     base_url="http://localhost", headers={"Authorization": "Bearer " + grant["token"]}) as http:
            phone = ClientSimulator(http, tmp_path / "phone", grant["device_id"], tracks, decisions=decisions)
            matches = await phone.bootstrap()
            assert matches["counts"]["confirmed"] == 2
            # No HTTP calls occur during these local recommendations/feedback.
            phone.engine.refresh_upcoming(now=1000)
            phone.record("started", "1", 1001)
            phone.record("completed", "1", 1180)
            phone.record("hearted", "1", 1181)
            phone.engine.enqueue("2")
            queue = list(phone.engine.state["queue"])
            assert phone.engine.replay() == "1"
            assert phone.engine.state["queue"] == queue
            assert phone.engine.search("Additional")[0]["song_id"] == "local:phone-only"
            with pytest.raises(ConnectionError):
                await phone.synchronize(lose_ack=True)
            await phone.synchronize()
            await phone.synchronize()
            assert not phone.outbox
            assert database.get_song(1)["play_count"] == 1
            assert database.get_song(1)["complete_count"] == 1
            assert phone.engine.state["current"] == "1" and phone.engine.state["queue"] == queue
            assert phone.engine.recommend(now=1200)
            tablet = store.issue_device("tablet", ["profile:write"])
            response = await http.post("/api/portable/v1/events", headers={"Authorization": "Bearer " + tablet["token"]},
                json={"protocol_version": 1, "events": [{"protocol_version": 1, "event_id": "tablet-local",
                    "device_id": tablet["device_id"], "logical_clock": 500, "occurred_at": 1201,
                    "kind": "favorite_changed", "song_id": None, "local_id": "phone-only", "value": True}]})
            assert response.status_code == 200
            await phone.synchronize()
            assert "local:phone-only" not in phone.engine.state["hearts"]
            # A long disconnected outbox exceeds one API batch. Losing the
            # second acknowledgement retains only that unacknowledged batch.
            for index in range(501):
                phone.record("replayed", "1", 1300 + index)
            with pytest.raises(ConnectionError):
                await phone.synchronize(lose_ack=True, lose_ack_batch=2)
            assert len(phone.outbox) == 1
            await phone.synchronize()
            assert not phone.outbox and database.get_song(1)["replay_count"] == 501
    asyncio.run(run())
