from fastapi import APIRouter
from backend.version import APP_VERSION
from backend.database import DATABASE_FILE_PATH
from fastapi import Depends
from fastapi import File
from fastapi.responses import FileResponse
from fastapi import Form
from pathlib import Path
from fastapi.responses import RedirectResponse
from fastapi import Request
from sqlalchemy.orm import Session
from backend.models import SystemSetting
from fastapi import UploadFile
from datetime import datetime
from backend.database import engine
from backend.database import get_db
from backend.services.system_time import parse_system_time_input
from urllib.parse import quote
from backend.services.system_time import set_system_time
import shutil
import threading
from backend.common import get_active_event, get_event_year, now_string, require_event
from backend.constants import CLUB_LOGO_UPLOAD_DIR, DATABASE_BACKUP_DIR, DATABASE_IMPORT_DIR
from backend.application.files import clear_club_logo_settings, get_database_backup_items, remove_existing_club_logos, validate_club_logo_upload, validate_database_upload, validate_sqlite_database
from backend.application.logo_runtime import get_logo_status_payload
from backend.application.settings_service import build_logo_status, get_app_settings, set_setting
from backend.application.system_actions import reboot_system_delayed, restart_quad_race_service_delayed
from backend.application.system_status import get_system_status
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/settings")
def ui_settings(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)
    settings = get_app_settings(db)
    logo_status = build_logo_status(settings)
    logo_hardware_status = get_logo_status_payload()
    system_status = get_system_status()

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_event": active_event,
            "settings": settings,
            "logo_status": logo_status,
            "logo_hardware_status": logo_hardware_status,
            "system_status": system_status,
            "app_version": APP_VERSION,
            "success": success,
            "error": error,
            "database_backups": get_database_backup_items(),
            "event_year": get_event_year(active_event),
        },
    )

@router.post("/ui/settings/system/time")
def ui_set_system_time(
    time_source: str = Form("manual"),
    manual_datetime: str = Form(""),
    browser_datetime: str = Form(""),
):
    raw_datetime = browser_datetime if time_source == "browser" else manual_datetime

    try:
        target_time = parse_system_time_input(raw_datetime)
    except ValueError as error:
        return RedirectResponse(
            url=f"/ui/settings?error={quote(str(error))}",
            status_code=303,
        )

    ok, message = set_system_time(target_time)

    if not ok:
        return RedirectResponse(
            url=f"/ui/settings?error={quote('Systemzeit konnte nicht gesetzt werden: ' + message)}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/settings?success={quote('Systemzeit gesetzt: ' + message)}",
        status_code=303,
    )

@router.get("/ui/settings/database/export")
def ui_export_database():
    if not DATABASE_FILE_PATH.exists():
        return RedirectResponse(
            url="/ui/settings?error=Datenbankdatei nicht gefunden",
            status_code=303,
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"quad_race_backup_{timestamp}.sqlite"

    return FileResponse(
        path=DATABASE_FILE_PATH,
        media_type="application/vnd.sqlite3",
        filename=filename,
    )

@router.post("/ui/settings/database/import")
async def ui_import_database(
    database_file: UploadFile = File(...),
):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        file_bytes = await database_file.read()
        extension = validate_database_upload(
            file=database_file,
            file_bytes=file_bytes,
        )
    except ValueError as error:
        return RedirectResponse(
            url=f"/ui/settings?error={quote(str(error))}",
            status_code=303,
        )

    DATABASE_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    import_path = DATABASE_IMPORT_DIR / f"import_{timestamp}{extension}"

    try:
        import_path.write_bytes(file_bytes)
    except OSError:
        return RedirectResponse(
            url="/ui/settings?error=Datenbank konnte nicht importiert werden.",
            status_code=303,
        )

    if not validate_sqlite_database(import_path):
        import_path.unlink(missing_ok=True)

        return RedirectResponse(
            url="/ui/settings?error=Die Datei ist keine gültige Quad-Race-Datenbank.",
            status_code=303,
        )

    backup_path = DATABASE_BACKUP_DIR / f"pre_import_backup_{timestamp}.sqlite"
    replace_path = DATABASE_FILE_PATH.with_name(f"{DATABASE_FILE_PATH.name}.importing")

    try:
        engine.dispose()

        if DATABASE_FILE_PATH.exists():
            shutil.copy2(DATABASE_FILE_PATH, backup_path)

        shutil.copy2(import_path, replace_path)
        replace_path.replace(DATABASE_FILE_PATH)
    except OSError:
        replace_path.unlink(missing_ok=True)

        return RedirectResponse(
            url="/ui/settings?error=Datenbank konnte nicht importiert werden.",
            status_code=303,
        )

    threading.Thread(
        target=restart_quad_race_service_delayed,
        daemon=True,
    ).start()

    return RedirectResponse(
        url="/ui/settings?success=Datenbank wurde importiert. Anwendung wird neu gestartet.",
        status_code=303,
    )

@router.post("/ui/settings/logo-upload")
async def ui_upload_club_logo(
    club_logo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        file_bytes = await club_logo.read()
        extension = validate_club_logo_upload(
            file=club_logo,
            file_bytes=file_bytes,
        )
    except ValueError as error:
        return RedirectResponse(
            url=f"/ui/settings?error={quote(str(error))}",
            status_code=303,
        )

    CLUB_LOGO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    remove_existing_club_logos()

    target_path = CLUB_LOGO_UPLOAD_DIR / f"club_logo{extension}"

    try:
        target_path.write_bytes(file_bytes)
    except OSError:
        return RedirectResponse(
            url="/ui/settings?error=Vereinslogo konnte nicht gespeichert werden",
            status_code=303,
        )

    set_setting(db, "club_logo_path", f"/static/uploads/club_logo{extension}")
    set_setting(db, "club_logo_original_filename", Path(club_logo.filename or "").name)
    set_setting(db, "club_logo_uploaded_at", now_string())
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=Vereinslogo gespeichert",
        status_code=303,
    )

@router.post("/ui/settings/logo-delete")
def ui_delete_club_logo(
    db: Session = Depends(get_db),
):
    remove_existing_club_logos()
    clear_club_logo_settings(db)
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=Vereinslogo entfernt",
        status_code=303,
    )

@router.post("/ui/settings/youth-card")
def ui_save_youth_card_settings(
    youth_card_scan_enabled: str = Form("false"),
    db: Session = Depends(get_db),
):
    set_setting(
        db=db,
        key="youth_card_scan_enabled",
        value="true" if youth_card_scan_enabled == "true" else "false",
    )
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=Jugendkarten-Einstellung gespeichert",
        status_code=303,
    )

@router.post("/ui/settings/marshals")
def ui_save_marshal_settings(
    event_id: int = Form(...),
    marshal_enabled: str | None = Form(None),
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    event.marshal_enabled = marshal_enabled == "1"

    if not event.marshal_enabled:
        setting = db.query(SystemSetting).filter(
            SystemSetting.key == f"marshal_qr_event_{event.id}_enabled"
        ).first()

        if setting:
            setting.value = "false"
            setting.updated_at = now_string()

    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=Streckenposten-Einstellung gespeichert",
        status_code=303,
    )

@router.post("/ui/settings/system/restart")
def ui_restart_system_service():
    threading.Thread(
        target=restart_quad_race_service_delayed,
        daemon=True,
    ).start()

    return RedirectResponse(
        url="/ui/events?success=Neustart wurde ausgelöst.",
        status_code=303,
    )

@router.post("/ui/settings/system/reboot")
def ui_reboot_system():
    threading.Thread(
        target=reboot_system_delayed,
        daemon=True,
    ).start()

    return RedirectResponse(
        url="/ui/events?success=System-Neustart wurde ausgelöst.",
        status_code=303,
    )
