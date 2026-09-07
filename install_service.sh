#!/bin/bash
# install_service.sh - Quad Parallel Race Service Installation

set -euo pipefail

BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
RACE_SERVICE_FILE="/etc/systemd/system/quad-race.service"
CHECKIN_SERVICE_FILE="/etc/systemd/system/quad-race-checkin.service"
SERVICE_USER="${SUDO_USER:-$(id -un)}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

echo "--- Quad Parallel Race Service Installation ---"
echo "Projektverzeichnis: $BASE_DIR"
echo "Dienstbenutzer: $SERVICE_USER:$SERVICE_GROUP"

if [ ! -f "$BASE_DIR/check_env.py" ]; then
    echo "FEHLER: check_env.py nicht gefunden in $BASE_DIR"
    exit 1
fi

echo "Prüfe Python-Version..."
python3 "$BASE_DIR/check_env.py"

if [ ! -d "$BASE_DIR/.venv" ]; then
    echo "Erstelle Virtual Environment..."
    python3 -m venv "$BASE_DIR/.venv"
fi

echo "Installiere/Aktualisiere Python-Abhängigkeiten..."
"$BASE_DIR/.venv/bin/python" -m pip install --upgrade pip
"$BASE_DIR/.venv/bin/python" -m pip install -r "$BASE_DIR/requirements.txt"

mkdir -p "$BASE_DIR/data"

echo "Erstelle systemd Service-Konfiguration für Rennbetrieb..."
sudo tee "$RACE_SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Quad Racing Software
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Erstelle systemd Service-Konfiguration für Check-in..."
sudo tee "$CHECKIN_SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Quad Racing Software Check-in
After=network-online.target quad-race.service
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/.venv/bin/python -m uvicorn backend.checkin_main:app --host 0.0.0.0 --port 8010
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Aktiviere und starte Dienste..."
sudo systemctl daemon-reload
sudo systemctl enable quad-race.service quad-race-checkin.service
sudo systemctl restart quad-race.service quad-race-checkin.service

echo "--- Installation erfolgreich abgeschlossen ---"
sudo systemctl --no-pager --full status quad-race.service quad-race-checkin.service
