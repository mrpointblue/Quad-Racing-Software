from backend.models import Event
from backend.models import MonitorDisplaySetting
from sqlalchemy.orm import Session
from backend.common import now_string


def parse_monitor_csv(value: str | None) -> list[str]:
    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]

def get_or_create_monitor_display_setting(
    event: Event,
    db: Session,
) -> MonitorDisplaySetting:
    setting = db.query(MonitorDisplaySetting).filter(
        MonitorDisplaySetting.event_id == event.id,
    ).first()

    if setting:
        return setting

    setting = MonitorDisplaySetting(
        event_id=event.id,
        show_race_control=True,
        display_classes="",
        display_rounds="",
        show_overall_results=False,
        show_team_results=False,
        show_logo_signal=False,
        show_statistics=False,
        show_checkin_qr=False,
        display_orientation="landscape",
        display_theme="dark",
        updated_at=now_string(),
    )
    db.add(setting)
    db.commit()
    db.refresh(setting)

    return setting

def monitor_display_setting_payload(setting: MonitorDisplaySetting) -> dict:
    return {
        "show_race_control": bool(setting.show_race_control),
        "display_classes": [
            int(item)
            for item in parse_monitor_csv(setting.display_classes)
            if item.isdigit()
        ],
        "display_rounds": parse_monitor_csv(setting.display_rounds),
        "show_overall_results": bool(setting.show_overall_results),
        "show_team_results": bool(setting.show_team_results),
        "show_logo_signal": bool(setting.show_logo_signal),
        "show_statistics": bool(setting.show_statistics),
        "show_checkin_qr": bool(setting.show_checkin_qr),
        "display_orientation": setting.display_orientation or "landscape",
        "display_theme": setting.display_theme or "dark",
    }
