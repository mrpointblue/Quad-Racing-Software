

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from backend.database import Base, engine, get_db
from backend.version import APP_VERSION
from backend.models import RaceRun
from backend.services.updates import install_update_guard, updater_request, safety_reason


from backend.constants import CLUB_LOGO_UPLOAD_DIR
from backend.constants import DATABASE_BACKUP_DIR
from backend.constants import DATABASE_IMPORT_DIR


app = FastAPI(
    title="Quad Parallel Race",
    version=APP_VERSION,
)
install_update_guard(app)

Base.metadata.create_all(bind=engine)
CLUB_LOGO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_IMPORT_DIR.mkdir(parents=True, exist_ok=True)


def ensure_schema_column(table_name: str, column_name: str, column_definition: str):
    with engine.connect() as connection:
        columns = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
        column_names = [column[1] for column in columns]

        if column_name not in column_names:
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )
            connection.commit()


def ensure_schema_index(index_sql: str):
    with engine.connect() as connection:
        connection.exec_driver_sql(index_sql)
        connection.commit()


ensure_schema_index(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_participants_event_start_number
    ON participants(event_id, start_number)
    """
)

ensure_schema_index(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_registrations_open_event_start_number
    ON registrations(event_id, start_number)
    WHERE registration_status IN ('draft', 'under_review', 'correction_required', 'approved')
    """
)


ensure_schema_column(
    table_name="monitor_display_settings",
    column_name="display_orientation",
    column_definition="VARCHAR DEFAULT 'landscape'",
)

ensure_schema_column(
    table_name="monitor_display_settings",
    column_name="display_theme",
    column_definition="VARCHAR DEFAULT 'dark'",
)

ensure_schema_column(
    table_name="monitor_display_settings",
    column_name="show_overall_results",
    column_definition="BOOLEAN DEFAULT 0",
)

ensure_schema_column(
    table_name="monitor_display_settings",
    column_name="show_logo_signal",
    column_definition="BOOLEAN DEFAULT 0",
)

ensure_schema_column(
    table_name="monitor_display_settings",
    column_name="show_statistics",
    column_definition="BOOLEAN DEFAULT 0",
)

ensure_schema_column(
    table_name="monitor_display_settings",
    column_name="show_checkin_qr",
    column_definition="BOOLEAN DEFAULT 0",
)

ensure_schema_column(
    table_name="registration_groups",
    column_name="registration_deadline",
    column_definition="VARCHAR",
)

ensure_schema_column(
    table_name="events",
    column_name="marshal_enabled",
    column_definition="BOOLEAN DEFAULT 0",
)

for column_name, column_definition in [
    ("penalty_type", "VARCHAR DEFAULT 'sector'"),
    ("sector", "INTEGER"),
    ("submitted_at", "VARCHAR"),
    ("updated_at", "VARCHAR"),
    ("created_by", "VARCHAR"),
    ("updated_by", "VARCHAR"),
]:
    ensure_schema_column(
        table_name="run_penalties",
        column_name=column_name,
        column_definition=column_definition,
    )

for table_name in ["driver_profiles", "participants"]:
    ensure_schema_column(
        table_name=table_name,
        column_name="youth_card_qr_url",
        column_definition="VARCHAR",
    )
    ensure_schema_column(
        table_name=table_name,
        column_name="youth_card_valid_until",
        column_definition="VARCHAR",
    )
    ensure_schema_column(
        table_name=table_name,
        column_name="youth_card_checked",
        column_definition="BOOLEAN DEFAULT 0",
    )
    ensure_schema_column(
        table_name=table_name,
        column_name="youth_card_checked_at",
        column_definition="VARCHAR",
    )

ensure_schema_column(
    table_name="registrations",
    column_name="legal_texts_accepted",
    column_definition="BOOLEAN DEFAULT 0",
)

ensure_schema_column(
    table_name="registrations",
    column_name="legal_texts_accepted_at",
    column_definition="VARCHAR",
)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")
from backend.template_runtime import templates


# ------------------------------------------------------------
# Allgemeine Hilfsfunktionen
# ------------------------------------------------------------


# ------------------------------------------------------------
# Fahrerstamm / Registrierung
# ------------------------------------------------------------


# ------------------------------------------------------------
# Zeit / Lauf-Helfer
# ------------------------------------------------------------


# ------------------------------------------------------------
# Monitor-Helfer
# ------------------------------------------------------------


# ------------------------------------------------------------
# Basis
# ------------------------------------------------------------


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------


# ------------------------------------------------------------
# Veranstaltungen
# ------------------------------------------------------------


# ------------------------------------------------------------
# Teilnehmer je Veranstaltung
# ------------------------------------------------------------


# ------------------------------------------------------------
# Startfreigabe je Veranstaltung
# ------------------------------------------------------------


# ------------------------------------------------------------
# Mannschaftswertung
# ------------------------------------------------------------


# ------------------------------------------------------------
# Läufe / Rennbetrieb je Veranstaltung
# ------------------------------------------------------------


# ------------------------------------------------------------
# Monitor
# ------------------------------------------------------------


# ------------------------------------------------------------
# Zeitnehmer / Laufdetails je Veranstaltung
# ------------------------------------------------------------


# ------------------------------------------------------------
# API: Fahrerstamm Suche
# ------------------------------------------------------------


# ------------------------------------------------------------
# API: Teilnehmer / Paarungen Legacy
# ------------------------------------------------------------


# ------------------------------------------------------------
# System / Konfiguration
# ------------------------------------------------------------


from backend.application.settings_service import get_logo_settings


from backend.application.logo_runtime import logo_status_cache


@app.on_event("startup")
def start_logo_status_cache():
    logo_status_cache.start()


@app.on_event("shutdown")
def stop_logo_status_cache():
    logo_status_cache.stop()


from backend.application.logo_runtime import get_logo_status_payload


# Startdisplay Service Functions


from backend.application.files import get_club_logo_path


templates.env.globals["get_club_logo_path"] = get_club_logo_path


@app.get("/api/system/update/safety")
def update_safety(db: Session = Depends(get_db)):
    running = db.query(RaceRun).filter(
        RaceRun.confirmed.is_(False), RaceRun.status.in_(["running", "started"])
    ).first() is not None
    logo = get_logo_status_payload()
    age = logo.get("cache_age_ms", 6000) / 1000
    reason = safety_reason(running, get_logo_settings(db).get("logo_enabled") == "true", logo, age)
    return {"protocol": "1", "safe": not bool(reason), "reason": reason}


@app.get("/api/system/update")
def update_status(db: Session = Depends(get_db)):
    state = updater_request("status")
    state["safety"] = update_safety(db)
    return state


@app.post("/api/system/update/{action}")
def update_action(action: str, request: Request, db: Session = Depends(get_db)):
    # No PIN by design. Block cross-site browser requests; no CORS enabled here.
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Update-Aktion nur aus dieser Oberfläche erlaubt.")
    if request.headers.get("x-quad-update") != "1" or action not in {"check", "install"}:
        raise HTTPException(400, "Ungültige Update-Aktion.")
    if action == "install":
        safety = update_safety(db)
        if not safety["safe"]:
            raise HTTPException(409, safety["reason"])
    return updater_request(action)


# ------------------------------------------------------------
# Zusatz-Router
# ------------------------------------------------------------

from backend.routers.notifications import router as r_notifications_router
app.include_router(r_notifications_router)

from backend.routers.events import router as r_events_router
app.include_router(r_events_router)

from backend.routers.drivers import router as r_drivers_router
app.include_router(r_drivers_router)

from backend.routers.participants import router as r_participants_router
app.include_router(r_participants_router)

from backend.routers.registration import router as r_registration_router
app.include_router(r_registration_router)

from backend.routers.release import router as r_release_router
app.include_router(r_release_router)

from backend.routers.teams import router as r_teams_router
app.include_router(r_teams_router)

from backend.routers.racing import router as r_racing_router
app.include_router(r_racing_router)

from backend.routers.api import router as r_api_router
app.include_router(r_api_router)

from backend.routers.monitor import router as r_monitor_router
app.include_router(r_monitor_router)

from backend.routers.timekeeper import router as r_timekeeper_router
app.include_router(r_timekeeper_router)

from backend.routers.settings import router as r_settings_router
app.include_router(r_settings_router)

from backend.routers.startdisplay import router as r_startdisplay_router
app.include_router(r_startdisplay_router)

from backend.routers.logo import router as r_logo_router
app.include_router(r_logo_router)

from backend.export_routes import router as export_router
app.include_router(export_router)

from backend.results_routes import router as results_router
app.include_router(results_router)

from backend.marshal_routes import router as marshal_router
app.include_router(marshal_router)
