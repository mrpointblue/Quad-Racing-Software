"""Narrow Unix-socket client; the web application never receives Docker access."""
import fcntl
import json
import os
import socket
from pathlib import Path


def updater_request(action):
    if action not in {"status", "check", "install"}:
        raise ValueError("Unknown update action")
    path = os.environ.get("QUAD_UPDATE_SOCKET")
    if not path:
        return {"enabled": False, "message": "Update-Dienst noch nicht eingerichtet."}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(path)
            client.sendall((json.dumps({"action": action}) + "\n").encode())
            with client.makefile("rb") as stream:
                return json.loads(stream.readline(65537))
    except (OSError, ValueError):
        return {"enabled": False, "message": "Update-Dienst nicht erreichbar. Auf dem Pi prüfen."}


def install_update_guard(app):
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def update_guard(request, call_next):
        path = os.environ.get("QUAD_UPDATE_LOCK")
        # Mutations and LOGO polling (which can save times) must drain before backup.
        guarded = request.method not in {"GET", "HEAD", "OPTIONS"} or request.url.path.endswith("/logo-status")
        if not path or not guarded:
            return await call_next(request)
        try:
            lock = Path(path).open("a")
        except OSError:
            return JSONResponse({"detail": "Update-Sperre nicht lesbar."}, status_code=503)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return JSONResponse({"detail": "Update wird installiert. Bitte warten."}, status_code=503)
            return await call_next(request)
        finally:
            lock.close()


def safety_reason(running, logo_enabled, logo, age):
    if running:
        return "Ein Lauf ist noch gestartet und nicht bestätigt."
    if logo_enabled:
        if not logo.get("ok") or age > 5:
            return "LOGO-Status nicht sicher verfügbar. Update gesperrt."
        markers = logo.get("markers", {})
        if any(markers.get(k) for k in ("lane_a_running", "lane_b_running")):
            return "Die LOGO meldet einen laufenden Lauf."
    return ""
