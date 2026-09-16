# Quad Parallel Race v1.7.5

## Änderungen

- Das initiale Viertelfinale wird jetzt nach Startnummern gepaart: kleinste gegen größte Startnummer, danach zweitkleinste gegen zweitgrößte. Mehrstellige Startnummern werden numerisch korrekt eingeordnet.
- Halbfinale und Finale werden weiterhin nach dem Ergebnis der vorherigen Runde von der Mitte nach außen gepaart. Bei ungerader Teilnehmerzahl fährt der letztplatzierte qualifizierte Teilnehmer allein.
- Startfreigabe und Rennbetrieb sind nur noch in der aktiven Veranstaltung möglich. Die Oberfläche sperrt die betreffenden Aktionen; direkte Aufrufe werden zusätzlich serverseitig abgewiesen.
- Die Hilfeseite wurde übersichtlicher aufgebaut und verwendet die Bezeichnungen der sichtbaren Oberfläche.
- Der Docker-Veröffentlichungsablauf installiert die benötigten Python-Abhängigkeiten vor den Regressionstests.

## Auslieferung

Die Stable-Docker-Images werden für ARM64 und AMD64 veröffentlicht. Der Versions-Tag `1.7.5` bleibt fest auf diesem Stand; `latest` verweist auf die aktuelle Stable-Version.

## Aktualisierung

Bestehende Stable-Installationen können das Update unter **Systemeinstellungen → Software-Update → Nach Updates suchen → Installieren** einspielen, sobald beide Docker-Images vollständig veröffentlicht sind.

Für eine feste Installation über das Terminal:

```bash
QUAD_RACE_TAG=1.7.5 bash install_release.sh
```
