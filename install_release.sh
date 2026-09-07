#!/bin/bash
set -euo pipefail

BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$BASE_DIR"

COMPOSE_FILE="compose.release.yaml"
QUAD_RACE_TAG="${QUAD_RACE_TAG:-dev}"

if ! command -v docker >/dev/null 2>&1; then
    echo "FEHLER: Docker ist nicht installiert."
    echo "Benötigt werden Docker Engine und das Docker-Compose-Plugin."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "FEHLER: Das Docker-Compose-Plugin ist nicht verfügbar."
    exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "FEHLER: $COMPOSE_FILE wurde nicht gefunden."
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
    echo "Melde den Kiosk-Benutzer an der grafischen Oberfläche an und starte die Installation erneut."
    exit 1
fi

VIDEO_GID="$(getent group video | cut -d: -f3 || true)"
RENDER_GID="$(getent group render | cut -d: -f3 || true)"
VIDEO_GID="${VIDEO_GID:-$KIOSK_GID}"
RENDER_GID="${RENDER_GID:-$VIDEO_GID}"

mkdir -p data uploads/signatures
if [ "$(id -u)" -eq 0 ]; then
    chown -R "$KIOSK_UID:$KIOSK_GID" data uploads
fi

cat > .env.release <<EOF
QUAD_RACE_TAG=$QUAD_RACE_TAG
KIOSK_UID=$KIOSK_UID
KIOSK_GID=$KIOSK_GID
KIOSK_RUNTIME_DIR=$KIOSK_RUNTIME_DIR
WAYLAND_DISPLAY=$WAYLAND_DISPLAY_NAME
VIDEO_GID=$VIDEO_GID
RENDER_GID=$RENDER_GID
EOF

COMPOSE=(docker compose --env-file .env.release --file "$COMPOSE_FILE")

echo "Prüfe Release-Konfiguration..."
"${COMPOSE[@]}" config --quiet

echo "Lade Quad Race $QUAD_RACE_TAG..."
"${COMPOSE[@]}" pull

echo "Starte Rennsoftware, Check-in und HDMI-Kiosk..."
"${COMPOSE[@]}" up -d --remove-orphans

echo
echo "Quad Race $QUAD_RACE_TAG wurde gestartet:"
echo "  Rennsoftware: http://127.0.0.1:8000"
echo "  Check-in:     http://127.0.0.1:8010/checkin"
echo "  Kiosk:        automatisch auf $WAYLAND_DISPLAY_NAME"
echo
"${COMPOSE[@]}" ps
