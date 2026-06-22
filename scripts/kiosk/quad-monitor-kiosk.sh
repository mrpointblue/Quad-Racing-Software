#!/bin/bash
set -u

LOG=/home/pi/.local/share/quad-race/monitor-kiosk.log
URL=http://127.0.0.1:8000/ui/monitor
PROFILE=/home/pi/.config/chromium-quad-kiosk

mkdir -p "$(dirname "$LOG")"
echo "$(date -Is) starting quad monitor kiosk" >> "$LOG"

export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/1000}
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}
export LANG=de_DE.UTF-8
export LANGUAGE=de_DE:de
export LC_ALL=de_DE.UTF-8

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

PROFILE="$PROFILE" /usr/bin/python3 <<'PY' >> "$LOG" 2>&1
import json
import os
from pathlib import Path

profile = Path(os.environ["PROFILE"])
default_profile = profile / "Default"
preferences_path = default_profile / "Preferences"

default_profile.mkdir(parents=True, exist_ok=True)

try:
    preferences = json.loads(preferences_path.read_text())
except (FileNotFoundError, json.JSONDecodeError):
    preferences = {}

preferences.setdefault("intl", {})
preferences["intl"]["accept_languages"] = "de-DE,de"
preferences["intl"]["selected_languages"] = "de-DE,de"

preferences.setdefault("translate", {})
preferences["translate"]["enabled"] = False

preferences["translate_site_blacklist"] = ["127.0.0.1", "localhost", "race.local"]
preferences["translate_site_blocklist_with_time"] = {
    "127.0.0.1": 99999999999999999,
    "localhost": 99999999999999999,
    "race.local": 99999999999999999,
}

preferences_path.write_text(
    json.dumps(preferences, ensure_ascii=True, separators=(",", ":")),
)
print("Chromium kiosk preferences updated: translate disabled, languages de-DE,de")
PY

exec /usr/bin/chromium \
    --user-data-dir="$PROFILE" \
    --ozone-platform=wayland \
    --kiosk \
    --start-maximized \
    --no-first-run \
    --lang=de-DE \
    --accept-lang=de-DE,de \
    --disable-search-engine-choice-screen \
    --password-store=basic \
    --noerrdialogs \
    --disable-infobars \
    --disable-translate \
    --disable-features=Translate,TranslateUI \
    --disable-session-crashed-bubble \
    "$URL" >> "$LOG" 2>&1
