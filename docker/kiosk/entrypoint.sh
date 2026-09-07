#!/bin/sh
set -eu

MONITOR_URL="${MONITOR_URL:-http://127.0.0.1:8000/ui/monitor}"
RELOAD_FILE="${KIOSK_RELOAD_FILE:-/control/kiosk-reload.request}"
PROFILE_DIR="${KIOSK_PROFILE_DIR:-/tmp/chromium-quad-kiosk}"
WAYLAND_SOCKET="${XDG_RUNTIME_DIR:-/run/user/1000}/${WAYLAND_DISPLAY:-wayland-0}"
BROWSER_PID=""

stop_browser() {
    if [ -n "$BROWSER_PID" ] && kill -0 "$BROWSER_PID" 2>/dev/null; then
        kill "$BROWSER_PID" 2>/dev/null || true
        wait "$BROWSER_PID" 2>/dev/null || true
    fi
}

trap 'stop_browser; exit 0' INT TERM

echo "Quad Race Kiosk wartet auf Wayland: $WAYLAND_SOCKET"
while [ ! -S "$WAYLAND_SOCKET" ]; do
    sleep 2
done

echo "Quad Race Kiosk wartet auf Monitor: $MONITOR_URL"
until curl --fail --silent --show-error "$MONITOR_URL" >/dev/null 2>&1; do
    sleep 2
done

mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonCookie" "$PROFILE_DIR/SingletonSocket"

while true; do
    rm -f "$RELOAD_FILE"
    echo "Starte Chromium Kiosk: $MONITOR_URL"

    chromium \
        --user-data-dir="$PROFILE_DIR" \
        --ozone-platform=wayland \
        --kiosk \
        --start-maximized \
        --no-first-run \
        --lang=de-DE \
        --accept-lang=de-DE,de \
        --password-store=basic \
        --noerrdialogs \
        --disable-infobars \
        --disable-translate \
        --disable-features=Translate,TranslateUI \
        --disable-session-crashed-bubble \
        --disable-background-networking \
        --disable-dev-shm-usage \
        --no-sandbox \
        "$MONITOR_URL" &

    BROWSER_PID=$!

    while kill -0 "$BROWSER_PID" 2>/dev/null; do
        if [ -f "$RELOAD_FILE" ]; then
            echo "Kiosk-Neustart angefordert."
            stop_browser
            break
        fi
        sleep 1
    done

    wait "$BROWSER_PID" 2>/dev/null || true
    BROWSER_PID=""
    sleep 1
done
