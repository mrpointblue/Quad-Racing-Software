#!/bin/bash
set -euo pipefail

BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$BASE_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "FEHLER: Docker ist nicht installiert."
    echo "Benötigt werden nur Docker Engine und das Docker-Compose-Plugin."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "FEHLER: Das Docker-Compose-Plugin ist nicht verfügbar."
    exit 1
fi

KIOSK_USER="${SUDO_USER:-$(id -un)}"
KIOSK_UID="$(id -u "$KIOSK_USER")"
KIOSK_GID="$(id -g "$KIOSK_USER")"
KIOSK_RUNTIME_DIR="/run/user/$KIOSK_UID"
WAYLAND_DISPLAY_NAME=""

for candidate in "$KIOSK_RUNTIME_DIR"/wayland-*; do
    if [ -S "$candidate" ]; then
        WAYLAND_DISPLAY_NAME="$(basename "$candidate")"
        break
    fi
done

if [ -z "$WAYLAND_DISPLAY_NAME" ]; then
    echo "FEHLER: Keine aktive Wayland-Sitzung für $KIOSK_USER gefunden."
    echo "Melde den Kiosk-Benutzer an der grafischen Raspberry-Pi-Oberfläche an und starte die Installation erneut."
    exit 1
fi

VIDEO_GID="$(getent group video | cut -d: -f3 || true)"
RENDER_GID="$(getent group render | cut -d: -f3 || true)"
VIDEO_GID="${VIDEO_GID:-$KIOSK_GID}"
RENDER_GID="${RENDER_GID:-$VIDEO_GID}"

mkdir -p data backend/static/uploads/signatures
if [ "$(id -u)" -eq 0 ]; then
    chown -R "$KIOSK_UID:$KIOSK_GID" data backend/static/uploads
fi

cat > .env.docker <<EOF
KIOSK_UID=$KIOSK_UID
KIOSK_GID=$KIOSK_GID
KIOSK_RUNTIME_DIR=$KIOSK_RUNTIME_DIR
WAYLAND_DISPLAY=$WAYLAND_DISPLAY_NAME
VIDEO_GID=$VIDEO_GID
RENDER_GID=$RENDER_GID
EOF

echo "Prüfe Docker-Konfiguration..."
docker compose --env-file .env.docker config --quiet

echo "Baue Quad Race inklusive Kiosk..."
docker compose --env-file .env.docker build

echo "Starte Rennsoftware, Check-in und HDMI-Kiosk..."
docker compose --env-file .env.docker up -d --remove-orphans

echo
echo "Quad Race wurde gestartet:"
echo "  Rennsoftware: http://127.0.0.1:8000"
echo "  Check-in:     http://127.0.0.1:8010/checkin"
echo "  Kiosk:        automatisch auf $WAYLAND_DISPLAY_NAME"
echo
docker compose --env-file .env.docker ps
