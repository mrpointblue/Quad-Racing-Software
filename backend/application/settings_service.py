from sqlalchemy.orm import Session
from backend.models import SystemSetting
from backend.common import now_string
from backend.constants import CLUB_LOGO_DEFAULT_SETTINGS, LOGO_DEFAULT_SETTINGS, STARTDISPLAY_DEFAULT_SETTINGS, YOUTH_CARD_DEFAULT_SETTINGS


def get_setting(
    db: Session,
    key: str,
    default_value: str = "",
) -> str:
    setting = db.query(SystemSetting).filter(
        SystemSetting.key == key
    ).first()

    if not setting:
        return default_value

    return setting.value or ""

def set_setting(
    db: Session,
    key: str,
    value: str | None,
):
    setting = db.query(SystemSetting).filter(
        SystemSetting.key == key
    ).first()

    if not setting:
        setting = SystemSetting(
            key=key,
            value=value or "",
            updated_at=now_string(),
        )
        db.add(setting)
    else:
        setting.value = value or ""
        setting.updated_at = now_string()

    db.flush()

def get_logo_settings(db: Session) -> dict:
    settings = {}

    for key, default_value in LOGO_DEFAULT_SETTINGS.items():
        settings[key] = get_setting(
            db=db,
            key=key,
            default_value=default_value,
        )

    if not str(settings.get("logo_web_start_coil", "")).strip():
        settings["logo_web_start_coil"] = LOGO_DEFAULT_SETTINGS["logo_web_start_coil"]

    if not str(settings.get("logo_web_reset_coil", "")).strip():
        settings["logo_web_reset_coil"] = LOGO_DEFAULT_SETTINGS["logo_web_reset_coil"]

    return settings

def get_app_settings(db: Session) -> dict:
    settings = get_logo_settings(db)

    for key, default_value in YOUTH_CARD_DEFAULT_SETTINGS.items():
        settings[key] = get_setting(
            db=db,
            key=key,
            default_value=default_value,
        )

    for key, default_value in CLUB_LOGO_DEFAULT_SETTINGS.items():
        settings[key] = get_setting(
            db=db,
            key=key,
            default_value=default_value,
        )

    for key, default_value in STARTDISPLAY_DEFAULT_SETTINGS.items():
        settings[key] = get_setting(
            db=db,
            key=key,
            default_value=default_value,
        )

    return settings

def get_club_logo_path_from_db(db: Session) -> str:
    return get_setting(
        db=db,
        key="club_logo_path",
        default_value="",
    )

def save_logo_settings(
    db: Session,
    form_values: dict,
):
    for key, value in form_values.items():
        set_setting(
            db=db,
            key=key,
            value=str(value) if value is not None else "",
        )

    db.commit()

def build_logo_status(settings: dict) -> dict:
    required_keys = [
        "logo_ip",
        "logo_port",
        "logo_timeout_ms",
        "logo_access_mode",
        "logo_time_register_start",
        "logo_output_coil_start",
        "logo_marker_coil_start",
    ]

    configuration_complete = "Ja"

    for key in required_keys:
        if not str(settings.get(key, "")).strip():
            configuration_complete = "Nein"
            break

    if settings.get("logo_enabled") != "true":
        mode = "LOGO deaktiviert"
    else:
        mode = "Modbus TCP aktiv"

    return {
        "configuration_complete": configuration_complete,
        "mode": mode,
    }
