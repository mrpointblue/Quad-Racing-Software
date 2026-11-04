import shutil
import copy
import os
import socket
import sqlite3
import subprocess
import threading
import time
import base64

from datetime import datetime, date
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Depends, File, HTTPException, Request, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import Base, DATABASE_FILE_PATH, DATA_DIR, SessionLocal, engine, get_db
from backend.marshal_urls import build_marshal_station_url, normalize_marshal_url
from backend.template_helpers import register_template_helpers
from backend.time_utils import (
    format_time_input_code,
    format_time_ms as format_centiseconds_time_ms,
    normalize_time_to_centiseconds_ms,
    parse_time_input_to_ms,
)
from backend.version import APP_VERSION
from backend.models import (
    Event,
    SystemSetting,
    DriverProfile,
    Participant,
    Pairing,
    RaceRun,
    RunResult,
    RegistrationGroup,
    RegistrationNotification,
    Registration,
    RegistrationAccessToken,
    RegistrationProtocol,
    RegistrationSignature,
    MonitorDisplaySetting,
    Team,
    TeamMember,
    RunPenalty,
)
from backend.schemas import ParticipantCreate, ParticipantResponse, PairingResponse
from backend.pairing_logic import create_random_preliminary_pairings
from backend.round_logic import (
    ROUND_PRELIMINARY,
    ROUND_SEMIFINAL,
    ROUND_FINAL,
    is_round_complete,
    round_exists,
    create_semifinal_pairings,
    create_final_pairings,
    has_unresolved_ranking_ties,
)
from backend.results_logic import (
    build_class_results,
    build_compact_round_results,
    get_participant_status,
)
from backend.export_logic import build_class_export_rows, build_overall_rows
from backend.statistics_logic import build_result_statistics
from backend.services.registration_profiles import save_registration_driver_profile
from backend.services.logo_client import build_logo_client
from backend.services.system_time import parse_system_time_input, set_system_time
from backend.services.updates import install_update_guard, updater_request, safety_reason


from backend.constants import EVENT_YEAR
from backend.constants import CLUB_LOGO_UPLOAD_DIR
from backend.constants import CLUB_LOGO_ALLOWED_EXTENSIONS
from backend.constants import CLUB_LOGO_MAX_SIZE_BYTES
from backend.constants import DATABASE_BACKUP_DIR
from backend.constants import DATABASE_IMPORT_DIR
from backend.constants import DATABASE_IMPORT_ALLOWED_EXTENSIONS
from backend.constants import DATABASE_IMPORT_MAX_SIZE_BYTES
from backend.constants import REQUIRED_DATABASE_TABLES


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


from backend.constants import REGISTRATION_GROUPS


from backend.constants import ROUND_ORDER

from backend.constants import CLASS_ROUND_CONFIGS

from backend.constants import POINTS_TABLE_C


# ------------------------------------------------------------
# Allgemeine Hilfsfunktionen
# ------------------------------------------------------------

from backend.common import now_string


from backend.common import parse_event_date


from backend.common import get_event_year


from backend.common import is_event_date_reached


from backend.common import get_event_date_block_message


from backend.common import determine_race_class


from backend.common import get_active_event


from backend.common import require_active_event


from backend.common import require_event


from backend.common import ensure_event_is_open


from backend.common import ensure_event_is_active


from backend.common import redirect_to_active_event_path


from backend.application.registration_service import create_registration_notification


from backend.routers.notifications import acknowledge_registration_notification


from backend.routers.notifications import api_pending_registration_notifications


# ------------------------------------------------------------
# Fahrerstamm / Registrierung
# ------------------------------------------------------------

from backend.application.registration_service import find_or_create_driver_profile


from backend.common import clean_optional_form_value


from backend.common import parse_form_bool


from backend.common import get_youth_card_checked_at


from backend.application.registration_service import get_group_key_for_class


from backend.application.registration_service import ensure_registration_groups


from backend.application.registration_service import normalize_registration_deadline


from backend.application.registration_service import get_default_registration_deadlines_for_event_date


from backend.application.registration_service import get_registration_deadlines_for_event


from backend.application.registration_service import update_registration_group_deadlines


from backend.application.registration_service import get_registration_group_for_class


from backend.application.registration_service import is_race_class_released


from backend.application.registration_service import parse_registration_group_classes


from backend.application.registration_service import is_registration_group_race_started


from backend.application.registration_service import registration_matches_participant


from backend.application.registration_service import delete_linked_registrations_for_participant_before_start_release


from backend.application.registration_service import delete_registration_relations


from backend.application.registration_service import delete_registrations_for_event


from backend.application.registration_service import is_registration_deletable


from backend.application.teams import get_points_table_c


from backend.application.teams import sort_place_or_last


from backend.application.teams import build_participant_points


from backend.application.teams import build_team_standings


# ------------------------------------------------------------
# Zeit / Lauf-Helfer
# ------------------------------------------------------------

from backend.application.runs import format_time_ms


from backend.application.runs import get_penalty_for_sector


from backend.application.runs import set_penalty_for_sector


from backend.application.runs import prepare_result_for_display


from backend.application.runs import result_has_repeatable_values


from backend.application.runs import get_digital_penalty_status


from backend.application.runs import build_digital_penalty_statuses


from backend.application.runs import get_lane_results_for_run


from backend.application.runs import get_round_display


from backend.application.runs import reset_run_values_for_repeat


from backend.application.runs import repeat_scope_setting_key


from backend.application.runs import normalize_repeat_scope


from backend.application.runs import get_run_repeat_scope


from backend.application.runs import is_lane_editable_for_repeat_scope


# ------------------------------------------------------------
# Monitor-Helfer
# ------------------------------------------------------------

from backend.application.monitor_live import get_monitor_penalty_sum


from backend.application.runs import normalize_penalty_input


from backend.application.monitor_live import get_monitor_valued_time_ms_for_raw


from backend.application.monitor_live import get_monitor_valued_time_ms


from backend.application.monitor_live import monitor_driver_name


from backend.application.monitor_live import monitor_driver_display_name


from backend.application.monitor_live import monitor_result_payload


from backend.application.monitor_live import monitor_run_payload


from backend.application.monitor_live import get_monitor_order_key


from backend.application.monitor_live import get_monitor_runs


from backend.application.runs import get_run_start_order_key


from backend.application.runs import get_start_ordered_class_runs


from backend.application.runs import pick_next_unconfirmed_run


from backend.application.runs import build_run_navigation_payload


from backend.application.runs import get_class_round_config


from backend.application.runs import participant_start_label


from backend.application.monitor_live import pick_monitor_current_run


from backend.application.monitor_live import pick_monitor_next_runs


from backend.application.monitor_live import get_monitor_result_classes


from backend.application.monitor_live import get_monitor_live_result_runs


from backend.application.monitor_live import build_monitor_logo_signal_payload


from backend.application.monitor_live import build_monitor_payload


from backend.constants import MONITOR_ROUND_OPTIONS


from backend.application.monitor_settings import parse_monitor_csv


from backend.application.monitor_settings import get_or_create_monitor_display_setting


from backend.application.monitor_settings import monitor_display_setting_payload


from backend.application.monitor_output import build_monitor_result_rows_for_round


from backend.application.monitor_output import build_monitor_result_sections


from backend.application.monitor_output import build_monitor_team_rows


from backend.application.monitor_output import build_monitor_overall_sections


from backend.application.monitor_output import build_monitor_output_payload


from backend.application.monitor_qr import build_qr_data_uri


from backend.constants import CHECKIN_QR_BASE_URL


from backend.application.monitor_qr import build_checkin_url


from backend.application.monitor_qr import get_monitor_checkin_qr_payload


from backend.application.monitor_qr import get_monitor_marshal_qr_payload


from backend.application.monitor_qr import build_marshal_station_options


from backend.application.monitor_qr import get_marshal_qr_expires_at


# ------------------------------------------------------------
# Basis
# ------------------------------------------------------------

from backend.routers.events import read_root


from backend.routers.events import health_check


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------

from backend.routers.events import ui_dashboard_redirect


from backend.routers.events import ui_event_dashboard


# ------------------------------------------------------------
# Veranstaltungen
# ------------------------------------------------------------

from backend.routers.events import ui_events


from backend.routers.events import ui_help


from backend.routers.events import ui_create_event


from backend.routers.events import ui_activate_event


from backend.routers.events import ui_edit_event


from backend.routers.events import ui_update_event


from backend.routers.events import ui_close_event


from backend.routers.events import ui_delete_event


# ------------------------------------------------------------
# Teilnehmer je Veranstaltung
# ------------------------------------------------------------

from backend.routers.participants import ui_participant_list_redirect


from backend.routers.participants import ui_new_participant_redirect


from backend.routers.participants import ui_edit_participant_redirect


from backend.routers.participants import ui_event_participant_list


from backend.application.registration_service import require_registration


from backend.application.registration_service import validate_registration_email


from backend.constants import REGISTRATION_STATUS_LABELS


from backend.constants import REGISTRATION_STATUS_ORDER


from backend.routers.registration import ui_event_registration_list


from backend.routers.registration import ui_event_registration_delete


from backend.routers.registration import ui_event_registration_detail


from backend.routers.registration import ui_event_registration_approve


from backend.routers.registration import ui_event_registration_update


from backend.routers.registration import ui_event_registration_reject


from backend.routers.registration import ui_event_registration_correction_required


from backend.routers.participants import ui_event_new_participant


from backend.routers.participants import ui_event_create_participant


from backend.routers.participants import ui_create_participant_legacy


from backend.routers.participants import ui_event_edit_participant


from backend.routers.participants import ui_event_update_participant


from backend.routers.participants import ui_update_participant_legacy


from backend.routers.participants import ui_event_delete_participant


from backend.routers.participants import ui_delete_participant_legacy


from backend.routers.drivers import ui_driver_profiles_redirect


from backend.routers.drivers import ui_event_driver_profiles


from backend.routers.drivers import ui_event_help_redirect


from backend.routers.drivers import ui_event_start_approval_redirect


from backend.routers.drivers import ui_event_race_redirect


from backend.routers.drivers import ui_event_team_results_redirect


from backend.routers.drivers import ui_event_driver_database_redirect


from backend.routers.drivers import ui_event_edit_driver_profile


from backend.routers.drivers import ui_event_update_driver_profile


from backend.routers.drivers import ui_event_delete_driver_profile


from backend.routers.participants import ui_event_withdraw_participant


from backend.routers.participants import ui_withdraw_participant_legacy


from backend.routers.participants import ui_event_revoke_withdraw_participant


from backend.routers.participants import ui_revoke_withdraw_participant_legacy


# ------------------------------------------------------------
# Startfreigabe je Veranstaltung
# ------------------------------------------------------------

from backend.routers.release import ui_registration_redirect


from backend.routers.release import ui_event_registration_release


from backend.routers.release import ui_event_release_registration_group


from backend.routers.release import ui_release_registration_group_legacy


from backend.routers.release import ui_event_revoke_registration_group


from backend.routers.release import ui_revoke_registration_group_legacy


# ------------------------------------------------------------
# Mannschaftswertung
# ------------------------------------------------------------

from backend.routers.teams import ui_teams_redirect


from backend.routers.teams import ui_event_teams


from backend.routers.teams import ui_event_create_team


from backend.routers.teams import ui_event_delete_team


# ------------------------------------------------------------
# Läufe / Rennbetrieb je Veranstaltung
# ------------------------------------------------------------

from backend.routers.racing import ui_classes_redirect


from backend.routers.racing import ui_event_classes


from backend.routers.racing import ui_event_start_class


from backend.routers.racing import ui_start_class_legacy


from backend.routers.racing import ui_event_class_round_pairings_print


from backend.routers.racing import ui_event_all_pairings_print


from backend.routers.racing import ui_event_pairings_display


from backend.routers.racing import ui_pairings_display_redirect


from backend.routers.racing import ui_event_start_semifinal


from backend.routers.racing import ui_start_semifinal_legacy


from backend.routers.racing import ui_event_start_final


from backend.routers.racing import ui_start_final_legacy


# ------------------------------------------------------------
# Monitor
# ------------------------------------------------------------

from backend.routers.monitor import ui_monitor_output_redirect


from backend.routers.monitor import ui_output_monitor_redirect


from backend.routers.monitor import ui_event_output_monitor_redirect


from backend.routers.monitor import ui_event_monitor_output


from backend.routers.monitor import ui_event_update_monitor_output


from backend.routers.monitor import ui_event_restart_monitor_browser


from backend.routers.monitor import ui_monitor_redirect


from backend.routers.monitor import ui_event_monitor


from backend.routers.monitor import api_event_monitor


# ------------------------------------------------------------
# Zeitnehmer / Laufdetails je Veranstaltung
# ------------------------------------------------------------

from backend.routers.timekeeper import ui_timekeeper_class_redirect


from backend.routers.timekeeper import ui_event_timekeeper_class


from backend.routers.timekeeper import ui_timekeeper_run_redirect


from backend.routers.timekeeper import ui_event_timekeeper_run


from backend.routers.timekeeper import ui_event_start_run


from backend.routers.timekeeper import ui_start_run_legacy


from backend.routers.timekeeper import ui_event_reset_run


from backend.application.runs import reset_active_run_hardware


from backend.routers.timekeeper import ui_event_timekeeper_reset


from backend.routers.timekeeper import ui_reset_run_legacy


from backend.routers.timekeeper import ui_event_repeat_run


from backend.routers.timekeeper import ui_repeat_run_legacy


from backend.routers.timekeeper import ui_event_set_false_start


from backend.routers.timekeeper import ui_set_false_start_legacy


from backend.routers.timekeeper import ui_event_save_run


from backend.routers.timekeeper import ui_save_run_legacy


from backend.routers.timekeeper import ui_event_confirm_run


from backend.routers.timekeeper import ui_confirm_run_legacy


# ------------------------------------------------------------
# API: Fahrerstamm Suche
# ------------------------------------------------------------

from backend.routers.api import api_search_driver_profiles


from backend.routers.api import api_event_driver_profile_registration_exists


from backend.routers.api import api_driver_profile_registration_exists_legacy


from backend.routers.api import api_event_start_number_exists


from backend.routers.api import api_start_number_exists_legacy


from backend.routers.api import api_event_registration_open_for_birth_year


from backend.routers.api import api_registration_open_for_birth_year_legacy


# ------------------------------------------------------------
# API: Teilnehmer / Paarungen Legacy
# ------------------------------------------------------------

from backend.routers.api import create_participant


from backend.routers.api import list_participants


from backend.routers.api import list_participants_by_class


from backend.routers.racing import generate_preliminary_pairings


from backend.routers.api import list_pairings_by_class


# ------------------------------------------------------------
# System / Konfiguration
# ------------------------------------------------------------

from backend.constants import LOGO_DEFAULT_SETTINGS

from backend.constants import YOUTH_CARD_DEFAULT_SETTINGS

from backend.constants import CLUB_LOGO_DEFAULT_SETTINGS

from backend.constants import STARTDISPLAY_DEFAULT_SETTINGS


from backend.application.settings_service import get_setting


from backend.application.settings_service import set_setting


from backend.application.settings_service import get_logo_settings


from backend.application.logo_runtime import parse_logo_poll_interval_ms


from backend.application.logo_runtime import build_empty_logo_status


from backend.application.logo_runtime import LogoStatusCache


from backend.application.logo_runtime import logo_status_cache


@app.on_event("startup")
def start_logo_status_cache():
    logo_status_cache.start()


@app.on_event("shutdown")
def stop_logo_status_cache():
    logo_status_cache.stop()


from backend.application.logo_runtime import get_logo_status_payload


from backend.application.logo_sync import LOGO_RUN_DB_SYNC_LOCK
from backend.application.logo_sync import LOGO_RUN_DB_SYNC_STATE
from backend.application.logo_sync import LOGO_RUN_DB_SYNC_MIN_INTERVAL_SECONDS


from backend.application.logo_sync import should_sync_logo_run_times


from backend.application.logo_sync import sync_logo_times_for_run


from backend.application.logo_runtime import pulse_logo_web_start


from backend.application.logo_runtime import pulse_logo_web_reset


from backend.application.logo_runtime import pulse_logo_web_start_background


from backend.application.logo_runtime import pulse_logo_web_reset_background


from backend.application.settings_service import get_app_settings


from backend.application.settings_service import get_club_logo_path_from_db


# Startdisplay Service Functions
from backend.application.startdisplay_dispatch import send_start_display_numbers


from backend.application.startdisplay_dispatch import send_start_display_for_run


from backend.application.files import get_club_logo_path


from backend.application.files import get_club_logo_file_path_from_db


templates.env.globals["get_club_logo_path"] = get_club_logo_path


from backend.application.settings_service import save_logo_settings


from backend.application.settings_service import build_logo_status


from backend.application.system_status import format_storage_size


from backend.application.system_status import get_system_storage_status


from backend.application.system_status import read_cpu_times


from backend.application.system_status import get_cpu_usage_percent


from backend.application.system_status import get_cpu_temperature


from backend.application.system_status import get_memory_status


from backend.application.system_status import read_text_file


from backend.application.system_status import get_ip_command


from backend.application.system_status import get_interface_ipv4_address


from backend.application.system_status import get_host_ipv4_addresses


from backend.application.system_status import get_primary_ipv4_address


from backend.application.system_status import list_network_interfaces


from backend.application.system_status import is_wireless_interface


from backend.application.system_status import get_default_route_interface


from backend.application.system_status import get_wifi_signal_strength


from backend.application.system_status import get_network_interface_status


from backend.application.system_status import get_empty_network_interface_status


from backend.application.system_status import choose_network_interface_status


from backend.application.system_status import get_network_status


from backend.application.system_status import get_system_status


from backend.application.system_actions import restart_quad_race_service_delayed


from backend.application.system_actions import reboot_system_delayed


from backend.application.system_actions import restart_monitor_browser_delayed

from backend.application.files import remove_existing_club_logos


from backend.application.files import clear_club_logo_settings


from backend.application.files import validate_club_logo_upload


from backend.application.files import validate_database_upload


from backend.application.files import validate_sqlite_database


from backend.application.files import get_database_backup_items


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


from backend.routers.settings import ui_settings


from backend.routers.settings import ui_set_system_time


from backend.routers.settings import ui_export_database


from backend.routers.settings import ui_import_database


from backend.routers.settings import ui_upload_club_logo


from backend.routers.settings import ui_delete_club_logo


@app.post("/ui/settings/logo")
def ui_save_logo_connection(
    logo_enabled: str = Form("false"),
    logo_simulation_mode: str = Form("false"),
    logo_ip: str = Form(...),
    logo_port: int = Form(...),
    logo_timeout_ms: int = Form(...),
    logo_poll_interval_ms: int = Form(200),
    logo_access_mode: str = Form("modbus_tcp"),
    logo_password: str = Form(""),
    logo_time_register_start: int = Form(0),
    logo_output_coil_start: int = Form(8192),
    logo_marker_coil_start: int = Form(8256),
    logo_web_start_coil: str = Form("8262"),
    logo_web_reset_coil: str = Form("8264"),
    logo_pulse_duration: str = Form("0.2"),
    db: Session = Depends(get_db),
):
    if logo_port < 1 or logo_port > 65535:
        return RedirectResponse(
            url="/ui/settings?error=Ungültiger Port",
            status_code=303,
        )

    if logo_timeout_ms < 100:
        logo_timeout_ms = 100

    if logo_timeout_ms > 10000:
        logo_timeout_ms = 10000

    if logo_poll_interval_ms < 500:
        logo_poll_interval_ms = 500

    if logo_poll_interval_ms > 500:
        logo_poll_interval_ms = 500

    if logo_access_mode not in ["modbus_tcp"]:
        return RedirectResponse(
            url="/ui/settings?error=Ungültige Zugriffsart",
            status_code=303,
        )

    try:
        pulse_duration_value = max(0.05, min(2.0, float(str(logo_pulse_duration).replace(",", "."))))
    except ValueError:
        pulse_duration_value = 0.2

    save_logo_settings(
        db=db,
        form_values={
            "logo_enabled": logo_enabled,
            "logo_simulation_mode": logo_simulation_mode,
            "logo_ip": logo_ip.strip(),
            "logo_port": str(logo_port),
            "logo_timeout_ms": str(logo_timeout_ms),
            "logo_poll_interval_ms": str(logo_poll_interval_ms),
            "logo_access_mode": logo_access_mode,
            "logo_password": logo_password.strip(),
            "logo_time_register_start": str(logo_time_register_start),
            "logo_output_coil_start": str(logo_output_coil_start),
            "logo_marker_coil_start": str(logo_marker_coil_start),
            "logo_web_start_coil": logo_web_start_coil.strip(),
            "logo_web_reset_coil": logo_web_reset_coil.strip(),
            "logo_pulse_duration": f"{pulse_duration_value:.2f}",
            "logo_pulse_duration_ms": str(round(pulse_duration_value * 1000)),
        },
    )

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Verbindung gespeichert",
        status_code=303,
    )


from backend.routers.settings import ui_save_youth_card_settings


from backend.routers.settings import ui_save_marshal_settings


@app.post("/ui/settings/logo-mapping")
def ui_save_logo_mapping(
    logo_start_range: int = Form(...),
    logo_start_address: int = Form(...),

    logo_reset_range: int = Form(...),
    logo_reset_address: int = Form(...),

    logo_time_lane_1_range: int = Form(...),
    logo_time_lane_1_address: int = Form(...),

    logo_time_lane_2_range: int = Form(...),
    logo_time_lane_2_address: int = Form(...),

    logo_ready_range: int = Form(...),
    logo_ready_address: int = Form(...),

    logo_go_range: int = Form(...),
    logo_go_address: int = Form(...),

    logo_attention_range: int = Form(...),
    logo_attention_address: int = Form(...),

    logo_light_barrier_1_range: int = Form(...),
    logo_light_barrier_1_address: int = Form(...),

    logo_light_barrier_2_range: int = Form(...),
    logo_light_barrier_2_address: int = Form(...),

    logo_error_range: int = Form(...),
    logo_error_address: int = Form(...),

    logo_false_start_1_range: int = Form(...),
    logo_false_start_1_address: int = Form(...),

    logo_false_start_2_range: int = Form(...),
    logo_false_start_2_address: int = Form(...),

    db: Session = Depends(get_db),
):
    save_logo_settings(
        db=db,
        form_values={
            "logo_start_range": str(logo_start_range),
            "logo_start_address": str(logo_start_address),

            "logo_reset_range": str(logo_reset_range),
            "logo_reset_address": str(logo_reset_address),

            "logo_time_lane_1_range": str(logo_time_lane_1_range),
            "logo_time_lane_1_address": str(logo_time_lane_1_address),

            "logo_time_lane_2_range": str(logo_time_lane_2_range),
            "logo_time_lane_2_address": str(logo_time_lane_2_address),

            "logo_ready_range": str(logo_ready_range),
            "logo_ready_address": str(logo_ready_address),

            "logo_go_range": str(logo_go_range),
            "logo_go_address": str(logo_go_address),

            "logo_attention_range": str(logo_attention_range),
            "logo_attention_address": str(logo_attention_address),

            "logo_light_barrier_1_range": str(logo_light_barrier_1_range),
            "logo_light_barrier_1_address": str(logo_light_barrier_1_address),

            "logo_light_barrier_2_range": str(logo_light_barrier_2_range),
            "logo_light_barrier_2_address": str(logo_light_barrier_2_address),

            "logo_error_range": str(logo_error_range),
            "logo_error_address": str(logo_error_address),

            "logo_false_start_1_range": str(logo_false_start_1_range),
            "logo_false_start_1_address": str(logo_false_start_1_address),

            "logo_false_start_2_range": str(logo_false_start_2_range),
            "logo_false_start_2_address": str(logo_false_start_2_address),
        },
    )

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Mapping gespeichert",
        status_code=303,
    )


@app.post("/ui/settings/logo/check")
def ui_logo_connection_check(
    db: Session = Depends(get_db),
):
    result = get_logo_status_payload()
    message = result.get("message", "Statusprüfung durchgeführt")
    ok = bool(result.get("ok"))

    set_setting(db, "logo_last_check_at", now_string())
    set_setting(db, "logo_last_check_ok", "true" if ok else "false")
    set_setting(db, "logo_last_error", "" if ok else message)
    db.commit()

    status_key = "success" if ok else "error"

    return RedirectResponse(
        url=f"/ui/settings?{status_key}={quote(message)}",
        status_code=303,
    )


@app.get("/api/logo/status")
def api_logo_status(
    db: Session = Depends(get_db),
):
    return get_logo_status_payload(db)


@app.get("/api/events/{event_id}/timekeeper/run/{run_id}/logo-status")
def api_timekeeper_run_logo_status(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")

    status = sync_logo_times_for_run(race_run, db)

    status["run"] = {
        "id": race_run.id,
        "status": race_run.status,
        "confirmed": race_run.confirmed,
    }

    return status


@app.post("/api/logo/test-connection")
def api_logo_test_connection(
    db: Session = Depends(get_db),
):
    return get_logo_status_payload(db)


@app.post("/api/logo/web-start")
def api_logo_web_start(
    db: Session = Depends(get_db),
):
    return pulse_logo_web_start(db)


@app.post("/api/logo/web-reset")
def api_logo_web_reset(
    db: Session = Depends(get_db),
):
    return pulse_logo_web_reset(db)


@app.post("/ui/settings/logo/web-start")
def ui_logo_web_start(
    db: Session = Depends(get_db),
):
    result = pulse_logo_web_start(db)
    status_key = "success" if result.get("ok") else "error"
    return RedirectResponse(
        url=f"/ui/settings?{status_key}={quote(result.get('message', 'WEB Start getestet'))}",
        status_code=303,
    )


@app.post("/ui/settings/logo/web-reset")
def ui_logo_web_reset(
    db: Session = Depends(get_db),
):
    result = pulse_logo_web_reset(db)
    status_key = "success" if result.get("ok") else "error"
    return RedirectResponse(
        url=f"/ui/settings?{status_key}={quote(result.get('message', 'WEB Reset getestet'))}",
        status_code=303,
    )


@app.post("/ui/settings/logo/reset-status")
def ui_logo_reset_status(
    db: Session = Depends(get_db),
):
    set_setting(db, "logo_last_check_at", "")
    set_setting(db, "logo_last_check_ok", "")
    set_setting(db, "logo_last_error", "")
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Status zurückgesetzt",
        status_code=303,
    )


@app.post("/ui/settings/startdisplay")
def ui_save_startdisplay_settings(
    startdisplay_enabled: str = Form("false"),
    startdisplay_host: str = Form(""),
    startdisplay_port: str = Form("80"),
    startdisplay_endpoint: str = Form("/display"),
    db: Session = Depends(get_db),
):
    set_setting(db, "startdisplay_enabled", startdisplay_enabled)
    set_setting(db, "startdisplay_host", startdisplay_host.strip())
    set_setting(db, "startdisplay_port", startdisplay_port.strip())
    set_setting(db, "startdisplay_endpoint", startdisplay_endpoint.strip())
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=Startdisplay-Einstellungen gespeichert",
        status_code=303,
    )


@app.post("/ui/settings/startdisplay/test")
def ui_test_startdisplay_connection(
    db: Session = Depends(get_db),
):
    try:
        import httpx
        import json
        
        enabled = get_setting(db, "startdisplay_enabled", "false")
        if enabled != "true":
            return RedirectResponse(
                url="/ui/settings?error=Startdisplay nicht aktiviert",
                status_code=303,
            )
        
        host = get_setting(db, "startdisplay_host", "").strip()
        if not host:
            return RedirectResponse(
                url="/ui/settings?error=Kein Host konfiguriert",
                status_code=303,
            )
        
        port = get_setting(db, "startdisplay_port", "80").strip()
        endpoint = get_setting(db, "startdisplay_endpoint", "/display").strip()
        
        # Build URL
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        
        url = f"http://{host}:{port}{endpoint}"
        
        # Send test payload
        test_payload = {
            "mode": "start_numbers",
            "lane_a_start_number": 23,
            "lane_b_start_number": 54
        }
        
        headers = {"Content-Type": "application/json"}
        data = json.dumps(test_payload)
        
        with httpx.Client(timeout=1.0) as client:
            response = client.post(url, data=data, headers=headers)
            
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            set_setting(db, "startdisplay_last_send", now)
            
            if response.status_code == 200:
                set_setting(db, "startdisplay_last_success", now)
                set_setting(db, "startdisplay_last_error", "")
                set_setting(db, "startdisplay_last_test_payload", "A:23 B:54")
                db.commit()
                return RedirectResponse(
                    url="/ui/settings?success=Verbindung zum Startdisplay erfolgreich",
                    status_code=303,
                )
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                set_setting(db, "startdisplay_last_error", error_msg)
                db.commit()
                return RedirectResponse(
                    url=f"/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: {error_msg}",
                    status_code=303,
                )
                
    except httpx.TimeoutException:
        return RedirectResponse(
            url="/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: Timeout",
            status_code=303,
        )
    except httpx.ConnectError:
        return RedirectResponse(
            url="/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: Verbindung fehlgeschlagen",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: {str(e)}",
            status_code=303,
        )


from backend.routers.settings import ui_restart_system_service


from backend.routers.settings import ui_reboot_system


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

from backend.export_routes import router as export_router
app.include_router(export_router)

from backend.results_routes import router as results_router
app.include_router(results_router)

from backend.marshal_routes import router as marshal_router
app.include_router(marshal_router)
