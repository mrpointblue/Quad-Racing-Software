"""HTTP integration tests; CI installs runtime dependencies before this suite."""
import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import sqlalchemy
    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False

from backend.services.updates import install_update_guard


@unittest.skipUnless(HTTP_AVAILABLE, "Runtime dependencies required for HTTP integration tests")
class GuardTests(unittest.TestCase):
    def test_shared_lock_blocks_mutations_but_keeps_health_available(self):
        app = FastAPI()
        install_update_guard(app)

        @app.post("/start")
        def start():
            return {"started": True}

        @app.get("/health")
        def health():
            return {"ok": True}

        @app.get("/run/1/logo-status")
        def poll():
            return {"ok": True}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            with patch.dict(os.environ, {"QUAD_UPDATE_LOCK": str(path)}), TestClient(app) as client:
                self.assertEqual(client.post("/start").status_code, 200)
                with path.open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertEqual(client.post("/start").status_code, 503)
                    self.assertEqual(client.get("/run/1/logo-status").status_code, 503)
                    self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.post("/start").status_code, 200)

    def test_update_routes_enforce_origin_and_running_guard(self):
        from backend import main
        client = TestClient(main.app)
        safe = {"protocol": "1", "safe": True, "reason": ""}
        headers = {"Origin": "http://testserver", "X-Quad-Update": "1"}
        with patch.object(main, "update_safety", return_value=safe), patch.object(main, "updater_request", return_value={"enabled": True}) as worker:
            self.assertEqual(client.post("/api/system/update/install").status_code, 403)
            self.assertEqual(client.post("/api/system/update/install", headers=dict(headers, Origin="https://evil.example")).status_code, 403)
            worker.assert_not_called()
            self.assertEqual(client.post("/api/system/update/install", headers=headers).status_code, 200)
            worker.assert_called_once_with("install")
        with patch.object(main, "update_safety", return_value=dict(safe, safe=False, reason="Lauf aktiv")), patch.object(main, "updater_request") as worker:
            self.assertEqual(client.post("/api/system/update/install", headers=headers).status_code, 409)
            worker.assert_not_called()

    def test_settings_template_and_build_information_render(self):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader("backend/templates"))
        env.parse(Path("backend/templates/settings.html").read_text())
        source = Path("backend/templates/settings.html").read_text()
        self.assertIn('id="software-update-install"', source)
        self.assertIn('src="/static/updates.js?v=', source)
