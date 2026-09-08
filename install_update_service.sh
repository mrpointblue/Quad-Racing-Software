#!/bin/bash
set -euo pipefail
BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$BASE_DIR"
if [ "$(id -u)" -eq 0 ]; then
    echo "Bitte als normaler Docker-Benutzer ausführen, nicht mit sudo."
    exit 1
fi
command -v python3 >/dev/null || { echo "Python 3 vom Raspberry-Pi-OS fehlt."; exit 1; }
docker info >/dev/null
docker buildx version >/dev/null
test -f .env.release
test -f scripts/update_agent.py
mkdir -p .update data
chmod 700 .update
touch data/update-maintenance.lock
# Generate a unit with safe, validated literal paths (systemd is not a shell).
python3 scripts/install_update_unit.py "$BASE_DIR" "$(id -un)"
echo "Update-Dienst eingerichtet. Status: systemctl status quad-race-updater"
