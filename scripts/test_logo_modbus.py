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
    "logo_enabled": "false",
    "logo_simulation_mode": "true",
    "logo_ip": "169.254.180.254",
    "logo_port": "502",
    "logo_timeout_ms": "2000",
    "logo_access_mode": "logo_web",
}


def load_settings() -> dict:
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


def main():
    settings = load_settings()
    result = read_logo_status(settings)

    print("LOGO Modbus Test")
    print(f"IP: {settings.get('logo_ip', '')}")
    print(f"Port: {settings.get('logo_port', '')}")
    print(f"Access Mode: {settings.get('logo_access_mode', '')}")
    print(f"Simulation: {settings.get('logo_simulation_mode', '')}")
    print(f"Enabled: {settings.get('logo_enabled', '')}")
    print(f"OK: {result.get('ok')}")
    print(f"Meldung: {result.get('message')}")
    print("Details:")
    print(json.dumps(result.get("details", {}), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
