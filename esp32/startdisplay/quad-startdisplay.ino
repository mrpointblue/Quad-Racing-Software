/*
  Quad Parallel Race - ESP32 Startdisplay

  Diese Datei enthält die Firmware für das optionale WLAN-Startdisplay.
  Der finale funktionierende Arduino-Code wird hier eingefügt.

  Erwarteter HTTP-Endpunkt:
  POST /display

  Erwartetes JSON:
  {
    "mode": "start_numbers",
    "lane_a_start_number": "23",
    "lane_b_start_number": "54"
  }

  Idle:
  {
    "mode": "idle",
    "lane_a_start_number": "",
    "lane_b_start_number": ""
  }
*/