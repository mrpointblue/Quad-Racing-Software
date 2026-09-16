# Quad Racing Software

Webbasierte Verwaltung und Zeitnahme für ADAC Quad Parallel Race: Veranstaltungen,
Fahrerstamm, Nennung, Check-in mit Unterschrift, Rennbetrieb, Ergebnisse und
optionale digitale Streckenposten.

**Softwareversion: 1.7.5** · [Releases und Veröffentlichungsstatus](https://github.com/mrpointblue/Quad-Racing-Software/releases)

## Installation mit Docker

Die vollständige Installation besteht aus drei Containern:

| Dienst | Aufgabe | Adresse |
| --- | --- | --- |
| quad-race | Rennleitung, Zeitnahme, Ergebnisse und Marshal | `http://<PI-IP-ODER-HOSTNAME>:8000` |
| quad-race-checkin | Check-in-Terminal | `http://<PI-IP-ODER-HOSTNAME>:8010/checkin` |
| quad-race-kiosk | Chromium-Vollbild am HDMI-Monitor | öffnet automatisch `/ui/monitor` |

Marshal benötigt keinen zusätzlichen Container. Smartphones und Tablets müssen
den Pi im Rennnetz erreichen können.

### Voraussetzungen

- Raspberry Pi mit 64-Bit Raspberry Pi OS **mit Desktop**, angeschlossenem
  HDMI-Monitor und aktiver Wayland-Sitzung. Für den automatischen Start muss die
  Desktop-Anmeldung automatisch erfolgen.
- Docker Engine, Compose-Plugin und Buildx; der angemeldete Benutzer muss Docker
  ohne `sudo` ausführen können.
- Git und Python 3 aus Raspberry Pi OS. Der Host-Updater nutzt Python 3 ohne
  zusätzliche pip-Pakete. Chromium und die Python-Anwendungsabhängigkeiten sind
  vollständig in den Images enthalten.
- Freie Ports 8000 und 8010, genügend Speicher für Images und Sicherungen.
  Andere Dienste dürfen diese Ports nicht belegen.
- Internetzugang für Installation und Updates; der lokale Rennbetrieb benötigt
  keine Registry-Verbindung.

Die Images werden für `linux/arm64` und `linux/amd64` gebaut. Der HDMI-Kiosk
setzt auch auf anderen Linux-Rechnern eine geeignete Wayland-Sitzung und
`/dev/dri` voraus; ein reiner Headless-Server ersetzt diese nicht.

### Privater GitHub-Zugriff

Repository und Container-Pakete sind derzeit privat. Für das Klonen wird ein
berechtigter GitHub-Zugang benötigt, für GHCR zusätzlich eine Docker-Anmeldung:

```bash
cd ~
git clone https://github.com/mrpointblue/Quad-Racing-Software.git
cd Quad-Racing-Software
docker login ghcr.io -u DEIN_GITHUB_BENUTZERNAME
```

Bei HTTPS-Git-Abfragen einen für das Repository berechtigten Token statt des
GitHub-Passworts verwenden. Für GHCR einen Personal Access Token (classic) mit
`read:packages` und Zugriff auf beide Pakete verwenden. Ein erfolgreiches
`docker login` allein bestätigt noch keine Leseberechtigung für die Images.
Tokens niemals in Dateien im Repository oder in öffentliche Protokolle schreiben.

### Stabile Auslieferung installieren

Nach Veröffentlichung des Releases und erfolgreichem Docker-Publishing:

```bash
QUAD_RACE_TAG=latest bash install_release.sh
```

Das Skript als **normaler Desktop-/Docker-Benutzer**, nicht mit `sudo`, ausführen.
Es erkennt Wayland-Socket und Gruppen, lädt fertige Images, startet alle drei
Container und richtet `quad-race-updater.service` ein. Für die Einrichtung des
Host-Dienstes wird gezielt `sudo` verwendet. Der Projektpfad darf keine
Leerzeichen enthalten.

Für eine festgelegte Version stattdessen:

```bash
QUAD_RACE_TAG=1.7.5 bash install_release.sh
```

Das Skript schreibt die gewählte Version nach `.env.release`. Ohne explizite
Angabe verwendet es weiterhin `dev`; für stabile Installationen deshalb
`QUAD_RACE_TAG=latest` ausdrücklich setzen.

### Installation prüfen

```bash
docker compose --env-file .env.release -f compose.release.yaml ps
docker compose --env-file .env.release -f compose.release.yaml logs --tail=80
systemctl status quad-race-updater --no-pager
```

Rennsoftware und Check-in sollen `healthy` melden, der Kiosk soll laufen und
den Monitor anzeigen. Anschließend einen Pi-Neustart und die automatische
Vollbildanzeige prüfen. In einer Testveranstaltung Check-in, Bestätigung,
Fahrerstamm und Rennablauf testen.

## Updates und Versionskanäle

Unter **Systemeinstellungen → Software-Update → Nach Updates suchen →
Installieren** lassen sich kompatible Image-Updates einspielen. Die Anzeige
enthält Softwareversion, Builds und Kanal. Der Host-Dienst prüft zusätzlich
stündlich; er vergleicht Image-Digests.

| Kanal in .env.release | Verhalten |
| --- | --- |
| `latest` | jeweils veröffentlichte stabile Docker-Version |
| `1.7.5` | feste Version; kein automatischer Wechsel zur nächsten Version |
| `dev` | laufender Entwicklungsstand von main, nicht der stabile Kanal |

Eine bestehende `dev`-Installation wechselt durch ein normales Image-Update
nicht automatisch auf `latest`. Den Kanal in einer Rennpause bewusst wechseln:

```bash
git pull --ff-only
QUAD_RACE_TAG=latest bash install_release.sh
```

Vor diesem Terminalweg eine vollständige Sicherung erstellen. Das Installationsskript
ersetzt nicht den abgesicherten Updateablauf der Oberfläche. Bei lokalen
Git-Änderungen den Abbruch prüfen; nichts ungeprüft überschreiben.

### Updateablauf und Sicherheit

- Nur in einer Rennpause aktualisieren. Während Download und Installation
  keinen Hardwarestart auslösen: Die Software-Sperre ist **keine physische
  Startsperre der LOGO!**.
- Die Oberfläche erlaubt nur zusammengehörige Rennsoftware-/Kiosk-Builds mit
  kompatiblem Updater-Protokoll. Unterschiedliche Build-Stände können während
  der Veröffentlichung auftreten; nach Abschluss erneut prüfen.
- Ein laufender, unbestätigter Lauf oder ein nicht verlässlich lesbarer Status
  einer aktivierten LOGO! blockiert die Installation.
- Der UI-Updater lädt Images per unveränderlichem Digest, prüft die Freigabe
  erneut, stoppt die Container und sichert `data/` und `uploads/` unter
  `update-backups/before-update-*.tar`.
- Danach werden die neuen Container gestartet. Healthchecks prüfen Rennsoftware
  und Check-in; ein laufender Kiosk-Container beweist noch kein korrektes HDMI-Bild.
- Während des Neustarts sind die Oberflächen kurz nicht erreichbar. Die
  Updateanzeige verbindet sich erneut und meldet den Abschluss.
- Bei Stromausfall oder fehlgeschlagenen Healthchecks können weitere Updates
  gesperrt bleiben. Dienstprotokoll und Sicherung prüfen; `.update/status.json`
  nicht ungeprüft löschen. Kein blindes Downgrade: Images und Datenbankstand
  müssen zusammenpassen.
- Die Updatefunktion ersetzt Images, nicht das lokale Installationsskript,
  Compose oder den Host-Dienst. Änderungen daran benötigen den Terminalweg.

Der Host-Updater verwendet die Docker-/GHCR-Anmeldung des Installationsbenutzers.
Docker-Socket und Registry-Zugangsdaten werden nicht in die Rennanwendung
eingebunden. Die Oberfläche besitzt keinen PIN-Schutz: **nur im vertrauenswürdigen
Rennnetz betreiben, nicht direkt ins Internet freigeben**.

## Daten und Sicherungen

Bei der **Release-Installation** bleiben folgende Verzeichnisse auf dem Host:

| Pfad | Inhalt |
| --- | --- |
| `data/` | SQLite-Datenbank, Einstellungen und Anwendungssicherungen |
| `uploads/` | Logos und Unterschriften |
| `update-backups/` | vollständige Sicherungen des UI-Updaters |
| `.env.release` | lokaler Versionskanal und Kiosk-Konfiguration |
| `.update/` | Updater-Socket und Betriebsstatus |

`uploads/` wird im Container nach `/app/backend/static/uploads` eingebunden.
Bei klassischer Installation bzw. lokalem Entwicklungs-Compose kann der
Host-Pfad abweichen; maßgeblich ist die jeweilige Compose-Konfiguration.

Container-Updates löschen die persistenten Daten nicht. Trotzdem Sicherungen
regelmäßig **extern** aufbewahren und freien Speicher kontrollieren.
Für eine vollständige manuelle Sicherung die Anwendung zuvor anhalten, damit
Datenbank und Dateien konsistent sind. Reale Teilnehmerdaten, Unterschriften,
Datenbanken, Tokens und lokale Konfigurationen gehören nicht ins Git-Repository.

## Siemens LOGO! und Offline-Uhrzeit

Die Zeitmessanlage wird über Modbus TCP angebunden. Host/IP, Port,
Signaladressen und Impulsdauer in den Systemeinstellungen passend zur realen
LOGO!-Programmierung konfigurieren.

- V0/V4 liefern Zeitwerte in Hundertstelsekunden.
- Q1–Q3 liefern die Ampelzustände; M6 wird zusammen mit I1/I2 zur Zuordnung
  einer Lichtschrankenstörung ausgewertet.
- WEB-Start verwendet M7, WEB-Reset M9; M8 ist reserviert.
- Ein LOGO!-Reset darf bereits gespeicherte Zeiten nicht löschen.
  Ein bewusstes Zurücksetzen erfolgt über „Lauf wiederholen“.

Für das Offline-Setzen der Pi-Uhr erhält ausschließlich der Renncontainer
`SYS_TIME`; die Host-Zeitzone wird eingebunden. Diese Berechtigung verändert
die **Host-Uhr**, nicht nur eine separate Container-Uhr.

HDMI-Vollbild nach Neustart und manuelles Zeitsetzen wurden auf dem Test-Pi
bestätigt. Die reale LOGO! stand für den abschließenden Docker-Praxistest nicht
zur Verfügung. Vor dem Renneinsatz daher Start, Reset, Fehlstart, beide Bahnen
und Verbindungsverlust mit der tatsächlichen Anlage prüfen. Automatisierte
Tests ersetzen diese Hardware-Abnahme nicht.

## Monitor und optionales Startdisplay

Der HDMI-Kiosk ist fester Bestandteil der Docker-Installation. Kein zusätzliches
Host-Chromium und keine eigene Browser-Autostartdatei sind erforderlich.
`/ui/monitor` öffnet die aktive Veranstaltung.

Unter Monitoreinstellungen stehen **Normal**, **Gedreht links** und
**Gedreht rechts** zur Verfügung. Der Monitor kann zusätzlich über einen Browser
im Rennnetz aufgerufen werden.

Das optionale ESP32-LED-Matrix-Startdisplay wird per HTTP angebunden.
Siehe [Startdisplay-Anleitung](esp32/startdisplay/README.md).

## Änderungen in 1.7.5

- Das initiale Viertelfinale wird nicht mehr zufällig ausgelost. Die numerisch
  kleinste Startnummer fährt gegen die größte, danach die zweitkleinste gegen
  die zweitgrößte. Mehrstellige Startnummern werden korrekt eingeordnet.
- Halbfinale und Finale bleiben ergebnisbasiert: Die qualifizierten Fahrer
  werden von der Mitte nach außen gepaart. Bei einer ungeraden Teilnehmerzahl
  fährt der letztplatzierte qualifizierte Teilnehmer allein.
- Startfreigabe und Rennbetrieb sind nur noch in der aktiven Veranstaltung
  möglich. In inaktiven Veranstaltungen werden die betreffenden Aktionen
  ausgeblendet oder gesperrt und zusätzlich serverseitig abgewiesen.
- Die Hilfeseite folgt jetzt der sichtbaren Oberfläche, verwendet deren
  Bezeichnungen und erklärt die Abläufe übersichtlicher.
- Der Docker-Veröffentlichungsablauf installiert vor den Regressionstests alle
  benötigten Python-Abhängigkeiten.

Ältere Änderungsstände stehen in den
[GitHub-Releases](https://github.com/mrpointblue/Quad-Racing-Software/releases)
und der Git-Historie.

## Entwicklung und bestehende Installationen ohne Docker

Für lokale Docker-Builds `bash install_docker.sh` verwenden. Dieser Weg nutzt
`compose.yaml` und `.env.docker`, nicht die Release-Konfiguration.

Klassische Python-/systemd-Installationen bleiben möglich; Python 3.10 oder
neuer ist erforderlich. Einstieg: `bash install_service.sh`. Bei einem Wechsel
zu Docker vorher Daten sichern und alte Dienste/Kiosk-Autostarts kontrolliert
stoppen, damit Ports und Bildschirm nicht doppelt belegt werden. Bestehende
Uploads müssen zum Host-Pfad der gewählten Installation übernommen werden.

Prüfungen im Entwicklungsumfeld mit installierten Anwendungsabhängigkeiten:

```bash
python3 -m pip install -r requirements.txt
python3 -m compileall -q backend scripts tests
python3 -m unittest discover -s tests -v
node tests/test_updates_ui.cjs
node tests/test_monitor_rotation.cjs
docker compose config --quiet
docker compose --env-file .env.release -f compose.release.yaml config --quiet
```

GitHub Actions prüft außerdem Installationsskripte, Image-Builds und den
Containerstart. Neue Fachlogik in separate Services/Logic-Module auslagern;
bestehende Bereiche nur schrittweise und mit Tests ändern.

## Fehlerdiagnose

```bash
docker compose --env-file .env.release -f compose.release.yaml logs --tail=100
journalctl -u quad-race-updater -n 80 --no-pager
```

- **GHCR denied:** Paketberechtigung und Anmeldung des Installationsbenutzers prüfen.
- **Keine Wayland-Sitzung:** grafische Anmeldung desselben Benutzers prüfen.
- **Check-in nicht erreichbar:** Check-in-Container und Port 8010 prüfen; die
  Adresse muss auf den Pi zeigen, nicht auf localhost des Smartphones.
- **Update-Dienst fehlt:** aktuelle Installationsdateien laden und
  `install_release.sh` mit bewusst gewähltem Kanal ausführen.

## Lizenz

Siehe [LICENSE](LICENSE).
