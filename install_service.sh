#!/bin/bash
# install_service.sh - Quad Parallel Race Service Installation

set -e

# Pfad-Konfiguration
BASE_DIR="/home/pi/Quad-Racing-Software"
SERVICE_FILE="/etc/systemd/system/quad-race.service"

echo "--- Quad Parallel Race Service Installation ---"

# 1. Python-Check (nutzt das vorhandene check_env.py)
if [ ! -f "$BASE_DIR/check_env.py" ]; then
    echo "FEHLER: check_env.py nicht gefunden in $BASE_DIR"
    exit 1
fi

echo "Prüfe Python-Version..."
if ! python3 "$BASE_DIR/check_env.py"; then
    echo "Installation abgebrochen: Python-Voraussetzungen nicht erfüllt."
    exit 1
fi

# 2. Virtual Environment und Requirements sicherstellen
if [ ! -d "$BASE_DIR/venv" ]; then
    echo "Erstelle Virtual Environment..."
    python3 -m venv "$BASE_DIR/venv"
fi

echo "Installiere/Aktualisiere Python-Abhängigkeiten..."
"$BASE_DIR/venv/bin/python" -m pip install --upgrade pip
"$BASE_DIR/venv/bin/python" -m pip install -r "$BASE_DIR/requirements.txt"

# 3. Datenverzeichnis anlegen
mkdir -p "$BASE_DIR/data"

# 4. systemd Service-Datei schreiben
echo "Erstelle systemd Service-Konfiguration..."
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Quad Racing Software
After=network.target

[Service]
User=pi
Group=pi
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 5. Dienst aktivieren und starten
echo "Aktiviere und starte Dienst..."
sudo systemctl daemon-reload
sudo systemctl enable quad-race.service
sudo systemctl restart quad-race.service

echo "--- Installation erfolgreich abgeschlossen ---"
systemctl status quad-race.service --no-pager