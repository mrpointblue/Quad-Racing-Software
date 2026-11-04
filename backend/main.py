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


@app.get("/ui/events/{event_id}/registrations")
def ui_event_registration_list(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    registrations = db.query(Registration).filter(
        Registration.event_id == active_event.id,
    ).all()

    registrations = sorted(
        registrations,
        key=lambda item: (
            REGISTRATION_STATUS_ORDER.get(item.registration_status, 99),
            -item.id,
        ),
        reverse=False,
    )

    for registration in registrations:
        registration.can_be_edited = (
            not active_event.closed
            and registration.registration_status not in ["approved", "rejected"]
        )
        registration.can_be_deleted = (
            not active_event.closed
            and is_registration_deletable(registration, db)
        )

    open_registrations = [
        registration
        for registration in registrations
        if registration.registration_status != "approved"
    ]
    approved_registrations = [
        registration
        for registration in registrations
        if registration.registration_status == "approved"
    ]

    return templates.TemplateResponse(
        request=request,
        name="registration_list.html",
        context={
            "active_event": active_event,
            "registrations": registrations,
            "open_registrations": open_registrations,
            "approved_registrations": approved_registrations,
            "status_labels": REGISTRATION_STATUS_LABELS,
            "event_year": get_event_year(active_event),
        },
    )


@app.post("/ui/events/{event_id}/registrations/{registration_id}/delete")
def ui_event_registration_delete(
    event_id: int,
    registration_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)
    registration = require_registration(event_id, registration_id, db)

    if not is_registration_deletable(registration, db):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/registrations",
            status_code=303,
        )

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.start_number == registration.start_number,
    ).all()

    for participant in participants:
        if not registration_matches_participant(registration, participant):
            continue

        db.query(TeamMember).filter(
            TeamMember.event_id == active_event.id,
            TeamMember.participant_id == participant.id,
        ).delete(synchronize_session=False)
        db.delete(participant)

    delete_registration_relations(db, registration)
    db.delete(registration)
    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/registrations",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/registrations/{registration_id}")
def ui_event_registration_detail(
    event_id: int,
    registration_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    registration = require_registration(event_id, registration_id, db)

    return templates.TemplateResponse(
        request=request,
        name="registration_detail.html",
        context={
            "active_event": active_event,
            "registration": registration,
            "event_year": get_event_year(active_event),
        },
    )


@app.post("/ui/events/{event_id}/registrations/{registration_id}/approve")
def ui_event_registration_approve(
    event_id: int,
    registration_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    registration = require_registration(event_id, registration_id, db)

    existing_participant = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.start_number == registration.start_number,
    ).first()

    if existing_participant:
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Die gewählte Startnummer ist bereits durch einen Teilnehmer belegt.",
            },
        )

    participant = Participant(
        event_id=active_event.id,
        driver_profile_id=registration.driver_profile_id,
        start_number=registration.start_number,
        first_name=registration.first_name,
        last_name=registration.last_name,
        birth_year=registration.birth_year,
        race_class=registration.class_id,
        club=registration.club,
        team=None,
        adac_number=registration.adac_member_number,
        youth_license_number=registration.youth_pass_number,
        youth_card_qr_url=None,
        youth_card_valid_until=None,
        youth_card_checked=False,
        active=True,
    )
    db.add(participant)

    registration.registration_status = "approved"
    registration.confirmed_at = now_string()
    registration.confirmed_by = "Nennstelle"
    registration.updated_at = now_string()

    try:
        profile = save_registration_driver_profile(db, registration)
        participant.driver_profile_id = profile.id
        db.commit()
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Die gewählte Startnummer ist inzwischen belegt.",
            },
            status_code=400,
        )

    return RedirectResponse(
        url=f"/ui/events/{event_id}/registrations/{registration_id}",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/registrations/{registration_id}/update")
def ui_event_registration_update(
    event_id: int,
    registration_id: int,
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_date: str = Form(...),
    club: str = Form(...),
    start_number: int = Form(...),
    adac_member_number: str = Form(""),
    email: str = Form(""),
    youth_pass_number: str = Form(""),
    digital_youth_pass_available: str = Form("false"),
    send_protocol_email: str = Form("false"),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    registration = require_registration(event_id, registration_id, db)

    if start_number < 1 or start_number > 99:
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Die Startnummer muss zwischen 1 und 99 liegen.",
            },
        )

    existing_participant = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.start_number == start_number,
    ).first()

    if existing_participant:
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Die gewählte Startnummer ist bereits durch einen Teilnehmer belegt.",
            },
        )

    existing_registration = db.query(Registration).filter(
        Registration.event_id == active_event.id,
        Registration.id != registration.id,
        Registration.start_number == start_number,
        Registration.registration_status.in_([
            "draft",
            "submitted",
            "under_review",
            "correction_required",
            "approved",
        ]),
    ).first()

    if existing_registration:
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Die gewählte Startnummer ist bereits durch eine andere Nennung reserviert.",
            },
        )

    try:
        parsed_birth_date = datetime.strptime(birth_date, "%Y-%m-%d").date()
        race_class = determine_race_class(
            birth_year=parsed_birth_date.year,
            event_year=get_event_year(active_event),
        )
    except ValueError as error:
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": str(error),
            },
        )

    clean_first_name = first_name.strip()
    clean_last_name = last_name.strip()
    clean_club = clean_optional_form_value(club)
    clean_adac_member_number = clean_optional_form_value(adac_member_number)
    clean_email = clean_optional_form_value(email)
    clean_youth_pass_number = clean_optional_form_value(youth_pass_number)
    clean_digital_youth_pass_available = parse_form_bool(digital_youth_pass_available)
    clean_send_protocol_email = parse_form_bool(send_protocol_email)

    if not clean_first_name or not clean_last_name:
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Vorname und Nachname sind Pflichtfelder.",
            },
        )

    if not clean_club:
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Verein ist ein Pflichtfeld.",
            },
        )

    if not validate_registration_email(clean_send_protocol_email, clean_email):
        return templates.TemplateResponse(
            request=request,
            name="registration_detail.html",
            context={
                "active_event": active_event,
                "registration": registration,
                "event_year": get_event_year(active_event),
                "error": "Bitte eine gültige E-Mail-Adresse eintragen oder Protokoll per E-Mail deaktivieren.",
            },
        )

    registration.first_name = clean_first_name
    registration.last_name = clean_last_name
    registration.birth_date = birth_date
    registration.birth_year = parsed_birth_date.year
    registration.age_at_event = get_event_year(active_event) - parsed_birth_date.year
    registration.class_id = race_class
    registration.club = clean_club
    registration.start_number = start_number
    registration.adac_member_number = clean_adac_member_number
    registration.email = clean_email
    registration.youth_pass_number = clean_youth_pass_number
    registration.digital_youth_pass_available = clean_digital_youth_pass_available
    registration.send_protocol_email = clean_send_protocol_email
    registration.mail_status = "pending" if clean_send_protocol_email else "not_requested"
    registration.registration_status = "under_review"
    registration.updated_at = now_string()

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{event_id}/registrations/{registration_id}",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/registrations/{registration_id}/reject")
def ui_event_registration_reject(
    event_id: int,
    registration_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    registration = require_registration(event_id, registration_id, db)

    registration.registration_status = "rejected"
    registration.updated_at = now_string()
    registration.confirmed_at = now_string()
    registration.confirmed_by = "Nennstelle"
    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{event_id}/registrations/{registration_id}",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/registrations/{registration_id}/correction-required")
def ui_event_registration_correction_required(
    event_id: int,
    registration_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    registration = require_registration(event_id, registration_id, db)

    registration.registration_status = "correction_required"
    registration.updated_at = now_string()
    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{event_id}/registrations/{registration_id}?edit=1",
        status_code=303,
    )


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

@app.get("/ui/registration")
def ui_registration_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/registration")


@app.get("/ui/events/{event_id}/registration")
def ui_event_registration_release(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    registration_groups = ensure_registration_groups(db, active_event.id)
    registration_group_status = {
        group.group_key: {
            "race_started": is_registration_group_race_started(group, db),
        }
        for group in registration_groups
    }

    return templates.TemplateResponse(
        request=request,
        name="registration_release.html",
        context={
            "active_event": active_event,
            "registration_groups": registration_groups,
            "registration_group_status": registration_group_status,
            "event_year": get_event_year(active_event),
        },
    )


@app.post("/ui/events/{event_id}/registration/{group_key}/release")
def ui_event_release_registration_group(
    event_id: int,
    group_key: str,
    released_by: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    ensure_registration_groups(db, active_event.id)

    group = db.query(RegistrationGroup).filter(
        RegistrationGroup.event_id == active_event.id,
        RegistrationGroup.group_key == group_key,
    ).first()

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Startfreigabe-Gruppe nicht gefunden",
        )

    group.released = True
    group.released_at = now_string()
    group.released_by = released_by.strip()

    group.release_revoked = False
    group.release_revoked_at = None
    group.release_revoked_by = None
    group.release_revoked_reason = None

    create_registration_notification(
        db=db,
        event=active_event,
        group=group,
    )

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/registration",
        status_code=303,
    )


@app.post("/ui/registration/{group_key}/release")
def ui_release_registration_group_legacy(
    group_key: str,
    released_by: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_release_registration_group(
        event_id=active_event.id,
        group_key=group_key,
        released_by=released_by,
        db=db,
    )


@app.post("/ui/events/{event_id}/registration/{group_key}/revoke")
def ui_event_revoke_registration_group(
    event_id: int,
    group_key: str,
    revoked_by: str = Form(...),
    revoked_reason: str = Form(""),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    group = db.query(RegistrationGroup).filter(
        RegistrationGroup.event_id == active_event.id,
        RegistrationGroup.group_key == group_key,
    ).first()

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Startfreigabe-Gruppe nicht gefunden",
        )

    if is_registration_group_race_started(group, db):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/registration",
            status_code=303,
        )

    group.released = False
    group.release_revoked = True
    group.release_revoked_at = now_string()
    group.release_revoked_by = revoked_by.strip()
    group.release_revoked_reason = revoked_reason.strip() or None

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/registration",
        status_code=303,
    )


@app.post("/ui/registration/{group_key}/revoke")
def ui_revoke_registration_group_legacy(
    group_key: str,
    revoked_by: str = Form(...),
    revoked_reason: str = Form(""),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_revoke_registration_group(
        event_id=active_event.id,
        group_key=group_key,
        revoked_by=revoked_by,
        revoked_reason=revoked_reason,
        db=db,
    )


# ------------------------------------------------------------
# Mannschaftswertung
# ------------------------------------------------------------

@app.get("/ui/teams")
def ui_teams_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/teams")


@app.get("/ui/events/{event_id}/teams")
def ui_event_teams(
    event_id: int,
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.active == True,
    ).order_by(
        Participant.race_class,
        Participant.start_number,
    ).all()

    assigned_participant_ids = {
        member.participant_id
        for member in db.query(TeamMember).filter(
            TeamMember.event_id == active_event.id,
        ).all()
    }

    available_participants = [
        participant for participant in participants
        if participant.id not in assigned_participant_ids
    ]

    teams = db.query(Team).filter(
        Team.event_id == active_event.id,
    ).order_by(
        Team.name,
    ).all()

    standings = build_team_standings(
        event=active_event,
        teams=teams,
        db=db,
    )

    return templates.TemplateResponse(
        request=request,
        name="team_results.html",
        context={
            "active_event": active_event,
            "participants": participants,
            "available_participants": available_participants,
            "teams": teams,
            "standings": standings,
            "error": error,
            "event_year": get_event_year(active_event),
        },
    )


@app.post("/ui/events/{event_id}/teams/new")
async def ui_event_create_team(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)

    form = await request.form()
    team_name = str(form.get("team_name", "")).strip()
    participant_ids = []

    for raw_participant_id in form.getlist("participant_ids"):
        if not raw_participant_id:
            continue

        participant_id = int(raw_participant_id)

        if participant_id not in participant_ids:
            participant_ids.append(participant_id)

    if not team_name:
        return ui_event_teams(
            event_id=event_id,
            request=request,
            error="Bitte einen Teamnamen eingeben.",
            db=db,
        )

    if len(participant_ids) == 0:
        return ui_event_teams(
            event_id=event_id,
            request=request,
            error="Bitte mindestens einen Fahrer auswählen.",
            db=db,
        )

    if len(participant_ids) > 5:
        return ui_event_teams(
            event_id=event_id,
            request=request,
            error="Maximal 5 Fahrer pro Team möglich.",
            db=db,
        )

    existing_team = db.query(Team).filter(
        Team.event_id == active_event.id,
        Team.name == team_name,
    ).first()

    if existing_team:
        return ui_event_teams(
            event_id=event_id,
            request=request,
            error="Ein Team mit diesem Namen existiert bereits.",
            db=db,
        )

    participants_count = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.id.in_(participant_ids),
        Participant.active == True,
    ).count()

    if participants_count != len(participant_ids):
        return ui_event_teams(
            event_id=event_id,
            request=request,
            error="Mindestens ein ausgewählter Fahrer wurde nicht gefunden.",
            db=db,
        )

    assigned_count = db.query(TeamMember).filter(
        TeamMember.event_id == active_event.id,
        TeamMember.participant_id.in_(participant_ids),
    ).count()

    if assigned_count > 0:
        return ui_event_teams(
            event_id=event_id,
            request=request,
            error="Bereits zugeteilte Fahrer können nicht in anderen Teams teilnehmen.",
            db=db,
        )

    team = Team(
        event_id=active_event.id,
        name=team_name,
        created_at=now_string(),
    )
    db.add(team)
    db.flush()

    for participant_id in participant_ids:
        db.add(
            TeamMember(
                event_id=active_event.id,
                team_id=team.id,
                participant_id=participant_id,
            )
        )

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/teams",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/teams/{team_id}/delete")
def ui_event_delete_team(
    event_id: int,
    team_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)

    team = db.query(Team).filter(
        Team.event_id == active_event.id,
        Team.id == team_id,
    ).first()

    if team:
        db.query(TeamMember).filter(
            TeamMember.event_id == active_event.id,
            TeamMember.team_id == team.id,
        ).delete()
        db.delete(team)
        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/teams",
        status_code=303,
    )


# ------------------------------------------------------------
# Läufe / Rennbetrieb je Veranstaltung
# ------------------------------------------------------------

@app.get("/ui/classes")
def ui_classes_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/classes")


@app.get("/ui/events/{event_id}/classes")
def ui_event_classes(
    event_id: int,
    request: Request,
    tie_error: str | None = None,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    ensure_registration_groups(db, active_event.id)

    class_data = []
    runs_by_class = {}
    run_sections_by_class = {}
    active_run_by_class = {}

    run_status_order = {
        "running": 1,
        "finished": 2,
        "ready": 3,
        "created": 4,
        "confirmed": 9,
    }

    for race_class in [1, 2, 3, 4, 5]:
        participants_count = db.query(Participant).filter(
            Participant.event_id == active_event.id,
            Participant.race_class == race_class,
            Participant.active == True,
        ).count()

        pairings = db.query(Pairing).filter(
            Pairing.event_id == active_event.id,
            Pairing.race_class == race_class,
        ).all()

        preliminary_pairings = [
            pairing for pairing in pairings
            if pairing.round_name in [ROUND_PRELIMINARY, "viertelfinale"]
        ]

        semifinal_pairings = [
            pairing for pairing in pairings
            if pairing.round_name == ROUND_SEMIFINAL
        ]

        final_pairings = [
            pairing for pairing in pairings
            if pairing.round_name == ROUND_FINAL
        ]

        group = get_registration_group_for_class(
            race_class,
            active_event.id,
            db,
        )

        preliminary_created = len(preliminary_pairings) > 0
        semifinal_created = len(semifinal_pairings) > 0
        final_created = len(final_pairings) > 0

        preliminary_complete = is_round_complete(
            pairings=pairings,
            round_name=ROUND_PRELIMINARY,
        ) or is_round_complete(
            pairings=pairings,
            round_name="viertelfinale",
        )

        semifinal_complete = is_round_complete(
            pairings=pairings,
            round_name=ROUND_SEMIFINAL,
        )

        final_complete = is_round_complete(
            pairings=pairings,
            round_name=ROUND_FINAL,
        )

        next_action_available = False

        if (
            not preliminary_created
            and participants_count > 0
            and group.released
            and is_event_date_reached(active_event)
            and not active_event.closed
        ):
            next_action_available = True

        if preliminary_complete and not semifinal_created and not active_event.closed:
            next_action_available = True

        if semifinal_complete and not final_created and not active_event.closed:
            next_action_available = True

        current_round_label = None
        current_round_count = 0

        if final_created:
            current_round_label = "Finale"
            current_round_count = len(final_pairings)
        elif semifinal_created:
            current_round_label = "Halbfinale"
            current_round_count = len(semifinal_pairings)
        elif preliminary_created:
            current_round_label = "Viertelfinale"
            current_round_count = len(preliminary_pairings)

        class_sort_order = 5

        if final_complete:
            class_sort_order = 9
        elif next_action_available:
            class_sort_order = 2
        elif preliminary_created or semifinal_created or final_created:
            class_sort_order = 3
        elif participants_count > 0:
            class_sort_order = 4

        class_data.append(
            {
                "race_class": race_class,
                "participants_count": participants_count,
                "preliminary_created": preliminary_created,
                "preliminary_count": len(preliminary_pairings),
                "preliminary_complete": preliminary_complete,
                "semifinal_created": semifinal_created,
                "semifinal_complete": semifinal_complete,
                "final_created": final_created,
                "final_complete": final_complete,
                "current_round_label": current_round_label,
                "current_round_count": current_round_count,
                "registration_released": group.released,
                "registration_group_name": group.name,
                "event_closed": active_event.closed,
                "next_action_available": next_action_available,
                "class_sort_order": class_sort_order,
            }
        )

        sorted_pairings = sorted(
            pairings,
            key=lambda pairing: (
                ROUND_ORDER.get(pairing.round_name, 99),
                pairing.pairing_number,
            ),
        )

        run_items = []

        for pairing in sorted_pairings:
            sorted_runs = sorted(pairing.runs, key=lambda run: run.run_number)

            for run in sorted_runs:
                lane_a, lane_b = get_lane_results_for_run(run)
                round_number, round_label = get_round_display(run.round_name)

                if run.confirmed:
                    status_order = 9
                else:
                    status_order = run_status_order.get(run.status, 5)

                run_items.append(
                    {
                        "pairing": pairing,
                        "run": run,
                        "lane_a": lane_a,
                        "lane_b": lane_b,
                        "round_number": round_number,
                        "round_label": round_label,
                        "status_order": status_order,
                    }
                )

        start_ordered_run_items = sorted(
            run_items,
            key=lambda item: (
                item["round_number"],
                item["pairing"].pairing_number,
                item["run"].run_number,
            ),
        )

        next_startable_run_id = None

        for run_item in start_ordered_run_items:
            if not run_item["run"].confirmed:
                next_startable_run_id = run_item["run"].id
                break

        for run_item in run_items:
            run = run_item["run"]
            run_item["is_next_startable"] = (
                next_startable_run_id is not None
                and run.id == next_startable_run_id
            )
            run_item["is_open_allowed"] = (
                run.confirmed
                or run_item["is_next_startable"]
            )

        run_items = sorted(
            run_items,
            key=lambda item: (
                item["status_order"],
                item["round_number"],
                item["pairing"].pairing_number,
                item["run"].run_number,
            ),
        )

        round_sections = []

        for round_config in CLASS_ROUND_CONFIGS:
            section_runs = [
                run_item for run_item in run_items
                if run_item["run"].round_name in round_config["round_names"]
            ]

            if not section_runs:
                continue

            section_runs = sorted(
                section_runs,
                key=lambda item: (
                    0 if item["is_next_startable"] else 1,
                    item["status_order"],
                    item["pairing"].pairing_number,
                    item["run"].run_number,
                ),
            )

            round_sections.append(
                {
                    "key": round_config["key"],
                    "label": round_config["label"],
                    "number": round_config["number"],
                    "runs": section_runs,
                    "complete": all(
                        run_item["run"].confirmed
                        for run_item in section_runs
                    ),
                }
            )

        active_run = None

        for run_item in run_items:
            if run_item["is_next_startable"]:
                active_run = run_item
                break

        active_run_by_class[race_class] = active_run
        runs_by_class[race_class] = run_items
        run_sections_by_class[race_class] = round_sections

    class_data = sorted(
        class_data,
        key=lambda item: (
            item["class_sort_order"],
            item["race_class"],
        ),
    )

    return templates.TemplateResponse(
        request=request,
        name="classes.html",
        context={
            "active_event": active_event,
            "class_data": class_data,
            "runs_by_class": runs_by_class,
            "run_sections_by_class": run_sections_by_class,
            "active_run_by_class": active_run_by_class,
            "event_year": get_event_year(active_event),
            "event_date_reached": is_event_date_reached(active_event),
            "tie_error": tie_error,
        },
    )


@app.post("/ui/events/{event_id}/classes/{race_class}/start")
def ui_event_start_class(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    if not is_event_date_reached(active_event):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    if not is_race_class_released(race_class, active_event.id, db):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/registration",
            status_code=303,
        )

    existing_pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
        Pairing.round_name.in_([ROUND_PRELIMINARY, "viertelfinale"]),
    ).count()

    if existing_pairings == 0:
        participants = db.query(Participant).filter(
            Participant.event_id == active_event.id,
            Participant.race_class == race_class,
            Participant.active == True,
            Participant.withdrawn == False,
        ).all()

        if len(participants) == 0:
            return RedirectResponse(
                url=f"/ui/events/{active_event.id}/classes",
                status_code=303,
            )

        pairings = create_random_preliminary_pairings(
            participants=participants,
            race_class=race_class,
            event_id=active_event.id,
        )

        for pairing in pairings:
            db.add(pairing)

        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )


@app.post("/ui/classes/{race_class}/start")
def ui_start_class_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_class(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )


@app.get("/ui/events/{event_id}/classes/{race_class}/round/{round_key}/pairings/print")
def ui_event_class_round_pairings_print(
    event_id: int,
    race_class: int,
    round_key: str,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    round_config = get_class_round_config(round_key)

    if round_config is None:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Runde",
        )

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
        Pairing.round_name.in_(round_config["round_names"]),
    ).order_by(
        Pairing.pairing_number,
    ).all()

    rows = []

    for pairing in pairings:
        for race_run in sorted(pairing.runs, key=lambda run: run.run_number):
            lane_a, lane_b = get_lane_results_for_run(race_run)

            rows.append(
                {
                    "pairing_number": pairing.pairing_number,
                    "run_number": race_run.run_number,
                    "round_number": round_config["number"],
                    "lane_a": participant_start_label(
                        lane_a.participant if lane_a else None
                    ),
                    "lane_b": participant_start_label(
                        lane_b.participant if lane_b else None
                    ),
                }
            )

    return templates.TemplateResponse(
        request=request,
        name="class_pairings_print.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "round_label": round_config["label"],
            "rows": rows,
            "event_year": get_event_year(active_event),
        },
    )


@app.get("/ui/events/{event_id}/classes/pairings/print")
def ui_event_all_pairings_print(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
    ).all()

    rows = []

    for pairing in pairings:
        round_number, round_label = get_round_display(pairing.round_name)

        for race_run in sorted(pairing.runs, key=lambda run: run.run_number):
            lane_a, lane_b = get_lane_results_for_run(race_run)

            rows.append(
                {
                    "race_class": pairing.race_class,
                    "round_number": round_number,
                    "round_label": round_label,
                    "pairing_number": pairing.pairing_number,
                    "run_number": race_run.run_number,
                    "lane_a": participant_start_label(
                        lane_a.participant if lane_a else None
                    ),
                    "lane_b": participant_start_label(
                        lane_b.participant if lane_b else None
                    ),
                }
            )

    rows.sort(
        key=lambda row: (
            row["round_number"],
            row["race_class"],
            row["pairing_number"],
            row["run_number"],
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="all_pairings_print.html",
        context={
            "active_event": active_event,
            "rows": rows,
            "event_year": get_event_year(active_event),
        },
    )


@app.get("/ui/events/{event_id}/pairings-display")
def ui_event_pairings_display(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    current_active_event = get_active_event(db)

    if current_active_event is not None and current_active_event.id != active_event.id:
        return RedirectResponse(
            url=f"/ui/events/{current_active_event.id}/pairings-display",
            status_code=303,
        )

    return templates.TemplateResponse(
        request=request,
        name="pairings_display.html",
        context={
            "active_event": active_event,
            "monitor_data": build_monitor_output_payload(active_event, db),
            "event_year": get_event_year(active_event),
        },
    )


@app.get("/ui/pairings-display")
def ui_pairings_display_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/pairings-display")


@app.post("/ui/events/{event_id}/classes/{race_class}/semifinal/start")
def ui_event_start_semifinal(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
        Participant.withdrawn == False,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    if round_exists(pairings, ROUND_SEMIFINAL):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if not (
        is_round_complete(pairings, ROUND_PRELIMINARY)
        or is_round_complete(pairings, "viertelfinale")
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if has_unresolved_ranking_ties(
        participants=participants,
        pairings=pairings,
        included_rounds=[ROUND_PRELIMINARY],
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes?tie_error=semifinal",
            status_code=303,
        )

    semifinal_pairings = create_semifinal_pairings(
        event_id=active_event.id,
        race_class=race_class,
        participants=participants,
        pairings=pairings,
    )

    for pairing in semifinal_pairings:
        db.add(pairing)

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )


@app.post("/ui/classes/{race_class}/semifinal/start")
def ui_start_semifinal_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_semifinal(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )


@app.post("/ui/events/{event_id}/classes/{race_class}/final/start")
def ui_event_start_final(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
        Participant.withdrawn == False,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    if round_exists(pairings, ROUND_FINAL):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if not is_round_complete(pairings, ROUND_SEMIFINAL):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if has_unresolved_ranking_ties(
        participants=participants,
        pairings=pairings,
        included_rounds=[ROUND_PRELIMINARY, ROUND_SEMIFINAL],
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes?tie_error=final",
            status_code=303,
        )

    final_pairings = create_final_pairings(
        event_id=active_event.id,
        race_class=race_class,
        participants=participants,
        pairings=pairings,
    )

    for pairing in final_pairings:
        db.add(pairing)

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )


@app.post("/ui/classes/{race_class}/final/start")
def ui_start_final_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_final(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )


# ------------------------------------------------------------
# Monitor
# ------------------------------------------------------------

@app.get("/ui/monitor-output")
def ui_monitor_output_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/monitor-output")


@app.get("/ui/output-monitor")
def ui_output_monitor_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/monitor-output")


@app.get("/ui/events/{event_id}/output-monitor")
def ui_event_output_monitor_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor-output",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/monitor-output")
def ui_event_monitor_output(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    setting = get_or_create_monitor_display_setting(active_event, db)
    selected_classes = [
        int(item)
        for item in parse_monitor_csv(setting.display_classes)
        if item.isdigit()
    ]
    selected_rounds = parse_monitor_csv(setting.display_rounds)

    return templates.TemplateResponse(
        request=request,
        name="monitor_output.html",
        context={
            "active_event": active_event,
            "setting": setting,
            "selected_classes": selected_classes,
            "selected_rounds": selected_rounds,
            "round_options": MONITOR_ROUND_OPTIONS,
            "monitor_data": build_monitor_output_payload(active_event, db),
            "marshal_station_options": build_marshal_station_options(active_event),
            "marshal_qr_expires_at": get_marshal_qr_expires_at(active_event, db),
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error"),
            "event_year": get_event_year(active_event),
        },
    )


@app.post("/ui/events/{event_id}/monitor-output")
async def ui_event_update_monitor_output(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    setting = get_or_create_monitor_display_setting(active_event, db)
    form = await request.form()

    selected_classes = [
        str(race_class)
        for race_class in [1, 2, 3, 4, 5]
        if form.get(f"class_{race_class}") == "1"
    ]
    selected_rounds = [
        option["key"]
        for option in MONITOR_ROUND_OPTIONS
        if form.get(f"round_{option['key']}") == "1"
    ]

    setting.show_race_control = form.get("show_race_control") == "1"
    setting.show_logo_signal = form.get("show_logo_signal") == "1"
    setting.display_classes = ",".join(selected_classes)
    setting.display_rounds = ",".join(selected_rounds)
    setting.show_overall_results = form.get("show_overall_results") == "1"
    setting.show_team_results = form.get("show_team_results") == "1"
    setting.show_statistics = form.get("show_statistics") == "1"
    setting.show_checkin_qr = form.get("show_checkin_qr") == "1"
    setting.display_orientation = (
        form.get("display_orientation")
        if form.get("display_orientation") in ("landscape", "portrait", "portrait_left")
        else "landscape"
    )
    setting.display_theme = (
        "light"
        if form.get("display_theme") == "light"
        else "dark"
    )
    setting.updated_at = now_string()

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor-output",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/monitor-output/restart-browser")
def ui_event_restart_monitor_browser(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    threading.Thread(
        target=restart_monitor_browser_delayed,
        daemon=True,
    ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor-output?success={quote('Monitor wird neu geladen.')}",
        status_code=303,
    )


@app.get("/ui/active/monitor")
@app.get("/ui/monitor")
def ui_monitor_redirect(
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return templates.TemplateResponse(
            request=request,
            name="monitor_no_event.html",
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/monitor")
def ui_event_monitor(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    current_active_event = get_active_event(db)

    if current_active_event is not None and current_active_event.id != active_event.id:
        return RedirectResponse(
            url=f"/ui/events/{current_active_event.id}/monitor",
            status_code=303,
        )

    monitor_data = build_monitor_output_payload(
        event=active_event,
        db=db,
    )

    return templates.TemplateResponse(
        request=request,
        name="monitor.html",
        context={
            "active_event": active_event,
            "monitor_data": monitor_data,
            "event_year": get_event_year(active_event),
        },
    )


@app.get("/api/events/{event_id}/monitor")
def api_event_monitor(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    current_active_event = get_active_event(db)

    if current_active_event is not None and current_active_event.id != active_event.id:
        active_event = current_active_event

    return JSONResponse(
        content=build_monitor_output_payload(
            event=active_event,
            db=db,
        ),
        headers={
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------
# Zeitnehmer / Laufdetails je Veranstaltung
# ------------------------------------------------------------

@app.get("/ui/timekeeper/class/{race_class}")
def ui_timekeeper_class_redirect(
    race_class: int,
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, f"/timekeeper/class/{race_class}")


@app.get("/ui/events/{event_id}/timekeeper/class/{race_class}")
def ui_event_timekeeper_class(
    event_id: int,
    race_class: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    if not is_race_class_released(race_class, active_event.id, db):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/registration",
            status_code=303,
        )

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).order_by(
        Pairing.round_name,
        Pairing.pairing_number,
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="timekeeper.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "pairings": pairings,
            "logo_status": get_logo_status_payload(),
            "reset_notice": request.query_params.get("reset") == "1",
            "reset_error": request.query_params.get("reset_error"),
            "event_year": get_event_year(active_event),
        },
    )


@app.get("/ui/timekeeper/run/{run_id}")
def ui_timekeeper_run_redirect(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/timekeeper/run/{run_id}")
def ui_event_timekeeper_run(
    event_id: int,
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    pairing = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.id == race_run.pairing_id,
    ).first()

    if not pairing:
        raise HTTPException(
            status_code=404,
            detail="Paarung nicht gefunden",
        )

    sector_count = active_event.sectors_per_lane or 4
    sectors = list(range(1, sector_count + 1))

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).order_by(
        RunResult.lane,
    ).all()

    marshal_enabled = bool(active_event.marshal_enabled)
    run_penalties = []

    if marshal_enabled:
        run_penalties = db.query(RunPenalty).filter(
            RunPenalty.event_id == active_event.id,
            RunPenalty.run_id == race_run.id,
        ).all()

    prepared_results = []
    repeat_scope = get_run_repeat_scope(db, race_run.id)
    has_repeatable_results = any(
        result_has_repeatable_values(result)
        for result in results
    )

    for result in results:
        prepared_result = prepare_result_for_display(result, sectors)
        prepared_result.repeat_is_fixed = not is_lane_editable_for_repeat_scope(
            result.lane,
            repeat_scope,
        )
        prepared_result.digital_penalty_statuses = []
        if marshal_enabled:
            prepared_result.digital_penalty_statuses = build_digital_penalty_statuses(
                race_run=race_run,
                result=result,
                sectors=sectors,
                run_penalties=run_penalties,
            )
        prepared_results.append(prepared_result)

    run_items = get_start_ordered_class_runs(
        event_id=active_event.id,
        race_class=race_run.race_class,
        db=db,
    )
    next_unconfirmed_item = pick_next_unconfirmed_run(run_items)
    next_unconfirmed_run = None
    next_unconfirmed_payload = None

    if next_unconfirmed_item is not None:
        next_unconfirmed_run, next_unconfirmed_pairing = next_unconfirmed_item
        next_unconfirmed_payload = build_run_navigation_payload(
            race_run=next_unconfirmed_run,
            pairing=next_unconfirmed_pairing,
        )

    is_next_startable_run = (
        next_unconfirmed_run is not None
        and next_unconfirmed_run.id == race_run.id
    )

    if is_next_startable_run and not race_run.confirmed:
        set_setting(db, "startdisplay_active_event_id", str(active_event.id))
        set_setting(db, "startdisplay_active_run_id", str(race_run.id))
        set_setting(db, "startdisplay_active_set_at", now_string())
        db.commit()

        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, race_run.id),
            daemon=True,
        ).start()

    logo_settings = get_logo_settings(db)
    logo_poll_interval_ms = parse_logo_poll_interval_ms(logo_settings)
    logo_status = get_logo_status_payload()

    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={
            "active_event": active_event,
            "pairing": pairing,
            "race_run": race_run,
            "results": prepared_results,
            "sectors": sectors,
            "marshal_enabled": marshal_enabled,
            "logo_status": logo_status,
            "logo_poll_interval_ms": logo_poll_interval_ms,
            "repeat_scope": repeat_scope,
            "has_repeatable_results": has_repeatable_results,
            "next_unconfirmed_run": next_unconfirmed_payload,
            "is_next_startable_run": is_next_startable_run,
            "start_error": request.query_params.get("start_error"),
            "time_error": request.query_params.get("time_error"),
            "reset_notice": request.query_params.get("reset") == "1",
            "reset_error": request.query_params.get("reset_error"),
            "saved_notice": request.query_params.get("saved") == "1",
            "event_year": get_event_year(active_event),
        },
    )


@app.post("/ui/events/{event_id}/timekeeper/run/{run_id}/start")
def ui_event_start_run(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    next_unconfirmed_item = pick_next_unconfirmed_run(
        get_start_ordered_class_runs(
            event_id=active_event.id,
            race_class=race_run.race_class,
            db=db,
        )
    )

    if (
        next_unconfirmed_item is None
        or next_unconfirmed_item[0].id != race_run.id
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?start_error=not-next",
            status_code=303,
        )

    if not race_run.confirmed:
        race_run.status = "running"
        race_run.traffic_light_status = "green"
        race_run.started_at = now_string()

        set_setting(db, "startdisplay_active_event_id", str(active_event.id))
        set_setting(db, "startdisplay_active_run_id", str(race_run.id))
        set_setting(db, "startdisplay_active_set_at", now_string())

        db.commit()

        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, race_run.id),
            daemon=True
        ).start()

        threading.Thread(
            target=pulse_logo_web_start_background,
            daemon=True,
        ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )


@app.post("/ui/timekeeper/run/{run_id}/start")
def ui_start_run_legacy(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_run(
        event_id=active_event.id,
        run_id=run_id,
        db=db,
    )


@app.post("/ui/events/{event_id}/timekeeper/run/{run_id}/reset")
def ui_event_reset_run(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    race_run.traffic_light_status = "off"
    race_run.false_start_lane = "none"
    race_run.last_reset_at = now_string()
    race_run.last_reset_by = "Zeitmessanlage"
    race_run.reset_count = (race_run.reset_count or 0) + 1

    if race_run.status in ["running", "started"]:
        race_run.status = "ready"

    db.commit()

    # Update Startdisplay
    threading.Thread(
        target=send_start_display_for_run,
        args=(active_event.id, race_run.id),
        daemon=True
    ).start()

    reset_result = pulse_logo_web_reset(db)
    reset_query = "reset=1" if reset_result.get("ok") else f"reset_error={quote(reset_result.get('message', 'Reset fehlgeschlagen'))}"

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?{reset_query}",
        status_code=303,
    )


from backend.application.runs import reset_active_run_hardware


@app.post("/ui/events/{event_id}/timekeeper/class/{race_class}/reset")
def ui_event_timekeeper_reset(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    reset_active_run_hardware(active_event.id, db)

    reset_result = pulse_logo_web_reset(db)
    reset_query = "reset=1" if reset_result.get("ok") else f"reset_error={quote(reset_result.get('message', 'Reset fehlgeschlagen'))}"

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/class/{race_class}?{reset_query}",
        status_code=303,
    )


@app.post("/ui/timekeeper/run/{run_id}/reset")
def ui_reset_run_legacy(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_reset_run(
        event_id=active_event.id,
        run_id=run_id,
        db=db,
    )


@app.post("/ui/events/{event_id}/timekeeper/run/{run_id}/repeat")
def ui_event_repeat_run(
    event_id: int,
    run_id: int,
    repeat_scope: str = Form("both"),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).all()
    repeat_scope = normalize_repeat_scope(repeat_scope)

    reset_run_values_for_repeat(
        race_run=race_run,
        results=results,
        repeat_scope=repeat_scope,
    )
    set_setting(db, repeat_scope_setting_key(race_run.id), repeat_scope)

    set_setting(db, "startdisplay_active_event_id", str(active_event.id))
    set_setting(db, "startdisplay_active_run_id", str(race_run.id))
    set_setting(db, "startdisplay_active_set_at", now_string())

    db.commit()

    # Update Startdisplay (Resend current run)
    threading.Thread(
        target=send_start_display_for_run,
        args=(active_event.id, race_run.id),
        daemon=True
    ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )


@app.post("/ui/timekeeper/run/{run_id}/repeat")
def ui_repeat_run_legacy(
    run_id: int,
    repeat_scope: str = Form("both"),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_repeat_run(
        event_id=active_event.id,
        run_id=run_id,
        repeat_scope=repeat_scope,
        db=db,
    )


@app.post("/ui/events/{event_id}/timekeeper/run/{run_id}/false-start")
def ui_event_set_false_start(
    event_id: int,
    run_id: int,
    false_start_lane: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    valid_values = ["none", "lane_a", "lane_b", "both"]

    if false_start_lane not in valid_values:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Fehlstartwert",
        )

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    if not race_run.confirmed:
        race_run.false_start_lane = false_start_lane
        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )


@app.post("/ui/timekeeper/run/{run_id}/false-start")
def ui_set_false_start_legacy(
    run_id: int,
    false_start_lane: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_set_false_start(
        event_id=active_event.id,
        run_id=run_id,
        false_start_lane=false_start_lane,
        db=db,
    )


@app.post("/ui/events/{event_id}/timekeeper/run/{run_id}/save")
async def ui_event_save_run(
    event_id: int,
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    form = await request.form()
    can_update_times = not race_run.confirmed and not active_event.closed
    repeat_scope = get_run_repeat_scope(db, race_run.id)

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).all()

    for result in results:
        lane_editable = is_lane_editable_for_repeat_scope(result.lane, repeat_scope)

        if result.participant and result.participant.withdrawn:
            if can_update_times and lane_editable:
                result.raw_time_ms = None

        if can_update_times and lane_editable:
            raw_time_value = form.get(f"raw_time_ms_{result.id}")

            if raw_time_value is None or str(raw_time_value).strip() == "":
                result.raw_time_ms = None
            else:
                try:
                    result.raw_time_ms = parse_time_input_to_ms(raw_time_value)
                except ValueError:
                    return RedirectResponse(
                        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?time_error=invalid",
                        status_code=303,
                    )

        if not active_event.closed and lane_editable:
            for sector in range(1, 11):
                penalty_value = form.get(f"sector_{sector}_penalty_{result.id}")
                set_penalty_for_sector(
                    result,
                    sector,
                    normalize_penalty_input(penalty_value),
                )

            general_penalty_value = form.get(f"general_penalty_{result.id}")
            result.general_penalty = normalize_penalty_input(general_penalty_value)

    lane_a_result = next((r for r in results if r.lane == "lane_a"), None)
    lane_b_result = next((r for r in results if r.lane == "lane_b"), None)

    if can_update_times:
        race_run.lane_a_time_ms = lane_a_result.raw_time_ms if lane_a_result else None
        race_run.lane_b_time_ms = lane_b_result.raw_time_ms if lane_b_result else None
        race_run.status = "finished"
        race_run.traffic_light_status = "off"
        race_run.finished_at = now_string()

    db.commit()

    # Startdisplay nur bei unbestätigten Läufen aktualisieren.
    # Bestätigte Läufe dürfen die Anzeige nicht wieder überschreiben.
    if not race_run.confirmed:
        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, race_run.id),
            daemon=True
        ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?saved=1",
        status_code=303,
    )


@app.post("/ui/timekeeper/run/{run_id}/save")
async def ui_save_run_legacy(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return await ui_event_save_run(
        event_id=active_event.id,
        run_id=run_id,
        request=request,
        db=db,
    )


@app.post("/ui/events/{event_id}/timekeeper/run/{run_id}/confirm")
def ui_event_confirm_run(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).all()

    for result in results:
        result.confirmed = True

    race_run.confirmed = True
    race_run.status = "confirmed"
    race_run.traffic_light_status = "off"
    set_setting(db, repeat_scope_setting_key(race_run.id), "")

    db.commit()

    run_items = get_start_ordered_class_runs(
        active_event.id,
        race_run.race_class,
        db,
    )
    next_item = pick_next_unconfirmed_run(run_items)

    if next_item:
        next_run, _ = next_item

        set_setting(db, "startdisplay_active_event_id", str(active_event.id))
        set_setting(db, "startdisplay_active_run_id", str(next_run.id))
        set_setting(db, "startdisplay_active_set_at", now_string())
        db.commit()

        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, next_run.id),
            daemon=True
        ).start()
    else:
        set_setting(db, "startdisplay_active_event_id", "")
        set_setting(db, "startdisplay_active_run_id", "")
        set_setting(db, "startdisplay_active_set_at", "")
        db.commit()

        threading.Thread(
            target=send_start_display_numbers,
            args=(None, None),
            daemon=True
        ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )


@app.post("/ui/timekeeper/run/{run_id}/confirm")
def ui_confirm_run_legacy(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_confirm_run(
        event_id=active_event.id,
        run_id=run_id,
        db=db,
    )


# ------------------------------------------------------------
# API: Fahrerstamm Suche
# ------------------------------------------------------------

@app.get("/api/drivers/search")
def api_search_driver_profiles(
    q: str = "",
    db: Session = Depends(get_db),
):
    q = q.strip()

    query = db.query(DriverProfile).filter(
        DriverProfile.active == True
    )

    if q:
        search = f"%{q}%"
        query = query.filter(
            (DriverProfile.first_name.ilike(search))
            | (DriverProfile.last_name.ilike(search))
            | (DriverProfile.club.ilike(search))
            | (DriverProfile.adac_number.ilike(search))
            | (DriverProfile.youth_license_number.ilike(search))
        )

    drivers = query.order_by(
        DriverProfile.last_name,
        DriverProfile.first_name,
    ).limit(20).all()

    return [
        {
            "id": driver.id,
            "first_name": driver.first_name,
            "last_name": driver.last_name,
            "birth_year": driver.birth_year,
            "default_start_number": driver.default_start_number,
            "club": driver.club,
            "team": driver.team,
            "adac_number": driver.adac_number,
            "youth_license_number": driver.youth_license_number,
            "youth_card_qr_url": driver.youth_card_qr_url,
            "youth_card_valid_until": driver.youth_card_valid_until,
            "youth_card_checked": driver.youth_card_checked,
            "youth_card_checked_at": driver.youth_card_checked_at,
        }
        for driver in drivers
    ]


@app.get("/api/events/{event_id}/participants/driver-profile/{driver_profile_id}/exists")
def api_event_driver_profile_registration_exists(
    event_id: int,
    driver_profile_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    existing = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.driver_profile_id == driver_profile_id,
    ).first()

    return {
        "exists": existing is not None
    }


@app.get("/api/participants/driver-profile/{driver_profile_id}/exists")
def api_driver_profile_registration_exists_legacy(
    driver_profile_id: int,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return {
            "exists": False
        }

    return api_event_driver_profile_registration_exists(
        event_id=active_event.id,
        driver_profile_id=driver_profile_id,
        db=db,
    )


@app.get("/api/events/{event_id}/participants/start-number/{start_number}/exists")
def api_event_start_number_exists(
    event_id: int,
    start_number: int,
    exclude_id: int | None = None,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    query = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.start_number == start_number,
    )

    if exclude_id is not None:
        query = query.filter(
            Participant.id != exclude_id
        )

    existing = query.first()

    return {
        "exists": existing is not None
    }


@app.get("/api/participants/start-number/{start_number}/exists")
def api_start_number_exists_legacy(
    start_number: int,
    exclude_id: int | None = None,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return {
            "exists": False
        }

    return api_event_start_number_exists(
        event_id=active_event.id,
        start_number=start_number,
        exclude_id=exclude_id,
        db=db,
    )


@app.get("/api/events/{event_id}/participants/birth-year/{birth_year}/registration-open")
def api_event_registration_open_for_birth_year(
    event_id: int,
    birth_year: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    if active_event.closed:
        return {
            "registration_open": False,
            "message": "Die Veranstaltung ist geschlossen.",
            "race_class": None,
        }

    try:
        race_class = determine_race_class(
            birth_year=birth_year,
            event_year=get_event_year(active_event),
        )
    except ValueError as error:
        return {
            "registration_open": False,
            "message": str(error),
            "race_class": None,
        }

    registration_group = get_registration_group_for_class(
        race_class=race_class,
        event_id=active_event.id,
        db=db,
    )

    if registration_group.released:
        return {
            "registration_open": False,
            "message": "Registrierung nach Nennschluss nicht möglich.",
            "race_class": race_class,
        }

    return {
        "registration_open": True,
        "message": None,
        "race_class": race_class,
    }


@app.get("/api/participants/birth-year/{birth_year}/registration-open")
def api_registration_open_for_birth_year_legacy(
    birth_year: int,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return {
            "registration_open": False,
            "message": "Keine aktive Veranstaltung vorhanden.",
            "race_class": None,
        }

    return api_event_registration_open_for_birth_year(
        event_id=active_event.id,
        birth_year=birth_year,
        db=db,
    )


# ------------------------------------------------------------
# API: Teilnehmer / Paarungen Legacy
# ------------------------------------------------------------

@app.post("/participants", response_model=ParticipantResponse)
def create_participant(
    participant: ParticipantCreate,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)
    ensure_event_is_open(active_event)

    existing = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.start_number == participant.start_number,
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Startnummer ist bereits vergeben",
        )

    try:
        race_class = determine_race_class(
            birth_year=participant.birth_year,
            event_year=get_event_year(active_event),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    registration_group = get_registration_group_for_class(
        race_class=race_class,
        event_id=active_event.id,
        db=db,
    )

    if registration_group.released:
        raise HTTPException(
            status_code=403,
            detail="Registrierung nach Nennschluss nicht möglich.",
        )

    clean_club = participant.club if hasattr(participant, "club") else None
    clean_team = participant.team if hasattr(participant, "team") else None

    driver_profile = find_or_create_driver_profile(
        db=db,
        first_name=participant.first_name,
        last_name=participant.last_name,
        birth_year=participant.birth_year,
        start_number=participant.start_number,
        club=clean_club,
        team=clean_team,
        adac_number=None,
        youth_license_number=None,
    )

    db_participant = Participant(
        event_id=active_event.id,
        driver_profile_id=driver_profile.id,
        start_number=participant.start_number,
        first_name=participant.first_name,
        last_name=participant.last_name,
        birth_year=participant.birth_year,
        race_class=race_class,
        club=participant.club,
        team=participant.team,
        checked_in=False,
        active=True,
    )

    db.add(db_participant)
    db.commit()
    db.refresh(db_participant)

    return db_participant


@app.get("/participants", response_model=list[ParticipantResponse])
def list_participants(db: Session = Depends(get_db)):
    active_event = require_active_event(db)

    return db.query(Participant).filter(
        Participant.event_id == active_event.id
    ).order_by(
        Participant.race_class,
        Participant.start_number,
    ).all()


@app.get("/participants/class/{race_class}", response_model=list[ParticipantResponse])
def list_participants_by_class(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    return db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class
    ).order_by(
        Participant.start_number
    ).all()


@app.post("/pairings/preliminary/class/{race_class}", response_model=list[PairingResponse])
def generate_preliminary_pairings(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    ui_event_start_class(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )

    return db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
        Pairing.round_name.in_([ROUND_PRELIMINARY, "viertelfinale"]),
    ).all()


@app.get("/pairings/class/{race_class}", response_model=list[PairingResponse])
def list_pairings_by_class(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    return db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).order_by(
        Pairing.round_name,
        Pairing.pairing_number,
    ).all()


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


@app.get("/ui/settings")
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


@app.post("/ui/settings/system/time")
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


@app.get("/ui/settings/database/export")
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


@app.post("/ui/settings/database/import")
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


@app.post("/ui/settings/logo-upload")
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


@app.post("/ui/settings/logo-delete")
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


@app.post("/ui/settings/youth-card")
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


@app.post("/ui/settings/marshals")
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


@app.post("/ui/settings/system/restart")
def ui_restart_system_service():
    threading.Thread(
        target=restart_quad_race_service_delayed,
        daemon=True,
    ).start()

    return RedirectResponse(
        url="/ui/events?success=Neustart wurde ausgelöst.",
        status_code=303,
    )


@app.post("/ui/settings/system/reboot")
def ui_reboot_system():
    threading.Thread(
        target=reboot_system_delayed,
        daemon=True,
    ).start()

    return RedirectResponse(
        url="/ui/events?success=System-Neustart wurde ausgelöst.",
        status_code=303,
    )


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

from backend.export_routes import router as export_router
app.include_router(export_router)

from backend.results_routes import router as results_router
app.include_router(results_router)

from backend.marshal_routes import router as marshal_router
app.include_router(marshal_router)
