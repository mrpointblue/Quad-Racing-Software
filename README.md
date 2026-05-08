# Quad Racing Software

Webbasierte Software zur Verwaltung und Durchführung von Quad Parallel Race Veranstaltungen.

---

# How To Install

Diese Anleitung beschreibt, wie die Quad Racing Software auf einem Raspberry Pi installiert, gestartet, aktualisiert und optional als Dienst eingerichtet wird.

---

## 1. System vorbereiten

Auf dem Raspberry Pi zuerst das System aktualisieren und die benötigten Pakete installieren:

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
cd Quad-Racing-Software
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

---

## 4. Abhängigkeiten installieren

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. Anwendung starten

Je nach Projektstruktur liegt die eigentliche Anwendung im Ordner `backend`.

```bash
cd backend
python app.py
```

Die Software ist danach im Browser erreichbar, zum Beispiel:

```text
http://<IP-DES-PI>:5000
```

Beispiel:

```text
http://192.168.178.50:5000
```

Lokal auf dem Pi:

```text
http://localhost:5000
```

---

## 6. Zugriff über Hostname

Wenn der Raspberry Pi per Hostname im Netzwerk erreichbar ist, kann die Software auch über den Hostnamen geöffnet werden:

```text
http://pi.local:5000
```

Alternativ den tatsächlichen Hostnamen des Raspberry Pi verwenden.

Beispiel:

```text
http://quad-race.local:5000
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
cd backend
python app.py
```

Wenn die Software als Dienst läuft:

```bash
sudo systemctl restart quad-race.service
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

# Autostart über systemd

Optional kann die Software als Dienst eingerichtet werden, damit sie automatisch beim Start des Raspberry Pi läuft.

---

## 1. systemd-Service erstellen

```bash
sudo nano /etc/systemd/system/quad-race.service
```

Diesen Inhalt einfügen:

```ini
[Unit]
Description=Quad Racing Software
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/Quad-Racing-Software/backend
ExecStart=/home/pi/Quad-Racing-Software/venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Speichern mit:

```text
STRG + O
Enter
STRG + X
```

---

## 2. Dienst aktivieren

```bash
sudo systemctl daemon-reload
sudo systemctl enable quad-race.service
sudo systemctl start quad-race.service
```

---

## 3. Dienststatus prüfen

```bash
sudo systemctl status quad-race.service
```

---

## 4. Logs anzeigen

```bash
journalctl -u quad-race.service -f
```

---

## 5. Dienst neu starten

```bash
sudo systemctl restart quad-race.service
```

---

## 6. Dienst stoppen

```bash
sudo systemctl stop quad-race.service
```

---

# Hinweise zur Siemens LOGO / Zeitmessanlage

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
http://<IP-DES-PI>:5000/monitor
```

oder per Hostname:

```text
http://quad-race.local:5000/monitor
```

Für einen Fernseher kann zum Beispiel ein Fire TV Stick genutzt werden.

Wichtig ist, dass der Raspberry Pi und das Anzeigegerät im gleichen Netzwerk sind.

---

# Projektstand

Dieser Stand basiert auf dem ersten stabilen Release der Quad Parallel Race Software.

Kernpunkte:

- veranstaltungsbezogene Struktur mit `event_id`
- Navigation je Veranstaltung
- Teilnehmerverwaltung je Veranstaltung
- Nennfreigabe je Veranstaltung
- Laufverwaltung je Veranstaltung
- Ergebnisverwaltung je Veranstaltung
- Exportfunktionen
- Gesamtwertung
- Rundenexporte für Viertelfinale, Halbfinale und Finale
- Berücksichtigung ausgeschiedener Fahrer
- Vorbereitung für ADAC Nummer
- Vorbereitung für Jugendausweisnummer
- Vorbereitung für Siemens LOGO / Zeitmessanlage
- Monitor-Seite für Anzeige auf TV oder externem Bildschirm

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
cd Quad-Racing-Software

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cd backend
python app.py
```

Danach im Browser öffnen:

```text
http://<IP-DES-PI>:5000
```