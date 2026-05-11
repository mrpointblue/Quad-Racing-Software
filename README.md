# Quad Racing Software

Webbasierte Software zur Verwaltung und Durchführung von ADAC Quad Parallel Race Veranstaltungen.

---

# How To Install

Diese Anleitung beschreibt, wie die Quad Racing Software auf einem Raspberry Pi installiert, gestartet, aktualisiert und optional als Dienst eingerichtet wird.

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
python3 -m venv venv
source venv/bin/activate
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
source venv/bin/activate
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
chmod +x scripts/install_service.sh
./scripts/install_service.sh
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

# Hinweise zur Siemens LOGO / Zeitmessanlage (Achtung, derzeit noch nicht implementiert)

Die Anbindung an die Siemens LOGO beziehungsweise Zeitmessanlage erfolgt lokal über die Konfiguration der jeweiligen Installation.

Wichtige Punkte:

- IP-Adresse der LOGO
- Modbus-TCP-Port
- Registeradressen
- Reset-Signal
- Startsignal
- Zielsignal
- Verhalten bei Laufwiederholung
- Verhalten bei Neustart der Software
- Verhalten bei Verbindungsverlust zur LOGO

Wichtig:

Die Reset-Funktion der LOGO darf bereits erfasste Zeiten nicht automatisch löschen.

Wenn die API nach einem Reset wieder `0` ausgibt, dürfen bereits gespeicherte Zeiten dadurch nicht verschwinden.

Ein bewusstes Löschen oder Überschreiben von Zeiten darf nur über die separate Funktion `Lauf wiederholen` erfolgen.

---

# Monitor-Seite / TV-Anzeige

Die Software ist webbasiert. Dadurch kann die Monitor-Seite auf jedem Gerät im gleichen Netzwerk angezeigt werden.

Beispiele:

```text
http://<IP-DES-PI>:8000/monitor
```

oder per Hostname:

```text
http://quad-race.local:8000/monitor
```

Für einen Fernseher kann zum Beispiel ein Fire TV Stick genutzt werden.

Wichtig ist, dass der Raspberry Pi und das Anzeigegerät im gleichen Netzwerk sind.

---

# Projektstand / Version 1.4.1

Dieser Stand basiert auf Version 1.4.1 der Quad Parallel Race Software.

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

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

mkdir -p data
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Danach im Browser öffnen:

```text
http://<IP-DES-PI>:8000
```
