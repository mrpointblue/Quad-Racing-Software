# ESP32 Startdisplay

Dieses Verzeichnis enthält die Firmware für das optionale WLAN-Startdisplay der Quad Parallel Race Software.

## Hardware

Dieses Projekt ist für ein **64x32 LED Display mit HUB75 Anschluss** ausgelegt.

Das Display wird direkt an den ESP32 angeschlossen und zeigt Startnummern für zwei Rennbahnen an.

## Funktion

Das Startdisplay zeigt die Startnummern der aktuellen Paarung an:

- Bahn A
- Bahn B

Die Quad-Racing-Software sendet die Daten per HTTP POST an den ESP32.

## Standard-Endpunkt

http://<ESP32-IP>/display

## Erwartetes JSON

Aktuelle Startnummern:

```json
{
  "mode": "start_numbers",
  "lane_a_start_number": 23,
  "lane_b_start_number": 54
}
```

Leerlauf / Anzeige löschen:

```json
{
  "mode": "idle",
  "lane_a_start_number": null,
  "lane_b_start_number": null
}
```

Bei `null` oder fehlender Paarung zeigt das Display `--/--` und löscht vorherige Startnummern.

## Konfiguration in der Quad-Racing-Software

Die Konfiguration erfolgt in der Weboberfläche unter:

Einstellungen → Startdisplay

Dort werden eingetragen:

- Aktiviert: Ja
- Host/IP: IP-Adresse des ESP32
- Port: 80
- Endpunkt: /display

## Installation / Flashen

Die Datei `quad-startdisplay.ino` wird mit der Arduino IDE oder Arduino CLI auf den ESP32 geflasht.

Benötigte Arduino-Libraries:

- WiFi
- WebServer
- DNSServer
- Preferences
- ArduinoJson
- ESPmDNS
- ESP32-HUB75-MatrixPanel-I2S-DMA

## Setup-Modus

Wenn keine WLAN-Daten gespeichert sind, startet der ESP32 einen eigenen Access Point:

SSID: Quad-Startdisplay-Setup
Passwort: quadstart
Adresse: http://192.168.4.1

Dort können die WLAN-Daten eingetragen werden.

## Hinweis

Das Startdisplay ist ein Zusatzmodul.
Die Quad-Racing-Software läuft auch ohne aktiviertes Startdisplay.
