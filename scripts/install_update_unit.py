"""Install one fixed systemd unit; all Docker actions still run as the Pi user."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    root = str(Path(sys.argv[1]).resolve())
    user = sys.argv[2]
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", root) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise SystemExit("Projektpfad/Benutzer enthält für die Service-Installation nicht unterstützte Zeichen.")
    python = shutil.which("python3")
    if python is None or not re.fullmatch(r"/[A-Za-z0-9_./-]+", python):
        raise SystemExit("Python 3 nicht gefunden.")
    unit = f"""[Unit]
Description=Quad Race image update service
After=docker.service network-online.target
Wants=docker.service network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={root}
ExecStart={python} {root}/scripts/update_agent.py {root}
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""
    # Only this unit is installed with sudo. Do not copy or expose Docker credentials.
    with tempfile.TemporaryDirectory(prefix="quad-updater-") as temporary:
        path = Path(temporary) / "quad-race-updater.service"
        path.write_text(unit)
        subprocess.run(["sudo", "install", "-m", "644", str(path), "/etc/systemd/system/quad-race-updater.service"], check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "quad-race-updater.service"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "quad-race-updater.service"], check=True)


if __name__ == "__main__":
    main()
