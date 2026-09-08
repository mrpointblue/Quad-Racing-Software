#!/usr/bin/env python3
"""Pi host updater. Standard library only; runs as the existing Docker user.

No network listener, arbitrary command, URL, path or image supplied by the web UI.
The local compose file and Docker credentials remain host-only.
"""
import fcntl
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

IMAGES = {
    "race": "ghcr.io/mrpointblue/quad-racing-software",
    "checkin": "ghcr.io/mrpointblue/quad-racing-software",
    "kiosk": "ghcr.io/mrpointblue/quad-racing-kiosk",
}
CONTAINERS = {"race": "quad-race", "checkin": "quad-race-checkin", "kiosk": "quad-race-kiosk"}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
PROTOCOL = "1"


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def command(args, cwd, timeout=120):
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True)
        return result.stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError("Zeitüberschreitung bei " + " ".join(args[:3])) from None
    except subprocess.CalledProcessError:
        # Never forward registry credentials, env files or raw subprocess output to browsers.
        raise RuntimeError("Befehl fehlgeschlagen: " + " ".join(args[:3]) + ". Netzwerk/GHCR-Anmeldung und Docker prüfen.") from None


def matches_image(local_id, candidate):
    """Docker stores either the index, platform manifest or config as image ID.

    These digests are linked by the exact registry index inspected in check().
    A Git revision alone cannot distinguish two builds of the same source.
    """
    return bool(DIGEST.fullmatch(local_id or "")) and local_id in {
        candidate.get("index_digest"), candidate.get("manifest_digest"), candidate.get("id")
    }


def validate_manifest(manifest, architecture):
    if "manifests" in manifest:
        matches = [m for m in manifest["manifests"] if m.get("platform", {}).get("os") == "linux"
                   and m.get("platform", {}).get("architecture") == architecture]
        if len(matches) != 1 or not DIGEST.fullmatch(matches[0].get("digest", "")):
            raise RuntimeError("Kein eindeutiges Image für diese Pi/PC-Architektur.")
        return matches[0]["digest"]
    raise RuntimeError("Multi-Arch-Veröffentlichung fehlt oder ist unvollständig.")


class Agent:
    def __init__(self, root):
        self.root = root.resolve()
        self.control = self.root / ".update"
        self.control.mkdir(mode=0o700, exist_ok=True)
        self.state_lock = threading.Lock()
        self.work_lock = threading.Lock()
        self.state = {"enabled": True, "phase": "idle", "available": False,
                      "message": "Noch nicht geprüft.", "installed": {}, "candidate": {}}
        self.recovery = False
        self.last_check = float("-inf")
        try:
            previous = json.loads((self.control / "status.json").read_text())
            for key in ("installed", "candidate", "checked_at", "backup"):
                if key in previous:
                    self.state[key] = previous[key]
            if previous.get("phase") in {"stopping", "backup", "installing", "verifying", "recovery"}:
                self.recovery = True
                self.state.update(phase="recovery", message="Update wurde unterbrochen. Keine weitere Installation: Container und Sicherung auf dem Pi prüfen.")
        except (OSError, ValueError):
            pass

    def run(self, args, timeout=120):
        return command(args, self.root, timeout)

    def compose(self, *args, override=None, timeout=180):
        cmd = ["docker", "compose", "--env-file", ".env.release", "-f", "compose.release.yaml"]
        if override:
            cmd += ["-f", str(override)]
        return self.run(cmd + list(args), timeout)

    def set(self, **values):
        with self.state_lock:
            self.state.update(values)
            atomic_json(self.control / "status.json", self.state)

    def snapshot(self):
        with self.state_lock:
            return dict(self.state, busy=self.work_lock.locked())

    def safety(self):
        # Fail closed when the application is unavailable or from an old build.
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/system/update/safety", timeout=5) as response:
                state = json.load(response)
        except (OSError, ValueError):
            raise RuntimeError("Rennstatus nicht prüfbar. Keine Installation.") from None
        if state.get("protocol") != PROTOCOL or state.get("safe") is not True:
            raise RuntimeError(state.get("reason") or "Rennbetrieb nicht sicher angehalten.")

    def installed(self):
        result = {}
        for service, container in CONTAINERS.items():
            info = json.loads(self.run(["docker", "inspect", container]))[0]
            image = json.loads(self.run(["docker", "image", "inspect", info["Image"]]))[0]
            result[service] = {"id": info["Image"], "architecture": image["Architecture"],
                               "revision": (image.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision", "")}
        return result

    def check(self):
        self.set(phase="checking", message="Prüfe Registry …")
        installed = self.installed()
        self.set(installed=installed)
        config = json.loads(self.compose("config", "--format", "json"))
        candidate = {}
        for service in ("race", "kiosk"):
            ref = config["services"][service]["image"]
            if not re.fullmatch(re.escape(IMAGES[service]) + r":[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", ref):
                raise RuntimeError("Nur der konfigurierte offizielle Image-Kanal wird unterstützt.")
            index = json.loads(self.run(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"], 45))
            index_digest = index.get("digest", "")
            if not DIGEST.fullmatch(index_digest):
                raise RuntimeError("Ungültiger Multi-Arch-Index-Digest.")
            digest = validate_manifest(index, installed[service]["architecture"])
            pinned = IMAGES[service] + "@" + digest
            manifest = json.loads(self.run(["docker", "buildx", "imagetools", "inspect", pinned, "--raw"], 45))
            image_id = manifest.get("config", {}).get("digest", "")
            if not DIGEST.fullmatch(image_id):
                raise RuntimeError("Ungültiger Image-Digest.")
            image_config = json.loads(self.run(["docker", "buildx", "imagetools", "inspect", pinned, "--format", "{{json .Image}}"], 45))
            labels = image_config.get("config", {}).get("Labels") or {}
            candidate[service] = {"ref": pinned, "id": image_id, "channel": ref.rsplit(":", 1)[1],
                                  "index_digest": index_digest, "manifest_digest": digest,
                                  "revision": labels.get("org.opencontainers.image.revision", ""),
                                  "protocol": labels.get("io.quad-race.updater.protocol", "")}
        if not candidate["race"]["revision"] or candidate["race"]["revision"] != candidate["kiosk"]["revision"]:
            raise RuntimeError("Veröffentlichung noch nicht vollständig: Rennsoftware und Kiosk haben unterschiedliche Build-Stände.")
        if any(c["protocol"] != PROTOCOL for c in candidate.values()):
            raise RuntimeError("Dieser Build benötigt eine manuelle Aktualisierung des Installers/Update-Dienstes.")
        candidate["checkin"] = dict(candidate["race"])
        available = any(not matches_image(installed[s]["id"], candidate[s]) for s in IMAGES)
        self.set(phase="ready", available=available, installed=installed, candidate=candidate,
                 checked_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                 message="Update verfügbar." if available else "Software ist aktuell.")
        self.last_check = time.monotonic()
        return installed, candidate, available

    def override(self, name, images):
        path = self.control / name
        atomic_json(path, {"services": {s: {"image": images[s], "pull_policy": "never"} for s in IMAGES}})
        return path

    def backup(self):
        destination = self.root / "update-backups"
        destination.mkdir(mode=0o700, exist_ok=True)
        paths = [self.root / "data", self.root / "uploads"]
        required = sum(p.stat().st_size for directory in paths for p in directory.rglob("*") if p.is_file() and not p.is_symlink())
        if shutil.disk_usage(destination).free < required + 256 * 1024 * 1024:
            raise RuntimeError("Nicht genügend Speicherplatz für die Sicherung.")
        path = destination / ("before-update-" + str(time.time_ns()) + ".tar")
        temporary = path.with_suffix(".partial")
        with tarfile.open(temporary, "w", dereference=False) as archive:
            for directory in paths:
                archive.add(directory, arcname=directory.name)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)
        self.set(backup=str(path.relative_to(self.root)))

    def install(self):
        self.safety()
        installed, candidate, available = self.check()
        if not available:
            return
        self.set(phase="downloading", message="Images werden geladen. Rennbetrieb bleibt verfügbar.")
        for service in ("race", "kiosk"):
            self.run(["docker", "pull", candidate[service]["ref"]], 1800)
            actual = json.loads(self.run(["docker", "image", "inspect", candidate[service]["ref"]]))[0]
            if not matches_image(actual["Id"], candidate[service]):
                raise RuntimeError("Geladenes Image entspricht nicht dem geprüften Build.")
        override = self.override("candidate.json", {s: candidate[s]["ref"] for s in IMAGES})
        old_override = self.override("previous.json", {s: installed[s]["id"] for s in IMAGES})
        self.compose("config", "--quiet", override=override)
        lock_path = self.root / "data" / "update-maintenance.lock"
        with lock_path.open("a") as lock:
            # One lock shared with all app POSTs. Prevent a new start between safety check and stop.
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Rennsoftware wird gerade bedient. Installation später erneut versuchen.")
                    time.sleep(0.1)
            self.safety()  # Recheck after downloading and after acquiring the maintenance lock.
            stopped = False
            launched = False
            try:
                self.set(phase="stopping", message="Rennsoftware und Check-in werden für die Sicherung angehalten.")
                stopped = True
                self.compose("stop", "--timeout", "30")
                self.set(phase="backup", message="Sichere Renndaten, Logos und Unterschriften …")
                self.backup()
                self.set(phase="installing", message="Installiere geprüften Build …")
                launched = True
                self.compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180", override=override, timeout=240)
                self.set(phase="verifying", message="Prüfe gestartete Container …")
                actual = self.installed()
                if any(not matches_image(actual[s]["id"], candidate[s]) for s in IMAGES):
                    raise RuntimeError("Nicht alle Container verwenden den erwarteten Build.")
                self.set(phase="success", available=False, installed=actual, message="Update erfolgreich installiert. Seite neu laden.")
            except Exception:
                if stopped and not launched:
                    try:
                        self.compose("up", "-d", "--no-build", "--pull", "never", override=old_override)
                    except Exception:
                        self.recovery = True
                        raise
                elif launched:
                    # New code may have migrated the DB: never downgrade it automatically.
                    self.recovery = True
                raise

    def submit(self, action):
        if action == "status":
            return self.snapshot()
        if action not in {"check", "install"}:
            return {"error": "Unbekannte Aktion."}
        if self.recovery:
            return dict(self.snapshot(), error="Unterbrochenes Update zuerst auf dem Pi prüfen.")
        if not self.work_lock.acquire(blocking=False):
            return dict(self.snapshot(), error="Eine Update-Aktion läuft bereits.")
        self.set(message="Aktion angefordert …")
        threading.Thread(target=self.work, args=(action,), daemon=True).start()
        return self.snapshot()

    def work(self, action):
        try:
            self.install() if action == "install" else self.check()
        except Exception as error:
            self.set(phase="recovery" if self.recovery else "error", available=False,
                     message=str(error) if isinstance(error, RuntimeError) else "Update fehlgeschlagen. Dienstprotokoll auf dem Pi prüfen.")
            print(type(error).__name__ + ": " + str(error), file=sys.stderr, flush=True)
        finally:
            self.last_check = time.monotonic()
            self.work_lock.release()

    def periodic(self):
        # Monotonic clock: setting the offline Pi time does not break scheduling.
        while True:
            if not self.recovery and time.monotonic() - self.last_check >= 3600:
                self.submit("check")
            time.sleep(30)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            raw = self.rfile.readline(1025)
            request = json.loads(raw) if len(raw) <= 1024 else {}
            action = request.get("action")
            result = self.server.agent.submit(action)
            self.wfile.write((json.dumps(result, ensure_ascii=False) + "\n").encode())
        except (OSError, ValueError, AttributeError):
            return


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    root = Path(sys.argv[1]).resolve()
    agent = Agent(root)
    # Prevent a second daemon from unlinking the live socket / running another update.
    with (agent.control / "agent.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        address = agent.control / "agent.sock"
        address.unlink(missing_ok=True)
        with Server(str(address), Handler) as server:
            os.chmod(address, 0o660)
            server.agent = agent
            threading.Thread(target=agent.periodic, daemon=True).start()
            server.serve_forever()


if __name__ == "__main__":
    main()
