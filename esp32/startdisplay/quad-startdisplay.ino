#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

// ============================================================
// Gerät / Netzwerk
// ============================================================

const char* DEVICE_HOSTNAME = "quad-startdisplay";

const char* SETUP_AP_SSID = "Quad-Startdisplay-Setup";
const char* SETUP_AP_PASS = "quadstart";

IPAddress apIP(192, 168, 4, 1);
IPAddress netMsk(255, 255, 255, 0);

Preferences preferences;
DNSServer dnsServer;
WebServer server(80);

bool setupMode = false;
String wifiSsid = "";
String wifiPass = "";

// ============================================================
// Matrix-Konfiguration
// Pinbelegung passend zu deiner funktionierenden ESPHome-YAML
// ============================================================

#define PANEL_WIDTH   64
#define PANEL_HEIGHT  32
#define PANEL_CHAIN   1

#define R1_PIN 18
#define G1_PIN 8
#define B1_PIN 17

#define R2_PIN 16
#define G2_PIN 1
#define B2_PIN 15

#define A_PIN  7
#define B_PIN  48
#define C_PIN  6
#define D_PIN  47
#define E_PIN  2

#define CLK_PIN 5
#define LAT_PIN 21
#define OE_PIN  4

MatrixPanel_I2S_DMA* display = nullptr;

// ============================================================
// Display-State
// ============================================================

String currentMode = "boot";
int laneA = -1;
int laneB = -1;

unsigned long lastDataMs = 0;
unsigned long lastScreenUpdateMs = 0;

// Kein automatisches Löschen nach Timeout.
// Ohne gültige Daten wird < A / B > / NO DATA angezeigt.
// Letzte gültige Startnummern bleiben stehen,
// wenn danach idle oder ungültige Daten empfangen werden.

// ============================================================
// Farben
// ============================================================

uint16_t COLOR_BLACK;
uint16_t COLOR_WHITE;
uint16_t COLOR_GREEN;
uint16_t COLOR_MAGENTA;
uint16_t COLOR_ORANGE;
uint16_t COLOR_RED;
uint16_t COLOR_BLUE;

// ============================================================
// Anzeige-Helfer
// ============================================================

void clearDisplay() {
  display->fillScreen(COLOR_BLACK);
}

void drawCentered(const String& text, int y, uint8_t textSize, uint16_t color) {
  int16_t x1, y1;
  uint16_t w, h;

  display->setTextSize(textSize);
  display->setTextColor(color);
  display->setTextWrap(false);

  display->getTextBounds(text, 0, y, &x1, &y1, &w, &h);

  int x = (PANEL_WIDTH - w) / 2;
  if (x < 0) x = 0;

  display->setCursor(x, y);
  display->print(text);
}

void showBoot() {
  clearDisplay();
  drawCentered("BOOT", 7, 2, COLOR_WHITE);
}

void showWifiConnecting() {
  clearDisplay();
  drawCentered("WLAN", 1, 2, COLOR_BLUE);
  drawCentered("SUCHE", 17, 1, COLOR_WHITE);
}

void showWifiOk() {
  clearDisplay();
  drawCentered("WLAN OK", 8, 1, COLOR_GREEN);
}

void showNoData() {
  clearDisplay();

  // 3 Pixel tiefer
  drawCentered("< A / B >", 3, 1, COLOR_GREEN);
  drawCentered("NO DATA", 17, 1, COLOR_ORANGE);
}

void showSetup() {
  clearDisplay();
  drawCentered("SETUP", 0, 2, COLOR_ORANGE);
  drawCentered("192.168.4.1", 18, 1, COLOR_WHITE);
}

void showOffline() {
  clearDisplay();
  drawCentered("OFF", 0, 2, COLOR_RED);
  drawCentered("LINE", 16, 2, COLOR_RED);
}

void showBadRequest() {
  clearDisplay();
  drawCentered("JSON", 1, 2, COLOR_RED);
  drawCentered("FEHLER", 17, 1, COLOR_WHITE);
}

void showStartNumbers(int a, int b) {
  clearDisplay();

  // 3 Pixel tiefer
  drawCentered("< A / B >", 3, 1, COLOR_GREEN);

  // Kompakter ohne Leerzeichen, damit es nicht abgeschnitten wird
  String numbers = String(a) + "/" + String(b);

  // Bis z. B. 26/4, 123/4 oder 12/34 groß anzeigen
  if (numbers.length() <= 5) {
    drawCentered(numbers, 16, 2, COLOR_MAGENTA);
  } else {
    // Bei sehr langen Startnummern automatisch kleiner
    drawCentered(numbers, 18, 1, COLOR_MAGENTA);
  }
}

void updateDisplay() {
  if (setupMode) {
    showSetup();
  } else if (currentMode == "start_numbers" && laneA >= 0 && laneB >= 0) {
    showStartNumbers(laneA, laneB);
  } else if (currentMode == "offline") {
    showOffline();
  } else {
    showNoData();
  }

  lastScreenUpdateMs = millis();
}

// ============================================================
// WLAN Config
// ============================================================

void loadWifiConfig() {
  preferences.begin("wifi", true);
  wifiSsid = preferences.getString("ssid", "");
  wifiPass = preferences.getString("pass", "");
  preferences.end();

  Serial.print("[CONFIG] SSID geladen: ");
  Serial.println(wifiSsid.length() > 0 ? wifiSsid : "(leer)");
}

void saveWifiConfig(const String& ssid, const String& pass) {
  preferences.begin("wifi", false);
  preferences.putString("ssid", ssid);
  preferences.putString("pass", pass);
  preferences.end();

  Serial.println("[CONFIG] WLAN gespeichert");
}

void clearWifiConfig() {
  preferences.begin("wifi", false);
  preferences.clear();
  preferences.end();

  Serial.println("[CONFIG] WLAN gelöscht");
}

// ============================================================
// HTML
// ============================================================

String htmlHeader(const String& title) {
  String html;
  html += "<!doctype html><html><head><meta charset='utf-8'>";
  html += "<meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<title>" + title + "</title>";
  html += "<style>";
  html += "body{font-family:Arial,sans-serif;margin:24px;background:#111;color:#eee;}";
  html += ".card{max-width:560px;margin:auto;background:#1d1d1d;padding:20px;border-radius:12px;}";
  html += "input,button{width:100%;padding:12px;margin:8px 0;font-size:16px;border-radius:8px;border:0;box-sizing:border-box;}";
  html += "button{background:#00a86b;color:white;font-weight:bold;}";
  html += ".danger{background:#b00020;}";
  html += ".muted{color:#aaa;font-size:14px;}";
  html += "pre{background:#000;padding:12px;border-radius:8px;overflow:auto;}";
  html += "a{color:#60a5fa;}";
  html += "</style></head><body><div class='card'>";
  return html;
}

String htmlFooter() {
  return "</div></body></html>";
}

// ============================================================
// HTTP Seiten
// ============================================================

void handleSetupPage() {
  String html = htmlHeader("Quad Startdisplay Setup");
  html.reserve(2048);

  html += "<h1>Quad Startdisplay</h1>";
  html += "<h2>WLAN konfigurieren</h2>";

  if (setupMode) {
    html += "<p class='muted'>Setup-Modus aktiv.</p>";
    html += "<p class='muted'>AP: ";
    html += SETUP_AP_SSID;
    html += "</p>";
  } else {
    html += "<p class='muted'>Normalbetrieb.</p>";
    html += "<p class='muted'>IP: ";
    html += WiFi.localIP().toString();
    html += "</p>";
    html += "<p class='muted'>Port: 80</p>";
  }

  html += "<form method='POST' action='/save-wifi'>";
  html += "<label>WLAN SSID</label>";
  html += "<input name='ssid' value='" + wifiSsid + "' placeholder='WLAN Name' required>";
  html += "<label>WLAN Passwort</label>";
  html += "<input name='pass' type='password' placeholder='WLAN Passwort'>";
  html += "<button type='submit'>Speichern und neu starten</button>";
  html += "</form>";

  html += "<hr>";
  html += "<h3>Race Software Ziel</h3>";
  html += "<p class='muted'>In der Quad Race Software eintragen:</p>";
  html += "<pre>Host/IP: ";
  html += setupMode ? "192.168.4.1" : WiFi.localIP().toString();
  html += "\nPort: 80\nEndpoint: /display</pre>";

  html += "<p><a href='/status'>Status JSON</a></p>";

  html += "<form method='POST' action='/reset-wifi'>";
  html += "<button class='danger' type='submit'>WLAN-Daten löschen</button>";
  html += "</form>";

  html += htmlFooter();

  server.send(200, "text/html", html);
}

void handleRoot() {
  if (setupMode) {
    handleSetupPage();
    return;
  }

  String html = htmlHeader("Quad Startdisplay");
  html.reserve(1500);

  html += "<h1>Quad Startdisplay</h1>";
  html += "<p>Status: " + currentMode + "</p>";
  html += "<p>Bahn A: " + String(laneA) + "</p>";
  html += "<p>Bahn B: " + String(laneB) + "</p>";
  html += "<p>IP: " + WiFi.localIP().toString() + "</p>";
  html += "<p>Port: 80</p>";
  html += "<p>Hostname: ";
  html += DEVICE_HOSTNAME;
  html += ".local</p>";

  html += "<h3>Race Software Ziel</h3>";
  html += "<pre>Host/IP: ";
  html += WiFi.localIP().toString();
  html += "\nPort: 80\nEndpoint: /display</pre>";

  html += "<h3>Endpoint</h3>";
  html += "<pre>POST /display</pre>";

  html += "<h3>Beispiel</h3>";
  html += "<pre>{\"mode\":\"start_numbers\",\"lane_a_start_number\":23,\"lane_b_start_number\":54}</pre>";

  html += "<p><a href='/setup'>WLAN Setup</a></p>";
  html += "<p><a href='/status'>Status JSON</a></p>";

  html += htmlFooter();

  server.send(200, "text/html", html);
}

void handleSaveWifi() {
  String ssid = server.arg("ssid");
  String pass = server.arg("pass");

  ssid.trim();

  if (ssid.length() == 0) {
    server.send(400, "text/plain", "SSID fehlt");
    return;
  }

  saveWifiConfig(ssid, pass);

  String html = htmlHeader("Gespeichert");
  html += "<h1>Gespeichert</h1>";
  html += "<p>WLAN-Daten wurden gespeichert.</p>";
  html += "<p>ESP32 startet neu.</p>";
  html += htmlFooter();

  server.send(200, "text/html", html);
  delay(1000);
  ESP.restart();
}

void handleResetWifi() {
  clearWifiConfig();

  String html = htmlHeader("WLAN gelöscht");
  html += "<h1>WLAN gelöscht</h1>";
  html += "<p>Gespeicherte WLAN-Daten wurden gelöscht.</p>";
  html += "<p>ESP32 startet neu im Setup-Modus.</p>";
  html += htmlFooter();

  server.send(200, "text/html", html);
  delay(1000);
  ESP.restart();
}

void handleStatus() {
  JsonDocument doc;

  doc["status"] = "ok";
  doc["setup_mode"] = setupMode;
  doc["mode"] = currentMode;

  if (laneA >= 0) {
    doc["lane_a_start_number"] = laneA;
  } else {
    doc["lane_a_start_number"] = (char*)NULL;
  }

  if (laneB >= 0) {
    doc["lane_b_start_number"] = laneB;
  } else {
    doc["lane_b_start_number"] = (char*)NULL;
  }

  doc["ip"] = setupMode ? apIP.toString() : WiFi.localIP().toString();
  doc["hostname"] = DEVICE_HOSTNAME;
  doc["port"] = 80;
  doc["endpoint"] = "/display";
  doc["last_data_ms_ago"] = lastDataMs > 0 ? (long)(millis() - lastDataMs) : -1;

  String response;
  response.reserve(256);
  serializeJson(doc, response);

  server.send(200, "application/json", response);
}

// ============================================================
// HTTP POST /display
// ============================================================

void handleDisplayPost() {
  if (server.method() != HTTP_POST) {
    server.send(405, "application/json", "{\"error\":\"method_not_allowed\"}");
    return;
  }

  String body = server.arg("plain");

  if (body.length() == 0) {
    server.send(400, "application/json", "{\"error\":\"empty_body\"}");
    return;
  }

  Serial.println();
  Serial.println("[POST /display]");
  Serial.println(body);

  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, body);

  if (error) {
    Serial.print("[JSON] Fehler: ");
    Serial.println(error.c_str());

    currentMode = "json_error";
    showBadRequest();

    server.send(400, "application/json", "{\"ok\":false,\"error\":\"invalid_json\"}");
    return;
  }

  const char* mode = doc["mode"] | "idle";

  if (strcmp(mode, "start_numbers") == 0) {
    int a = doc["lane_a_start_number"] | -1;
    int b = doc["lane_b_start_number"] | -1;

    if (a < 0 || b < 0) {
      lastDataMs = millis();

      // Ungültige Startnummern dürfen eine vorhandene Anzeige nicht löschen.
      // Wenn noch keine gültige Anzeige vorhanden ist, bleibt NO DATA sichtbar.
      if (currentMode != "start_numbers") {
        currentMode = "idle";
        laneA = -1;
        laneB = -1;
        updateDisplay();
      }

      server.send(200, "application/json", "{\"ok\":true,\"mode\":\"invalid_numbers_ignored_if_numbers_active\"}");
      return;
    }

    currentMode = "start_numbers";
    laneA = a;
    laneB = b;
    lastDataMs = millis();

    updateDisplay();

    JsonDocument responseDoc;
    responseDoc["ok"] = true;
    responseDoc["mode"] = "start_numbers";
    responseDoc["lane_a_start_number"] = laneA;
    responseDoc["lane_b_start_number"] = laneB;

    String response;
    serializeJson(responseDoc, response);

    server.send(200, "application/json", response);
    return;
  }

  if (strcmp(mode, "idle") == 0) {
    lastDataMs = millis();

    // Wichtig:
    // Idle darf eine bereits gültige Anzeige nicht überschreiben.
    // Die Quad Race Software sendet aktuell regelmäßig idle.
    // Deshalb bleibt die letzte gültige Startnummernanzeige stehen.
    if (currentMode != "start_numbers") {
      currentMode = "idle";
      laneA = -1;
      laneB = -1;
      updateDisplay();
    }

    server.send(200, "application/json", "{\"ok\":true,\"mode\":\"idle_ignored_if_numbers_active\"}");
    return;
  }

  lastDataMs = millis();

  // Unbekannte Modi nicht die aktive Anzeige überschreiben lassen.
  if (currentMode != "start_numbers") {
    currentMode = "idle";
    laneA = -1;
    laneB = -1;
    updateDisplay();
  }

  server.send(200, "application/json", "{\"ok\":true,\"mode\":\"unknown_ignored_if_numbers_active\"}");
}

void handleNotFound() {
  if (setupMode) {
    handleSetupPage();
    return;
  }

  server.send(404, "application/json", "{\"error\":\"not_found\"}");
}

// ============================================================
// Matrix Setup
// ============================================================

void setupMatrix() {
  HUB75_I2S_CFG mxconfig(
    PANEL_WIDTH,
    PANEL_HEIGHT,
    PANEL_CHAIN
  );

  mxconfig.gpio.r1 = R1_PIN;
  mxconfig.gpio.g1 = G1_PIN;
  mxconfig.gpio.b1 = B1_PIN;

  mxconfig.gpio.r2 = R2_PIN;
  mxconfig.gpio.g2 = G2_PIN;
  mxconfig.gpio.b2 = B2_PIN;

  mxconfig.gpio.a = A_PIN;
  mxconfig.gpio.b = B_PIN;
  mxconfig.gpio.c = C_PIN;
  mxconfig.gpio.d = D_PIN;
  mxconfig.gpio.e = E_PIN;

  mxconfig.gpio.clk = CLK_PIN;
  mxconfig.gpio.lat = LAT_PIN;
  mxconfig.gpio.oe = OE_PIN;

  mxconfig.clkphase = false;

  display = new MatrixPanel_I2S_DMA(mxconfig);

  if (!display->begin()) {
    Serial.println("[MATRIX] Fehler bei begin()");
    return;
  }

  display->setBrightness8(90);

  COLOR_BLACK = display->color565(0, 0, 0);
  COLOR_WHITE = display->color565(255, 255, 255);
  COLOR_GREEN = display->color565(0, 255, 0);
  COLOR_MAGENTA = display->color565(255, 0, 255);
  COLOR_ORANGE = display->color565(255, 120, 0);
  COLOR_RED = display->color565(255, 0, 0);
  COLOR_BLUE = display->color565(0, 80, 255);

  clearDisplay();
}

// ============================================================
// WLAN
// ============================================================

void startSetupAccessPoint() {
  setupMode = true;

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(apIP, apIP, netMsk);
  WiFi.softAP(SETUP_AP_SSID, SETUP_AP_PASS);

  dnsServer.start(53, "*", apIP);

  Serial.println("[AP] Setup Access Point gestartet");
  Serial.print("[AP] SSID: ");
  Serial.println(SETUP_AP_SSID);
  Serial.print("[AP] Passwort: ");
  Serial.println(SETUP_AP_PASS);
  Serial.print("[AP] IP: ");
  Serial.println(apIP);

  updateDisplay();
}

void connectWifi() {
  loadWifiConfig();

  if (wifiSsid.length() == 0) {
    Serial.println("[WLAN] Keine gespeicherte SSID");
    startSetupAccessPoint();
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(DEVICE_HOSTNAME);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());

  showWifiConnecting();

  Serial.print("[WLAN] Verbinde mit ");
  Serial.println(wifiSsid);

  unsigned long start = millis();

  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    setupMode = false;

    Serial.print("[WLAN] verbunden. IP: ");
    Serial.println(WiFi.localIP());

    showWifiOk();
    delay(1200);

    if (MDNS.begin(DEVICE_HOSTNAME)) {
      MDNS.addService("http", "tcp", 80);
      Serial.println("[mDNS] Hostname registriert: http://" + String(DEVICE_HOSTNAME) + ".local");
    } else {
      Serial.println("[mDNS] Fehler beim Starten");
    }
  } else {
    Serial.println("[WLAN] Verbindung fehlgeschlagen");
    startSetupAccessPoint();
  }
}

// ============================================================
// HTTP Server
// ============================================================

void setupServer() {
  server.on("/", HTTP_GET, handleRoot);
  server.on("/setup", HTTP_GET, handleSetupPage);
  server.on("/save-wifi", HTTP_POST, handleSaveWifi);
  server.on("/reset-wifi", HTTP_POST, handleResetWifi);

  server.on("/status", HTTP_GET, handleStatus);
  server.on("/display", HTTP_POST, handleDisplayPost);

  server.onNotFound(handleNotFound);

  server.begin();
  Serial.println("[HTTP] Server auf Port 80 gestartet");
}

// ============================================================
// Setup / Loop
// ============================================================

void setup() {
  Serial.begin(115200);
  delay(1000);

  setupMatrix();
  showBoot();
  delay(1000);

  connectWifi();
  setupServer();

  if (!setupMode) {
    currentMode = "idle";
    updateDisplay();
  }
}

void loop() {
  if (setupMode) {
    dnsServer.processNextRequest();
  }

  server.handleClient();

  if (setupMode) {
    delay(5);
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    currentMode = "offline";

    if (millis() - lastScreenUpdateMs > 2000) {
      updateDisplay();
    }

    delay(10);
    return;
  }

  delay(5);
}