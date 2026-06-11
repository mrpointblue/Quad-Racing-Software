#!/bin/bash
set -u

LOG=/home/pi/.local/share/quad-race/monitor-kiosk.log
URL=http://127.0.0.1:8000/ui/monitor
PROFILE=/home/pi/.config/chromium-quad-kiosk

mkdir -p "$(dirname "$LOG")"
echo "$(date -Is) starting quad monitor kiosk" >> "$LOG"

export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/1000}
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}

for i in $(seq 1 60); do
    if /usr/bin/curl -fsS "$URL" >/dev/null 2>&1; then
        echo "$(date -Is) monitor URL reachable" >> "$LOG"
        break
    fi

    echo "$(date -Is) waiting for monitor URL" >> "$LOG"
    sleep 2
done

if ! /usr/bin/pgrep -x chromium >/dev/null 2>&1; then
    rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonCookie" "$PROFILE/SingletonSocket"
    echo "$(date -Is) cleared stale Chromium profile lock" >> "$LOG"
fi

exec /usr/bin/chromium \
    --user-data-dir="$PROFILE" \
    --ozone-platform=wayland \
    --kiosk \
    --start-maximized \
    --no-first-run \
    --password-store=basic \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    "$URL" >> "$LOG" 2>&1
