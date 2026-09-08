# Quad Racing Software

Webbasierte Software zur Verwaltung und Durchführung von ADAC Quad Parallel Race Veranstaltungen.

**Aktueller Entwicklungsstand:** `1.7.4-dev`

Dieser Stand enthält Änderungen nach dem stabilen Release `1.7.3`. Vor einem produktiven Renneinsatz müssen die automatisierten Tests erfolgreich durchlaufen und die Funktionen mit der realen Siemens LOGO! geprüft werden.

---

# Empfohlene Installation: Docker

Die vollständige Anlage wird über Docker Compose betrieben. Dabei werden automatisch drei Dienste gestartet:

- Rennsoftware auf Port 8000
- Check-in-Terminal auf Port 8010
- Chromium-Kiosk für den direkt angeschlossenen HDMI-Monitor

Auf dem Raspberry Pi müssen weder Python, Chromium noch Python-Pakete separat installiert werden. Benötigt werden nur Docker Engine mit Compose-Plugin und Raspberry Pi OS mit aktiver Wayland-Desktop-Sitzung für den HDMI-Kiosk.

## Offizielle Image-Installation

Nach dem Klonen des Repositorys:

```bash
cd ~/Quad-Racing-Software
chmod +x install_release.sh
./install_release.sh
```

Diese Installation kompiliert keine Anwendung auf dem Raspberry Pi. Sie lädt die für ARM64 und AMD64 vorbereiteten Images aus der GitHub Container Registry und startet die vollständige Anlage. Während der Entwicklungsphase wird standardmäßig der Kanal `dev` verwendet. Nach dem stabilen Release kann eine feste Version gewählt werden:

```bash
QUAD_RACE_TAG=1.7.4 ./install_release.sh
```

Das Skript erkennt Benutzer, Wayland-Sitzung sowie Video-Gruppen automatisch. Der Kiosk wartet auf die Rennsoftware und öffnet anschließend selbstständig den Monitor unter `/ui/monitor`.

## Lokale Entwicklungsinstallation

Für Entwicklung oder Tests direkt aus dem ausgecheckten Quellcode:

```bash
chmod +x install_docker.sh
./install_docker.sh
```

Dieser Weg baut die Images lokal und verwendet `compose.yaml`. Die offizielle Auslieferung verwendet dagegen `compose.release.yaml` und fertige GHCR-Images.

Datenbank, Sicherungen, Unterschriften und Vereinslogo liegen persistent in:

```text
data/
backend/static/uploads/
```

Ein Neuaufbau oder Update der Container löscht diese Daten nicht.

## Betrieb der offiziellen Installation

```bash
# Status
docker compose --env-file .env.release -f compose.release.yaml ps

# Protokolle
docker compose --env-file .env.release -f compose.release.yaml logs -f

# Aktualisierte Images laden
docker compose --env-file .env.release -f compose.release.yaml pull
docker compose --env-file .env.release -f compose.release.yaml up -d --remove-orphans

# Neustart
docker compose --env-file .env.release -f compose.release.yaml restart

# Stoppen
docker compose --env-file .env.release -f compose.release.yaml down
```

Die Container starten nach einem Neustart des Raspberry Pi automatisch wieder. Die Schaltfläche zum Neustart der Monitoranzeige arbeitet im Docker-Betrieb über den Kiosk-Container.

## Voraussetzungen für den HDMI-Kiosk

- Der Monitor ist direkt am Raspberry Pi angeschlossen.
- Für den vorgesehenen Benutzer läuft eine grafische Wayland-Sitzung.
- Der Kiosk darf auf `/dev/dri` und den Wayland-Socket zugreifen; diese Freigaben setzt Docker Compose.
- Der Renncontainer erhält gezielt `SYS_TIME`, damit die Oberfläche bei fehlendem Internet die Uhr des Raspberry Pi setzen kann. Andere zusätzliche Linux-Berechtigungen werden nicht vergeben.
- Auf dem Host darf nicht gleichzeitig ein anderer Browser dauerhaft denselben Bildschirm als Kiosk belegen.

---

# Alternative manuelle Installation

Die manuelle Python-/systemd-Installation bleibt für bestehende Anlagen erhalten. Für neue Installationen wird Docker empfohlen.

---

## 1. System vorbereiten

Die Software benötigt zwingend **Python 3.10 oder höher**.
Empfohlene Umgebung: **Raspberry Pi OS Bookworm** (enthält Python 3.11).

*Wichtiger Hinweis: Ältere Systeme wie Raspberry Pi OS Bullseye (Python 3.9) werden ausdrücklich nicht unterstützt.*

System aktualisieren und Pakete installieren:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip
```

---

## 2. Repository von GitHub klonen

```bash
cd ~
git clone https://github.com/mrpointblue/Quad-Racing-Software.git
cd /home/pi/Quad-Racing-Software
```

Falls das Repository privat ist, wird ein GitHub Login oder ein Personal Access Token benötigt.

---

## 3. Python-Umgebung erstellen

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Nach dem Aktivieren sollte vorne in der Terminalzeile ungefähr Folgendes stehen:

```text
(venv)
```

Falls lokal bereits eine Umgebung mit dem Namen `.venv` genutzt wird, entsprechend aktivieren:

```bash
source .venv/bin/activate
```

---

## 4. Abhängigkeiten installieren

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. Anwendung starten

```bash
mkdir -p data
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Die Software ist danach im Browser erreichbar, zum Beispiel:

```text
http://<IP-DES-PI>:8000
```

Für das separate lokale Check-in-Terminal kann die neue Check-in-App gestartet werden:

```bash
uvicorn backend.checkin_main:app --host 0.0.0.0 --port 8010
```

Die Check-in-Oberfläche ist dann erreichbar unter:

```text
http://<IP-DES-PI>:8010/checkin
```

Beispiel:

```text
http://192.168.178.50:8000
```

Lokal auf dem Pi:

```text
http://localhost:8000
```

---

## 6. Zugriff über Hostname

Wenn der Raspberry Pi per Hostname im Netzwerk erreichbar ist, kann die Software auch über den Hostnamen geöffnet werden:

```text
http://pi.local:8000
```

Alternativ den tatsächlichen Hostnamen des Raspberry Pi verwenden.

Beispiel:

```text
http://quad-race.local:8000
```

---

## 7. Datenbank

Die lokale Datenbank wird nicht über GitHub gesichert.

Das ist bewusst so, damit keine echten Teilnehmer-, Mitglieder- oder Veranstaltungsdaten versehentlich veröffentlicht werden.

Typische Datenbankdateien sind in `.gitignore` ausgeschlossen:

```text
*.db
*.sqlite
*.sqlite3
```

Falls eine neue Installation eingerichtet wird, muss die Datenbank lokal neu erstellt oder aus einem Backup wiederhergestellt werden.

---

## 8. Lokale Konfiguration

Lokale Konfigurationsdateien, Zugangsdaten oder Umgebungsvariablen dürfen nicht auf GitHub gespeichert werden.

Beispiele:

```text
.env
.env.*
config.local.py
```

Diese Dateien müssen je Raspberry Pi separat eingerichtet werden.

Typische lokale Einstellungen können später sein:

- IP-Adresse der Siemens LOGO
- Modbus-TCP-Port
- Registeradressen
- lokale Veranstaltungsdaten
- Datenbankpfad
- Anzeige-/Monitor-Konfiguration

---

## 9. Update auf einem bestehenden Raspberry Pi

Um die Software auf einem bereits eingerichteten Pi zu aktualisieren:

```bash
cd ~/Quad-Racing-Software
git pull
source .venv/bin/activate
pip install -r requirements.txt
```

Danach die Anwendung neu starten.

Wenn die Software manuell gestartet wurde:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Wenn die Software als Dienst läuft:

```bash
sudo systemctl restart quad-race
```

---

## 10. Änderungen sichern und auf GitHub hochladen

Nach Änderungen am Code:

```bash
git status
git add .
git commit -m "Beschreibung der Änderung"
git push
```

Beispiel:

```bash
git commit -m "Update installation instructions"
git push
```

---

# Automatisierte Installation als Dienst (systemd)

Die Software kann als Hintergrunddienst eingerichtet werden, damit sie automatisch beim Start des Raspberry Pi lädt und bei Fehlern neu startet.

---

## 1. Automatische Installation

Führe das Installations-Script aus. Dieses prüft die Python-Version, erstellt die virtuelle Umgebung, installiert alle Abhängigkeiten und registriert den systemd-Dienst:

```bash
cd /home/pi/Quad-Racing-Software
chmod +x install_service.sh
./install_service.sh
```

---

## 2. Dienst-Verwaltung

Nach der Installation kann der Dienst mit folgenden Befehlen gesteuert werden:

**Status prüfen:**
`sudo systemctl status quad-race.service`

**Dienst neu starten:**
`sudo systemctl restart quad-race.service`

**Dienst stoppen:**
`sudo systemctl stop quad-race.service`

**Dienst starten:**
`sudo systemctl start quad-race.service`

**Echtzeit-Logs anzeigen:**
`journalctl -u quad-race.service -f`

---

# Siemens LOGO! / Zeitmessanlage

Die Siemens LOGO! ist über Modbus TCP produktiv angebunden. Verbindung, Signaladressen und Impulsdauer werden in den Einstellungen der jeweiligen Installation konfiguriert.

Wichtige Punkte:

- IP-Adresse und Modbus-TCP-Port
- Holding-Register für V0/V4
- Coil-Adressen für Q1-Q3 und M1-M6
- Discrete Inputs I1/I2 für die bahnbezogene Lichtschrankenstörung
- WEB Start über M7 und WEB Reset über M9
- Verhalten bei Laufwiederholung, Neustart und Verbindungsverlust

Wichtig:

Die Reset-Funktion der LOGO darf bereits erfasste Zeiten nicht automatisch löschen.

Wenn die API nach einem Reset wieder `0` ausgibt, dürfen bereits gespeicherte Zeiten dadurch nicht verschwinden.

Ein bewusstes Löschen oder Überschreiben von Zeiten darf nur über die separate Funktion `Lauf wiederholen` erfolgen.

---

# Monitor-Seite / TV-Anzeige

Die Software ist webbasiert. Dadurch kann die Monitor-Seite auf jedem Gerät im gleichen Netzwerk angezeigt werden.

Beispiele:

```text
http://<IP-DES-PI>:8000/ui/monitor
```

oder per Hostname:

```text
http://race.local:8000/ui/monitor
```

Für einen Fernseher kann zum Beispiel ein Fire TV Stick genutzt werden.

Wichtig ist, dass der Raspberry Pi und das Anzeigegerät im gleichen Netzwerk sind.

---

# Kiosk-Modus für Monitor-Anzeige

Der Kiosk ist fester Bestandteil der Docker-Installation. Der enthaltene Chromium-Browser startet automatisch, wartet auf eine erreichbare Rennsoftware und zeigt anschließend die aktive Veranstaltung an. Eine separate Chromium-Installation oder Desktop-Autostartdatei ist bei Docker nicht erforderlich.

Die nachfolgende manuelle Konfiguration gilt nur für bestehende Installationen ohne Docker.

## Automatische Umschaltung zur aktiven Veranstaltung

Statt eine feste Veranstaltungs-ID wie `/ui/events/6/monitor` in der URL zu verwenden, wird die Route `/ui/monitor` genutzt.

Diese Route sucht automatisch nach der aktiven Veranstaltung:

- **URL:** `http://race.local:8000/ui/monitor`
- **Alias:** `http://race.local:8000/ui/active/monitor`
- Falls eine Veranstaltung mit `active=true` existiert → automatischer Redirect zum Monitor dieser Veranstaltung
- Falls keine aktive Veranstaltung existiert → Hinweisseite mit Link zur Veranstaltungsübersicht

## Chromium Kiosk-Start

Starten Sie Chromium im Kiosk-Modus mit dieser URL:

```bash
XDG_RUNTIME_DIR=/run/user/1000 \
WAYLAND_DISPLAY=wayland-0 \
chromium \
  --user-data-dir=/home/pi/chromium-race-profile \
  --ozone-platform=wayland \
  --kiosk \
  http://race.local:8000/ui/monitor
```

Dieser Befehl:

- Verwendet das Wayland-Display
- Speichert Browser-Daten in `/home/pi/chromium-race-profile`
- Startet im Kiosk-Modus (keine Menüleiste, keine Browser-UI)
- Verbindet sich zu `http://race.local:8000/ui/monitor`

## Autostart (optional)

Falls das Terminal automatisch beim Boot starten soll, kann ein Autostart-Script erstellt werden:

```bash
mkdir -p ~/.config/autostart
```

Beispiel-Datei `~/.config/autostart/quad-race-monitor.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Quad Race Monitor
Exec=bash -c 'sleep 5 && XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 chromium --user-data-dir=/home/pi/chromium-race-profile --ozone-platform=wayland --kiosk http://race.local:8000/ui/monitor'
NoDisplay=false
X-GNOME-Autostart-enabled=true
```

Die `sleep 5` Verzögerung gibt dem System Zeit, vollständig hochzufahren, bevor der Browser startet.

---

# ESP32 Startdisplay

Der Code für das optionale ESP32 LED-Matrix-Startdisplay liegt unter:

esp32/startdisplay/

Das Startdisplay wird über die Einstellungen der Software per HTTP angebunden und zeigt die aktuellen Startnummern für Bahn A und Bahn B an.

Weitere Hinweise befinden sich in:

esp32/startdisplay/README.md

---

# Version 1.7.4-dev

- Entwicklungsstand nach Release 1.7.3; noch kein stabiler Release.
- Renn- und Monitorabläufe, Check-in sowie LOGO!-Diagnose weiter stabilisiert.
- M6-Lichtschrankenstörung wird mit I1/I2 einer Bahn zugeordnet; bei Eingangslesefehlern zeigt die Diagnose den Zustand als unklar.
- Automatisierte LOGO!-Tests und GitHub-Actions-Prüfung ergänzt.
- LOGO!-Diagnoseskript auf die aktuelle Modbus-TCP-Struktur aktualisiert.
- Installationsskript verwendet automatisch das tatsächliche Projektverzeichnis und den ausführenden Benutzer.
- Installations- und LOGO!-Dokumentation korrigiert.
- Vollständige Docker-Compose-Installation für Rennsoftware, Check-in und HDMI-Kiosk ergänzt.
- Chromium ist im Kiosk-Image enthalten und muss nicht mehr auf dem Host installiert werden.
- Persistente Speicherung für Datenbank, Backups, Logos und Unterschriften eingerichtet.
- Monitor-Neustart aus der Weboberfläche mit dem Docker-Kiosk verbunden.
- Entwicklungs- und Release-Compose-Konfiguration sowie beide Images werden durch GitHub Actions geprüft.
- Multi-Arch-Images für `linux/arm64` und `linux/amd64` werden automatisch in GHCR veröffentlicht.
- `main` veröffentlicht den Kanal `dev`; Versions-Tags wie `v1.7.4` veröffentlichen `1.7.4`, `1.7` und `latest`.
- Offline-Zeitsetzung für Docker korrigiert: Host-Zeitzone wird übernommen und die Raspberry-Pi-Uhr über die freigegebene `SYS_TIME`-Berechtigung gesetzt.
- Zeitlogik in einen eigenen Service ausgelagert und mit automatisierten Tests abgesichert.

---

# Tests und Entwicklungsregeln

Vor einem Commit oder Update ausführen:

```bash
python -m compileall -q backend scripts tests
python -m unittest discover -s tests -v
docker compose config --quiet
```

GitHub Actions führt diese Prüfungen bei Änderungen an `main` und in Pull Requests automatisch aus.

`backend/main.py` enthält historisch noch viele unterschiedliche Verantwortlichkeiten. Eine vollständige Zerlegung erfolgt nicht innerhalb dieses Entwicklungsupdates, weil sie den Rennbetrieb unnötig gefährden würde. Neue Hardware-, Export-, Wertungs- und Integrationslogik gehört in eigene Module unter `backend/services` beziehungsweise in die bereits vorhandenen Logic- und Routes-Module. Bestehende Bereiche werden schrittweise und jeweils mit Tests ausgelagert.

---

# Version 1.7.2

- Stabilisierung der Modbus-TCP Kommunikation zur Siemens LOGO!
- Integration von System-Telemetrie (CPU/RAM) in der Weboberfläche
- Verbesserte Fehlerbehandlung beim Startdisplay-Dienst
- Sicherheitsprüfung für Datenbank-Backups und Imports
- Optimierung der mobilen Streckenposten-Eingabe für langsame WLAN-Verbindungen
- **Check-in Terminal:** Validierung der Altersprüfung korrigiert (Stichtagsregelung)
- **Check-in Terminal:** Fehler behoben, bei dem die Unterschrift auf einigen Tablets nicht korrekt im Backend gespeichert wurde

---

# Release-Historie ab Version 1.6.0

Die folgenden Abschnitte dokumentieren ältere Entwicklungsstände und Funktionen.

Kernpunkte:

- veranstaltungsbezogene Struktur mit `event_id`
- Navigation je Veranstaltung
- Teilnehmerverwaltung je Veranstaltung
- Startfreigabe je Veranstaltung
- Rennbetrieb je Veranstaltung
- Ergebnisverwaltung je Veranstaltung
- Exportfunktionen
- Gesamtwertung
- Rundenexporte für Viertelfinale, Halbfinale und Finale
- Berücksichtigung ausgeschiedener Fahrer
- Ausscheidegrund in Ergebnislisten und Mannschaftswertung
- Hilfe-Seite mit Ablaufhinweisen und Reglement-Link
- Nennschlusszeiten je Startfreigabe-Gruppe
- Datenbank-Sicherung in den Einstellungen
- Vereinslogo in Oberfläche, Monitor und Excel-Exports
- Vorbereitung für ADAC Nummer und Jugendausweisnummer
- Vorbereitung für Siemens LOGO / Zeitmessanlage im Simulationsmodus
- Monitor-Seite für Anzeige auf TV oder externem Bildschirm
- optionale digitale Streckenpostenfunktion je Veranstaltung
- mobile Erfassung allgemeiner und positionsbezogener Fehlerpunkte
- QR-Codes für Streckenposten-Stationen mit Monitoranzeige und Druckansicht

Neue Funktionen in Version 1.4.0:

- zweistufige Navigation mit Hauptbereich `Nennung`
- Untertab `Teilnehmerdatenbank` für Fahrerstamm/wiederkehrende Fahrer
- optionale digitale Streckenpostenfunktion in den Einstellungen
- Start-/Zielposten für allgemeine Fehlerpunkte je Bahn und Fahrer
- Streckenposten Positionen für positionsbezogene Fehlerpunkte
- mobile Streckenposten-Oberfläche für Smartphone und Tablet
- Fehlerpunkte in 5er-Schritten mit Übergabe in die Zeitnahme
- Statusanzeige in der Zeitnahme: keine Meldung, 0 FP übermittelt, FP übermittelt
- QR-Code-Anzeige für einzelne Stationen, alle Stationen oder Auswahl Station auf dem Monitor
- automatische Rücksetzung der Streckenposten-QR-Anzeige nach fünf Minuten
- Druckansichten für Streckenposten-QR-Codes

Korrekturen in Version 1.4.1:

- Veranstaltungsbereich in Untertabs für Dashboard, offene Veranstaltungen, Veranstaltung anlegen und geschlossene Veranstaltungen aufgeteilt.
- Redundante Navigationsbuttons in Teilnehmerliste, Teilnehmerdatenbank und Ergebnisansichten entfernt.
- Rennbetrieb auf einen klaren Button `Ergebnisse anzeigen` reduziert.
- Zeitnahme-Steuerung kompakter dargestellt; `Lauf wiederholen` bleibt auch bei bestätigten Läufen verfügbar, solange die Veranstaltung offen ist.
- Ergebnisnavigation vereinheitlicht: Viertelfinale, Halbfinale, Finale, Gesamtwertung, Export und Druck.
- Mobile Streckenposten-Eingabe springt beim Hochzählen der Fehlerpunkte nicht mehr durch Auto-Aktualisierung zurück auf 0.

Neue Funktionen in Version 1.4.2:

- ESP32-LED-Matrix-Startdisplay-Integration für externe Anzeige der aktuellen Startnummern
- Konfiguration in den Settings unter "Startdisplay" mit Host/IP, Port und Endpoint
- Automatische Synchronisation mit Monitor-Daten: Bahn A (links) und Bahn B (rechts)
- Ausgeschiedene Fahrer werden nicht mehr auf dem Startdisplay und Monitor angezeigt
- Test-Verbindungsfunktion für ESP32-Konnektivität mit Statusprotokoll
- Asynchrone Kommunikation ohne Blockierung des Rennbetriebs
- JSON-Payload: `{"mode": "start_numbers", "lane_a_start_number": 23, "lane_b_start_number": 54}`
- Idle-Modus: `{"mode": "idle", "lane_a_start_number": null, "lane_b_start_number": null}`

UI-Verbesserungen in Version 1.4.2:

- Hilfe-/Anleitungsseite mit aufklappbaren Kacheln für bessere Übersichtlichkeit
- Settings-Reihenfolge optimiert: Signal-Mapping → Streckenpostenfunktion → Startdisplay
- Konsistente Checkbox-Styling mit choice-row Klasse
- Verbesserte Statusanzeige für Startdisplay-Konfiguration

Korrekturen und Erweiterungen in Version 1.5.0:

- Monitor zeigt die aktuelle Paarung auch vor dem Drücken von START, solange ein nächster unbestätigter Lauf vorhanden ist.
- Digitale Streckenposten bleiben strikt optional; bei deaktivierter Funktion werden mobile Eingaben, QR-Code-Seiten und digitale Meldungen in der Zeitnahme ausgeblendet.
- Start-/Zielposten ist fachlich korrekt als eine gemeinsame Station umgesetzt und erfasst allgemeine Fehlerpunkte für Bahn A und Bahn B auf einer gemeinsamen mobilen Seite.
- Sektor-/Positionsposten bleiben bahn- und positionsbezogen.
- Marshal-Eingaben sind an die aktuelle Paarung gebunden; ein späterer Lauf kann nicht vorzeitig durch eine Marshal-Station bearbeitet werden.
- Ausgabe Monitor kann Streckenposten-QR-Codes zeitlich begrenzt anzeigen und automatisch zurücksetzen.
- Zeitnahme zeigt den nächsten Lauf im Kopfbereich klarer neben der Rennbetriebsübersicht.
- Ausgeschiedene Teilnehmer werden in Rennbetrieb und Teilnehmerliste deutlicher als Status-Badge dargestellt.
- Einstellungen enthalten eine besser lesbare Aktivierung für die digitale Streckenpostenfunktion.

LOGO-Hardwareintegration in Version 1.6.0:

- Siemens LOGO! wird über Modbus TCP eingebunden.
- Default-Verbindung: Host `192.168.2.20`, Port `502`.
- V0/V4 werden als DWORD-Zeitwerte in Hundertstelsekunden gelesen und intern für die Speicherung in Millisekunden umgerechnet.
- Q1/Q2/Q3 werden als Ampelzustände Gelb, Grün und Rot gelesen.
- M1-M6 werden als Betriebs-, Reset-, Fehlstart- und Lichtschrankenstatus gelesen.
- WEB Start wird über M7 / Coil `8262` gepulst, WEB Reset über M9 / Coil `8264`; M8 bleibt reserviert und wird nicht verwendet.
- Die Laufansicht zeigt eine echte dreifarbige Ampel, Live-Zeiten und LOGO-Statusindikatoren.
- LOGO-Fehler und Offline-Zustände bringen die Software nicht zum Absturz; manuelle Zeitkorrektur bleibt möglich.

Wichtige Korrekturen seit Version 1.3.0:

- Zeitwertung erfolgt für Anzeige, Wertung, Export, Monitor und Paarungslogik auf 1/100 Sekunde.
- Die Startnummer entscheidet keinen fachlichen Gleichstand.
- Bei gleicher Gesamtzeit und gleicher Fehlerpunktzahl erhält die Ergebnisliste einen Hinweis für die Rennleitung.
- Ausgeschiedene oder disqualifizierte Fahrer bleiben sichtbar, werden aber nicht gewertet.
- Die Mannschaftswertung berücksichtigt ausgeschiedene Fahrer mit 0 Punkten und zeigt den Ausscheidegrund an.
- Geschlossene Veranstaltungen können bewusst gelöscht werden; der Fahrerstamm bleibt erhalten.

---

# Wichtige Git-Hinweise

Die Datenbank und lokale Konfigurationen sollen nicht auf GitHub gespeichert werden.

Daher sollten folgende Dateien nicht committed werden:

```text
*.db
*.sqlite
*.sqlite3
.env
.env.*
config.local.py
```

Vor jedem Commit prüfen:

```bash
git status
```

Wenn versehentlich eine Datenbank oder Konfigurationsdatei auftaucht, nicht committen.

---

# Typischer Ablauf für neue Installation

Kurzfassung:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip

cd ~
git clone https://github.com/mrpointblue/Quad-Racing-Software.git
cd /home/pi/Quad-Racing-Software

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

mkdir -p data
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Danach im Browser öffnen:

```text
http://<IP-DES-PI>:8000
```
# Updates über die Systemeinstellungen (Docker-Auslieferung)

Unter **Systemeinstellungen → Software-Update** stehen installierter Build,
verfügbarer Build, Kanal, letzte Prüfung und **Installieren**. Der Host-Dienst
prüft stündlich sowie auf Knopfdruck. Verglichen werden Image-Digests, nicht nur
die unveränderte Versionsnummer `1.7.4-dev`. Der konfigurierte Kanal aus
`.env.release` bleibt erhalten; ein fester Versions-Tag wechselt nicht automatisch
auf eine andere Version. Für laufende Entwicklung ist der Kanal `dev` geeignet.

## Einmalige Einrichtung bestehender Installationen

In einer Rennpause, nachdem die beiden neuen Images vollständig veröffentlicht sind:

```bash
cd ~/Quad-Racing-Software
git pull --ff-only
bash install_release.sh
```

Der Installer richtet zusätzlich `quad-race-updater.service` ein. Als normaler
Docker-Benutzer ausführen; `sudo` wird nur zur Einrichtung der systemd-Unit benutzt.
Der Dienst verwendet Python 3 aus Raspberry Pi OS (keine zusätzlichen pip-Pakete),
Docker Compose, Docker Buildx und die vorhandene GHCR-Anmeldung dieses Benutzers.
Das erstmalige Einrichten benötigt deshalb noch das Terminal. Danach sind
kompatible Image-Updates über den Browser möglich. Python 3 und Buildx müssen
vorhanden sein; die Standardinstallation von Raspberry Pi OS und Docker bringt
sie mit. Der Projektpfad darf für diese Service-Installation keine Leerzeichen enthalten.

## Ablauf und Grenzen

- Kein PIN-Schutz, wie für das vertrauenswürdige Rennnetz vorgesehen. Nicht ins
  Internet freigeben. Browser-Anfragen von fremden Origins werden zurückgewiesen.
- Der Host-Dienst lauscht ausschließlich an einem Unix-Socket. Nur der
  Renncontainer erhält diesen Mount, schreibgeschützt. Weder Docker-Socket noch
  Registry-Zugangsdaten werden in die Anwendung eingebunden.
- Offline oder bei fehlenden GHCR-Rechten bleibt der Rennbetrieb unverändert;
  die Anzeige meldet den Prüfungsfehler, es wird nichts installiert.
- Nur vollständige Veröffentlichungen mit identischer Git-Revision von
  Rennsoftware und Kiosk sowie passender Updater-Protokollversion sind zulässig.
- Nach Klick auf **Installieren** werden zunächst unveränderlich per Digest
  referenzierte Images heruntergeladen. Ein gestarteter, unbestätigter Lauf oder
  laufende LOGO-Bahnen sperren die Installation. Bei aktivierter LOGO sperrt auch
  ein unbekannter oder veralteter Hardwarestatus. Der Status wird nach dem
  Download unter einer gemeinsamen Bedienungssperre erneut geprüft.
- Erst danach werden Container gestoppt und `data/` sowie `uploads/` vollständig
  in `update-backups/before-update-*.tar` gesichert. Bei fehlgeschlagener Sicherung
  werden die bisherigen Images wieder gestartet. Sicherungen bleiben erhalten;
  freien Speicher regelmäßig prüfen und Sicherungen extern aufbewahren.
- Anschließend starten die neuen Images. Docker-Healthchecks prüfen Rennsoftware
  und Check-in; beim Kiosk wird der laufende Container geprüft. Der echte HDMI-
  Bildinhalt und LOGO-Funktionen können nicht automatisch verifiziert werden.
- Während Installation/Neustart sind Oberfläche und Check-in kurz nicht erreichbar.
  Die Einstellungsseite versucht die Verbindung automatisch wiederherzustellen.
  Nach Erfolg die Seite neu laden.
- **Keine physische Startsperre der LOGO:** nur in einer Rennpause installieren,
  auch während des Updates keinen Hardwarestart auslösen. Die Software-Sperre
  ersetzt keine Verriegelung der Zeitmesshardware.
- Bei Stromausfall während des Austauschs oder fehlgeschlagenen Healthchecks
  werden weitere Updates gesperrt. Kein automatisches Downgrade nach möglichen
  Datenbankmigrationen. Container, Sicherung und Dienstprotokoll am Pi prüfen;
  `.update/status.json` nicht ungeprüft löschen. Eine Wiederherstellung muss
  passende alte Images **und** die gesicherten Daten berücksichtigen.
- Diese Funktion aktualisiert Images, nicht den Host-Dienst oder die lokale
  Compose-Datei. Änderungen daran benötigen weiterhin den einmaligen Terminalweg;
  inkompatible Images müssen ihre `io.quad-race.updater.protocol`-Version erhöhen.

Diagnose:

```bash
systemctl status quad-race-updater --no-pager
journalctl -u quad-race-updater -n 80 --no-pager
docker compose --env-file .env.release -f compose.release.yaml ps
```

