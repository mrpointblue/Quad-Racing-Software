from pathlib import Path
import os
import subprocess
import time


def restart_quad_race_service_delayed():
    time.sleep(1)

    if os.environ.get("CONTAINERIZED", "").strip().lower() == "true":
        os._exit(0)

    subprocess.run(
        ["sudo", "-n", "systemctl", "restart", "quad-race"],
        check=False,
        timeout=15,
    )

def reboot_system_delayed():
    time.sleep(1)

    subprocess.run(
        ["sudo", "-n", "reboot"],
        check=False,
        timeout=15,
    )

def restart_monitor_browser_delayed():
    time.sleep(1)

    reload_file = os.environ.get("KIOSK_RELOAD_FILE", "").strip()
    if reload_file:
        reload_path = Path(reload_file)
        reload_path.parent.mkdir(parents=True, exist_ok=True)
        reload_path.touch()
        return

    kiosk_script = Path("/home/pi/.local/bin/quad-monitor-kiosk.sh")

    subprocess.run(
        ["/usr/bin/pkill", "-f", "chromium.*chromium-quad-kiosk"],
        check=False,
        timeout=5,
    )
    time.sleep(1)

    if not kiosk_script.exists():
        return

    subprocess.run(
        [
            "sudo",
            "-n",
            "systemd-run",
            "--unit=quad-monitor-kiosk-reload",
            "--collect",
            "--uid=pi",
            "--setenv=XDG_RUNTIME_DIR=/run/user/1000",
            "--setenv=WAYLAND_DISPLAY=wayland-0",
            "--setenv=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            str(kiosk_script),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
