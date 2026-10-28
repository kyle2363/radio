"""Registration order of the real app: API routes must precede the client mount.

Starlette resolves routes in registration order, so a catch-all ``Mount("/")``
registered before an API route silently shadows it. This shipped once: the built
client answered ``/api/auth/status`` with 404, so the UI never left the
connecting phase. Private routes stayed hidden behind the middleware's 401 gate,
which is why only the auth endpoints exposed the mistake.
"""
import pytest


def test_api_routes_precede_the_client_mount():
    main = pytest.importorskip("main")
    routes = [(getattr(route, "path", None), type(route).__name__) for route in main.app.routes]
    mounts = [index for index, (_, kind) in enumerate(routes) if kind == "Mount"]
    if not mounts:
        pytest.skip("no client build mounted")

    first_mount = min(mounts)
    shadowed = [path for path, _ in routes[first_mount:] if (path or "").startswith("/api/")]
    assert shadowed == []
    auth_index = next(index for index, (path, _) in enumerate(routes) if path == "/api/auth/status")
    assert auth_index < first_mount
