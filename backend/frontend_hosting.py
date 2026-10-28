"""Serve the compiled client through the authenticated API's trusted origin."""
from pathlib import Path

from starlette.staticfiles import StaticFiles


def mount_frontend(app, directory):
    """Register last, so API routes retain precedence. A build is optional for dev."""
    dist = Path(directory).resolve()
    if not (dist / "index.html").is_file():
        return False
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="radio-client")
    return True
