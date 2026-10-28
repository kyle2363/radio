import asyncio
from pathlib import Path

from fastapi import FastAPI
import httpx

import config
from api_security import ApiSecurity, SecuritySettings


def test_public_frontend_cannot_expose_private_api_or_files():
    from frontend_hosting import mount_frontend
    root = Path(config.DB_PATH).parent
    dist = root / "frontend"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>Radio</html>")
    (dist / "assets" / "app.js").write_text("export const radio = true")
    (root / "secret.txt").write_text("private-file")
    app = FastAPI()
    @app.get("/api/private")
    def private():
        return {"secret": True}
    ApiSecurity(SecuritySettings(password="fixture", signing_secret="a" * 40,
                                allowed_hosts=("radio.test",), public_get_paths=("/", "/assets/"))).install(app)
    assert mount_frontend(app, dist)
    async def verify():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("192.168.1.10", 4567)), base_url="http://radio.test") as client:
            assert (await client.get("/")).text == "<html>Radio</html>"
            assert (await client.get("/assets/app.js")).status_code == 200
            assert (await client.get("/api/private")).status_code == 401
            status = await client.get("/api/auth/status")
            assert status.status_code == 200
            assert status.json() == {"authenticated": False, "configured": True}
            assert "private-file" not in (await client.get("/assets/%2e%2e/%2e%2e/secret.txt")).text
            assert (await client.post("/", headers={"Origin": "http://radio.test"})).status_code == 401
    asyncio.run(verify())


def test_missing_frontend_build_does_not_replace_backend_routes():
    from frontend_hosting import mount_frontend
    app = FastAPI()
    assert mount_frontend(app, Path(config.DB_PATH).parent / "missing-dist") is False
