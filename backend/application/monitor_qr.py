from io import BytesIO
from backend.models import Event
from backend.models import MonitorDisplaySetting
from sqlalchemy.orm import Session
from backend.models import SystemSetting
import base64
from backend.marshal_urls import build_marshal_station_url
from datetime import datetime
from backend.marshal_urls import normalize_marshal_url
from backend.common import now_string
from backend.constants import CHECKIN_QR_BASE_URL
from backend.application.settings_service import get_setting


def build_qr_data_uri(value: str) -> str | None:
    try:
        import qrcode
    except ImportError:
        return None

    image = qrcode.make(value)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

def build_checkin_url() -> str:
    return f"{CHECKIN_QR_BASE_URL}/checkin"

def get_monitor_checkin_qr_payload(
    event: Event,
    setting: MonitorDisplaySetting,
) -> dict | None:
    if not setting.show_checkin_qr:
        return None

    checkin_url = build_checkin_url()

    return {
        "label": "Check-in QR",
        "url": checkin_url,
        "qr_data_uri": build_qr_data_uri(checkin_url),
    }

def get_monitor_marshal_qr_payload(event: Event, db: Session) -> dict | None:
    enabled_setting = db.query(SystemSetting).filter(
        SystemSetting.key == f"marshal_qr_event_{event.id}_enabled"
    ).first()

    if not enabled_setting or enabled_setting.value != "true":
        return None

    expires_at_setting = db.query(SystemSetting).filter(
        SystemSetting.key == f"marshal_qr_event_{event.id}_expires_at"
    ).first()

    if expires_at_setting and expires_at_setting.value:
        try:
            expires_at = datetime.fromisoformat(expires_at_setting.value)
        except ValueError:
            expires_at = None

        if expires_at and datetime.now() >= expires_at:
            enabled_setting.value = "false"
            enabled_setting.updated_at = now_string()
            db.commit()
            return None

    mode_setting = db.query(SystemSetting).filter(
        SystemSetting.key == f"marshal_qr_event_{event.id}_mode"
    ).first()
    mode = mode_setting.value if mode_setting else "single"

    if mode == "all":
        stations = []

        for station in build_marshal_station_options(event):
            station_url = build_marshal_station_url(
                event.id,
                station["type"],
                station["lane"],
                station["sector"],
            )
            stations.append(
                {
                    "label": station["label"],
                    "url": station_url,
                    "qr_data_uri": build_qr_data_uri(station_url),
                }
            )

        return {
            "mode": "all",
            "label": "Alle Streckenposten",
            "stations": stations,
        }

    label_setting = db.query(SystemSetting).filter(
        SystemSetting.key == f"marshal_qr_event_{event.id}_label"
    ).first()
    url_setting = db.query(SystemSetting).filter(
        SystemSetting.key == f"marshal_qr_event_{event.id}_url"
    ).first()

    station_url = normalize_marshal_url(url_setting.value if url_setting else "", event.id)

    if not station_url:
        return None

    return {
        "mode": "landing" if mode == "landing" else "single",
        "label": label_setting.value if label_setting else "Streckenposten",
        "url": station_url,
        "qr_data_uri": build_qr_data_uri(station_url),
    }

def build_marshal_station_options(event: Event) -> list[dict]:
    position_count = max(1, int(event.sectors_per_lane or 1))
    stations = [
        {
            "type": "general",
            "lane": "",
            "sector": "",
            "label": "Start-/Zielposten",
        }
    ]

    for lane in ["A", "B"]:
        for sector in range(1, position_count + 1):
            stations.append(
                {
                    "type": "sector",
                    "lane": lane,
                    "sector": str(sector),
                    "label": f"Bahn {lane} / Position {sector}",
                }
            )

    return stations

def get_marshal_qr_expires_at(event: Event, db: Session) -> str:
    enabled = get_setting(
        db=db,
        key=f"marshal_qr_event_{event.id}_enabled",
        default_value="false",
    )

    if enabled != "true":
        return ""

    expires_at = get_setting(
        db=db,
        key=f"marshal_qr_event_{event.id}_expires_at",
        default_value="",
    )

    if not expires_at:
        return ""

    try:
        parsed_expires_at = datetime.fromisoformat(expires_at)
    except ValueError:
        return ""

    if datetime.now() >= parsed_expires_at:
        return ""

    return expires_at
