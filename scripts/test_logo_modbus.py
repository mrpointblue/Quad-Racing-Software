import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.database import SessionLocal
from backend.logo_modbus import read_logo_status
from backend.models import SystemSetting


DEFAULT_SETTINGS = {
    "logo_enabled": "true",
    "logo_ip": "192.168.2.20",
    "logo_port": "502",
    "logo_timeout_ms": "1000",
    "logo_time_register_start": "0",
    "logo_output_coil_start": "8192",
    "logo_marker_coil_start": "8256",
    "logo_light_barrier_1_address": "0",
    "logo_light_barrier_2_address": "1",
    "logo_web_start_coil": "8262",
    "logo_web_reset_coil": "8264",
    "logo_pulse_duration_ms": "200",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Liest den aktuellen Siemens-LOGO!-Status über Modbus TCP."
    )
    parser.add_argument("--host", help="LOGO!-IP überschreiben")
    parser.add_argument("--port", type=int, help="Modbus-TCP-Port überschreiben")
    parser.add_argument("--timeout-ms", type=int, help="Timeout überschreiben")
    parser.add_argument(
        "--respect-disabled",
        action="store_true",
        help="Eine in der Datenbank deaktivierte LOGO! nicht für den Test aktivieren.",
    )
    return parser.parse_args()


def load_settings() -> dict[str, str]:
    settings = DEFAULT_SETTINGS.copy()
    db = SessionLocal()

    try:
        rows = db.query(SystemSetting).filter(
            SystemSetting.key.in_(settings.keys())
        ).all()

        for row in rows:
            settings[row.key] = row.value or ""
    finally:
        db.close()

    return settings


def main() -> int:
    args = parse_args()
    settings = load_settings()

    if not args.respect_disabled:
        settings["logo_enabled"] = "true"
    if args.host:
        settings["logo_ip"] = args.host
    if args.port is not None:
        settings["logo_port"] = str(args.port)
    if args.timeout_ms is not None:
        settings["logo_timeout_ms"] = str(args.timeout_ms)

    result = read_logo_status(settings)

    print("LOGO! Modbus-TCP-Diagnose")
    print(f"Ziel: {settings['logo_ip']}:{settings['logo_port']}")
    print(f"Timeout: {settings['logo_timeout_ms']} ms")
    print(f"Online: {result.get('online', False)}")
    print(f"OK: {result.get('ok', False)}")
    print(f"Meldung: {result.get('message', '-')}")
    print("Status:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
