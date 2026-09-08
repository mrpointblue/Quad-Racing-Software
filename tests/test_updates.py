import fcntl
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.updates import safety_reason, updater_request
from scripts.update_agent import Agent, IMAGES, validate_manifest, matches_image


def image(n):
    return "sha256:" + str(n) * 64


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir()
        (self.root / "uploads").mkdir()
        self.agent = Agent(self.root)
        self.old = {s: {"id": image(1), "architecture": "arm64"} for s in IMAGES}
        self.new = {s: {"id": image(2), "ref": IMAGES[s] + "@" + image(3)} for s in IMAGES}

    def setup_install(self):
        self.agent.check = lambda: (self.old, self.new, True)
        self.agent.safety = lambda: None
        self.agent.run = lambda args, timeout=120: json.dumps([{"Id": image(2)}]) if args[1:3] == ["image", "inspect"] else ""
        self.agent.installed = lambda: self.new

    def test_no_service_is_explicit(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(updater_request("status")["enabled"])

    def test_active_race_and_unknown_logo_block(self):
        self.assertTrue(safety_reason(True, False, {}, 0))
        self.assertTrue(safety_reason(False, True, {}, 0))
        self.assertTrue(safety_reason(False, True, {"ok": True}, 6))
        self.assertTrue(safety_reason(False, True, {"ok": True, "markers": {"lane_b_running": True}}, 0))
        self.assertEqual(safety_reason(False, False, {}, 100), "")
        self.assertEqual(safety_reason(False, True, {"ok": True, "markers": {}}, 0), "")

    def test_platform_selection_ignores_attestations(self):
        manifest = {"manifests": [
            {"platform": {"os": "linux", "architecture": "amd64"}, "digest": image(1)},
            {"platform": {"os": "linux", "architecture": "arm64"}, "digest": image(2)},
            {"platform": {"os": "unknown", "architecture": "unknown"}, "digest": image(3)},
        ]}
        self.assertEqual(validate_manifest(manifest, "arm64"), image(2))
        with self.assertRaises(RuntimeError):
            validate_manifest(manifest, "arm")

    def test_ambiguous_and_malicious_digest_rejected(self):
        m = {"platform": {"os": "linux", "architecture": "arm64"}, "digest": "bad; command"}
        with self.assertRaises(RuntimeError):
            validate_manifest({"manifests": [m]}, "arm64")
        m["digest"] = image(1)
        with self.assertRaises(RuntimeError):
            validate_manifest({"manifests": [m, m]}, "arm64")

    def test_running_after_download_prevents_stop(self):
        self.setup_install()
        with patch.object(self.agent, "safety", side_effect=[None, RuntimeError("Lauf aktiv")]), patch.object(self.agent, "compose") as compose:
            with self.assertRaisesRegex(RuntimeError, "Lauf aktiv"):
                self.agent.install()
            self.assertFalse(any(call.args[0] == "stop" for call in compose.call_args_list))

    def test_download_failure_never_stops_containers(self):
        self.setup_install()
        with patch.object(self.agent, "run", side_effect=RuntimeError("offline")), patch.object(self.agent, "compose") as compose:
            with self.assertRaises(RuntimeError):
                self.agent.install()
            compose.assert_not_called()

    def test_success_holds_exclusive_lock_at_final_safety_check(self):
        self.setup_install()
        checks = []
        def safety():
            if checks:
                with (self.root / "data/update-maintenance.lock").open("a") as competing:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(competing, fcntl.LOCK_SH | fcntl.LOCK_NB)
            checks.append(1)
        with patch.object(self.agent, "safety", side_effect=safety), patch.object(self.agent, "compose") as compose:
            self.agent.install()
        self.assertEqual(self.agent.snapshot()["phase"], "success")
        self.assertEqual(len(checks), 2)
        calls = [c.args[0] for c in compose.call_args_list]
        self.assertEqual(calls, ["config", "stop", "up"])
        self.assertTrue((self.root / self.agent.snapshot()["backup"]).exists())

    def test_backup_failure_restarts_old_images_only(self):
        self.setup_install()
        with patch.object(self.agent, "backup", side_effect=RuntimeError("disk full")), patch.object(self.agent, "compose") as compose:
            with self.assertRaises(RuntimeError):
                self.agent.install()
            self.assertEqual(compose.call_args_list[-1].kwargs["override"].name, "previous.json")
        self.assertFalse(self.agent.recovery)

    def test_health_failure_does_not_downgrade_migrated_database(self):
        self.setup_install()
        def compose(*args, **kwargs):
            if args[0] == "up":
                raise RuntimeError("health failed")
        with patch.object(self.agent, "compose", side_effect=compose) as mock:
            self.agent.work_lock.acquire()
            self.agent.work("install")
            self.assertEqual(sum(c.args[0] == "up" for c in mock.call_args_list), 1)
        self.assertEqual(self.agent.snapshot()["phase"], "recovery")
        self.assertIn("error", self.agent.submit("install"))

    def test_interrupted_install_requires_manual_recovery(self):
        self.agent.set(phase="installing")
        restart = Agent(self.root)
        self.assertTrue(restart.recovery)
        self.assertIn("error", restart.submit("install"))

    def test_duplicate_and_unknown_requests_are_rejected(self):
        self.agent.work_lock.acquire()
        self.assertIn("error", self.agent.submit("install"))
        self.assertIn("error", self.agent.submit("arbitrary-command"))
        self.agent.work_lock.release()

    def test_real_database_and_uploads_backup(self):
        db = self.root / "data/quad_race.sqlite"
        with sqlite3.connect(db) as connection:
            connection.execute("create table race_runs (id integer)")
            connection.execute("insert into race_runs values (123)")
        (self.root / "uploads/logo.txt").write_text("club")
        self.agent.backup()
        with tarfile.open(self.root / self.agent.snapshot()["backup"]) as archive:
            self.assertEqual(archive.extractfile("uploads/logo.txt").read(), b"club")
            restored = self.root / "verify.sqlite"
            restored.write_bytes(archive.extractfile("data/quad_race.sqlite").read())
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute("select id from race_runs").fetchone()[0], 123)

    def mock_registry(self, mixed=False):
        self.agent.installed = lambda: self.old
        self.agent.compose = lambda *args: json.dumps({"services": {s: {"image": IMAGES[s] + ":dev"} for s in IMAGES}})
        def run(args, timeout=120):
            ref = args[4]
            if args[-1] == "{{json .Image}}":
                revision = "b" if mixed and "kiosk" in ref else "a"
                return json.dumps({"config": {"Labels": {"org.opencontainers.image.revision": revision, "io.quad-race.updater.protocol": "1"}}})
            if "@" in ref:
                return json.dumps({"config": {"digest": image(2)}})
            return json.dumps({"digest": image(4), "manifests": [{"platform": {"os": "linux", "architecture": "arm64"}, "digest": image(3)}]})
        self.agent.run = run

    def test_dev_digest_comparison_detects_new_build(self):
        self.mock_registry()
        _, candidate, available = self.agent.check()
        self.assertTrue(available)
        self.assertEqual(candidate["race"]["channel"], "dev")
        self.assertEqual(candidate["race"], candidate["checkin"])
        self.agent.installed = lambda: {s: dict(self.old[s], id=image(2)) for s in IMAGES}
        self.assertFalse(self.agent.check()[2])

    def test_mixed_publication_is_not_installable(self):
        self.mock_registry(mixed=True)
        with self.assertRaisesRegex(RuntimeError, "unterschiedliche"):
            self.agent.check()

    def test_all_docker_image_id_forms_detect_current_build(self):
        self.mock_registry()
        for digest in (image(2), image(3), image(4)):
            with self.subTest(digest=digest):
                self.agent.installed = lambda: {s: dict(self.old[s], id=digest) for s in IMAGES}
                self.assertFalse(self.agent.check()[2])

    def test_reported_pi_digests_are_the_same_image(self):
        candidate = {
            "index_digest": "sha256:912411aadfd0a0a92c3bc29e65126646d727d3767bfb721e11959db5214e898d",
            "manifest_digest": "sha256:a638913b0faf195a5497b155f49dcbd087092b232dcd6b964162db9627bf39a5",
            "id": "sha256:e7703236cf1bfe4871d6ab67f6061aa02fe03461988188cf92c27ef8ae7c85eb",
        }
        for digest in candidate.values():
            self.assertTrue(matches_image(digest, candidate))
        self.assertFalse(matches_image(image(9), candidate))
        # Another architecture's manifest from the same index is not acceptable.
        self.assertFalse(matches_image("sha256:c53eb7bef8b1d6ca3c7a13dac37d06324bcc285932f3b31ec7de79a0eb0ae133", candidate))
        self.assertFalse(matches_image("", candidate))

    def test_manifest_ids_pass_download_and_post_install_verification(self):
        self.setup_install()
        for c in self.new.values():
            c.update(manifest_digest=image(3), index_digest=image(4))
        self.agent.run = lambda args, timeout=120: json.dumps([{"Id": image(3)}]) if args[1:3] == ["image", "inspect"] else ""
        self.agent.installed = lambda: {s: {"id": image(3)} for s in IMAGES}
        with patch.object(self.agent, "compose"):
            self.agent.install()
        self.assertEqual(self.agent.snapshot()["phase"], "success")

    def test_changed_digest_with_same_revision_remains_an_update(self):
        self.mock_registry()
        self.agent.installed = lambda: {s: dict(self.old[s], revision="a") for s in IMAGES}
        self.assertTrue(self.agent.check()[2])

    def test_offline_check_clears_installability_without_stopping(self):
        self.agent.set(available=True)
        with patch.object(self.agent, "check", side_effect=RuntimeError("offline")), patch.object(self.agent, "compose") as compose:
            self.agent.work_lock.acquire()
            self.agent.work("check")
        self.assertFalse(self.agent.snapshot()["available"])
        compose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
