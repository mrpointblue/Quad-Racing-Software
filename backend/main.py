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
from backend.services.logo_client import build_logo_client


EVENT_YEAR = datetime.now().year
CLUB_LOGO_UPLOAD_DIR = Path("backend/static/uploads")
CLUB_LOGO_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
CLUB_LOGO_MAX_SIZE_BYTES = 2 * 1024 * 1024
DATABASE_BACKUP_DIR = DATA_DIR / "backups"
DATABASE_IMPORT_DIR = DATA_DIR / "imports"
DATABASE_IMPORT_ALLOWED_EXTENSIONS = {".sqlite", ".db"}
DATABASE_IMPORT_MAX_SIZE_BYTES = 100 * 1024 * 1024
REQUIRED_DATABASE_TABLES = {
    "events",
    "participants",
    "pairings",
    "race_runs",
    "run_results",
    "system_settings",
}


app = FastAPI(
    title="Quad Parallel Race",
    version=APP_VERSION,
)

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
templates = Jinja2Templates(directory="backend/templates")
register_template_helpers(templates)
templates.env.globals["format_time_input_code"] = format_time_input_code


REGISTRATION_GROUPS = [
    {
        "group_key": "class_1_2",
        "name": "Klasse 1 & 2",
        "classes": "1,2",
    },
    {
        "group_key": "class_3_4",
        "name": "Klasse 3 & 4",
        "classes": "3,4",
    },
    {
        "group_key": "class_5",
        "name": "Klasse 5",
        "classes": "5",
    },
]


ROUND_ORDER = {
    ROUND_PRELIMINARY: 1,
    "viertelfinale": 1,
    ROUND_SEMIFINAL: 2,
    ROUND_FINAL: 3,
}

CLASS_ROUND_CONFIGS = [
    {
        "key": "final",
        "round_names": [ROUND_FINAL],
        "label": "Finale",
        "number": 3,
    },
    {
        "key": "semifinal",
        "round_names": [ROUND_SEMIFINAL],
        "label": "Halbfinale",
        "number": 2,
    },
    {
        "key": "quarterfinal",
        "round_names": [ROUND_PRELIMINARY, "viertelfinale"],
        "label": "Viertelfinale",
        "number": 1,
    },
]

POINTS_TABLE_C = {
    1: 25,
    2: 22,
    3: 20,
    4: 18,
    5: 16,
    6: 15,
    7: 14,
    8: 13,
    9: 12,
    10: 11,
    11: 10,
    12: 9,
    13: 8,
    14: 7,
    15: 6,
    16: 5,
    17: 4,
    18: 3,
    19: 2,
    20: 1,
}


# ------------------------------------------------------------
# Allgemeine Hilfsfunktionen
# ------------------------------------------------------------

def now_string() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def parse_event_date(event_date: str | None) -> date | None:
    if not event_date:
        return None

    event_date = event_date.strip()

    for date_format in ["%d.%m.%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(event_date, date_format).date()
        except ValueError:
            pass

    return None


def get_event_year(event: Event | None) -> int:
    if event and event.event_date:
        parsed_date = parse_event_date(event.event_date)

        if parsed_date:
            return parsed_date.year

    return datetime.now().year


def is_event_date_reached(event: Event) -> bool:
    parsed_date = parse_event_date(event.event_date)

    if parsed_date is None:
        return True

    return date.today() >= parsed_date


def get_event_date_block_message(event: Event) -> str:
    return (
        "Rennbetrieb erst ab Veranstaltungsdatum möglich. "
        f"Eingetragenes Datum: {event.event_date or '-'}"
    )


def determine_race_class(birth_year: int, event_year: int = EVENT_YEAR) -> int:
    age = event_year - birth_year

    if age in [7, 8]:
        return 1

    if age in [9, 10, 11]:
        return 2

    if age in [12, 13, 14]:
        return 3

    if age in [15, 16, 17, 18]:
        return 4

    if age >= 19:
        return 5

    raise ValueError(
        f"Geburtsjahr {birth_year} passt im Veranstaltungsjahr {event_year} zu keiner gültigen Klasse"
    )


def get_active_event(db: Session) -> Event | None:
    return db.query(Event).filter(Event.active == True).first()


def require_active_event(db: Session) -> Event:
    event = get_active_event(db)

    if not event:
        raise HTTPException(
            status_code=400,
            detail="Keine aktive Veranstaltung vorhanden",
        )

    return event


def require_event(
    event_id: int,
    db: Session,
) -> Event:
    event = db.query(Event).filter(
        Event.id == event_id
    ).first()

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Veranstaltung nicht gefunden",
        )

    return event


def ensure_event_is_open(event: Event):
    if event.closed:
        raise HTTPException(
            status_code=403,
            detail="Die Veranstaltung ist geschlossen. Änderungen sind nicht mehr möglich.",
        )


def redirect_to_active_event_path(
    db: Session,
    path_suffix: str,
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}{path_suffix}",
        status_code=303,
    )


def create_registration_notification(
    db: Session,
    event: Event,
    group: RegistrationGroup,
):
    message = (
        f"Startfreigabe erfolgt: {group.name} wurde freigegeben. "
        "Der Rennbetrieb kann gestartet werden."
    )

    notification = RegistrationNotification(
        event_id=event.id,
        group_key=group.group_key,
        group_name=group.name,
        message=message,
        created_at=now_string(),
        acknowledged=False,
        acknowledged_at=None,
        acknowledged_by=None,
    )
    db.add(notification)


@app.post("/ui/notifications/{notification_id}/acknowledge")
def acknowledge_registration_notification(
    notification_id: int,
    request: Request,
    acknowledged_by: str = Form("Zeitnahme"),
    db: Session = Depends(get_db),
):
    notification = db.query(RegistrationNotification).filter(
        RegistrationNotification.id == notification_id,
    ).first()

    if notification:
        notification.acknowledged = True
        notification.acknowledged_at = now_string()
        notification.acknowledged_by = acknowledged_by.strip() or "Zeitnahme"
        db.commit()

    return RedirectResponse(
        url=request.headers.get("referer") or "/ui/events",
        status_code=303,
    )


@app.get("/api/events/{event_id}/notifications/pending")
def api_pending_registration_notifications(
    event_id: int,
    db: Session = Depends(get_db),
):
    notifications = db.query(RegistrationNotification).filter(
        RegistrationNotification.event_id == event_id,
        RegistrationNotification.acknowledged == False,
    ).order_by(
        RegistrationNotification.id.desc()
    ).all()

    return {
        "notifications": [
            {
                "id": notification.id,
                "group_name": notification.group_name,
                "message": notification.message,
                "created_at": notification.created_at or "",
            }
            for notification in notifications
        ]
    }


# ------------------------------------------------------------
# Fahrerstamm / Registrierung
# ------------------------------------------------------------

def find_or_create_driver_profile(
    db: Session,
    first_name: str,
    last_name: str,
    birth_year: int,
    start_number: int | None,
    club: str | None,
    team: str | None,
    adac_number: str | None,
    youth_license_number: str | None,
    youth_card_qr_url: str | None = None,
    youth_card_valid_until: str | None = None,
    youth_card_checked: bool = False,
    youth_card_checked_at: str | None = None,
) -> DriverProfile:
    first_name = first_name.strip()
    last_name = last_name.strip()

    driver_profile = db.query(DriverProfile).filter(
        DriverProfile.first_name == first_name,
        DriverProfile.last_name == last_name,
        DriverProfile.birth_year == birth_year,
        DriverProfile.active == True,
    ).first()

    if not driver_profile:
        driver_profile = DriverProfile(
            first_name=first_name,
            last_name=last_name,
            birth_year=birth_year,
            default_start_number=start_number,
            club=club,
            team=team,
            adac_number=adac_number,
            youth_license_number=youth_license_number,
            youth_card_qr_url=youth_card_qr_url,
            youth_card_valid_until=youth_card_valid_until,
            youth_card_checked=youth_card_checked,
            youth_card_checked_at=youth_card_checked_at,
            active=True,
        )
        db.add(driver_profile)
        db.flush()
        return driver_profile

    driver_profile.default_start_number = start_number
    driver_profile.club = club
    driver_profile.team = team
    driver_profile.adac_number = adac_number
    driver_profile.youth_license_number = youth_license_number
    driver_profile.youth_card_qr_url = youth_card_qr_url
    driver_profile.youth_card_valid_until = youth_card_valid_until
    driver_profile.youth_card_checked = youth_card_checked

    if youth_card_checked:
        driver_profile.youth_card_checked_at = (
            youth_card_checked_at
            or driver_profile.youth_card_checked_at
            or now_string()
        )
    else:
        driver_profile.youth_card_checked_at = youth_card_checked_at

    driver_profile.active = True

    db.flush()

    return driver_profile


def clean_optional_form_value(value: str | None) -> str | None:
    if value is None:
        return None

    return value.strip() or None


def parse_form_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in ["1", "true", "yes", "on"]


def get_youth_card_checked_at(
    checked: bool,
    existing_checked_at: str | None = None,
) -> str | None:
    if not checked:
        return None

    return existing_checked_at or now_string()


def get_group_key_for_class(race_class: int) -> str:
    if race_class in [1, 2]:
        return "class_1_2"

    if race_class in [3, 4]:
        return "class_3_4"

    if race_class == 5:
        return "class_5"

    raise ValueError("Ungültige Klasse")


def ensure_registration_groups(
    db: Session,
    event_id: int,
) -> list[RegistrationGroup]:
    for group_data in REGISTRATION_GROUPS:
        existing = db.query(RegistrationGroup).filter(
            RegistrationGroup.event_id == event_id,
            RegistrationGroup.group_key == group_data["group_key"],
        ).first()

        if not existing:
            group = RegistrationGroup(
                event_id=event_id,
                group_key=group_data["group_key"],
                name=group_data["name"],
                classes=group_data["classes"],
                released=False,
                released_at=None,
                released_by=None,
                registration_deadline=None,
                release_revoked=False,
                release_revoked_at=None,
                release_revoked_by=None,
                release_revoked_reason=None,
            )
            db.add(group)

    db.commit()

    return db.query(RegistrationGroup).filter(
        RegistrationGroup.event_id == event_id
    ).order_by(
        RegistrationGroup.id
    ).all()


def normalize_registration_deadline(value: str | None) -> str | None:
    value = (value or "").strip()

    if not value:
        return None

    parts = value.split(":")

    if len(parts) < 2:
        return None

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None

    return f"{hour:02d}:{minute:02d}"


def get_default_registration_deadlines_for_event_date(
    event_date: str | None,
) -> dict[str, str]:
    parsed_date = parse_event_date(event_date)

    if not parsed_date:
        return {
            "class_1_2": "",
            "class_3_4": "",
            "class_5": "",
        }

    if parsed_date.weekday() == 5:
        return {
            "class_1_2": "12:30",
            "class_3_4": "14:30",
            "class_5": "16:30",
        }

    if parsed_date.weekday() == 6:
        return {
            "class_1_2": "09:30",
            "class_3_4": "11:30",
            "class_5": "13:30",
        }

    return {
        "class_1_2": "",
        "class_3_4": "",
        "class_5": "",
    }


def get_registration_deadlines_for_event(
    db: Session,
    event_id: int,
) -> dict[str, str]:
    groups = ensure_registration_groups(db, event_id)

    return {
        group.group_key: group.registration_deadline or ""
        for group in groups
    }


def update_registration_group_deadlines(
    db: Session,
    event_id: int,
    deadlines: dict[str, str],
    commit: bool = True,
):
    ensure_registration_groups(db, event_id)

    for group_key, deadline in deadlines.items():
        group = db.query(RegistrationGroup).filter(
            RegistrationGroup.event_id == event_id,
            RegistrationGroup.group_key == group_key,
        ).first()

        if group:
            group.registration_deadline = normalize_registration_deadline(deadline)

    if commit:
        db.commit()


def get_registration_group_for_class(
    race_class: int,
    event_id: int,
    db: Session,
) -> RegistrationGroup:
    group_key = get_group_key_for_class(race_class)

    ensure_registration_groups(db, event_id)

    group = db.query(RegistrationGroup).filter(
        RegistrationGroup.event_id == event_id,
        RegistrationGroup.group_key == group_key,
    ).first()

    if not group:
        raise HTTPException(
            status_code=500,
            detail="Startfreigabe-Gruppe nicht gefunden",
        )

    return group


def is_race_class_released(
    race_class: int,
    event_id: int,
    db: Session,
) -> bool:
    group = get_registration_group_for_class(race_class, event_id, db)
    return group.released is True


def parse_registration_group_classes(group: RegistrationGroup) -> list[int]:
    race_classes = []

    for raw_class in group.classes.split(","):
        raw_class = raw_class.strip()

        if not raw_class:
            continue

        race_classes.append(int(raw_class))

    return race_classes


def is_registration_group_race_started(
    group: RegistrationGroup,
    db: Session,
) -> bool:
    race_classes = parse_registration_group_classes(group)

    if not race_classes:
        return False

    race_runs = db.query(RaceRun).filter(
        RaceRun.event_id == group.event_id,
        RaceRun.race_class.in_(race_classes),
    ).all()

    for race_run in race_runs:
        if race_run.started_at or race_run.finished_at or race_run.confirmed:
            return True

        if race_run.status and race_run.status != "created":
            return True

        if race_run.lane_a_time_ms is not None or race_run.lane_b_time_ms is not None:
            return True

        for result in race_run.results:
            if result.raw_time_ms is not None or result.confirmed:
                return True

    return False


def registration_matches_participant(
    registration: Registration,
    participant: Participant,
) -> bool:
    if (
        participant.driver_profile_id
        and registration.driver_profile_id
        and registration.driver_profile_id == participant.driver_profile_id
    ):
        return True

    return (
        registration.first_name.strip().lower() == participant.first_name.strip().lower()
        and registration.last_name.strip().lower() == participant.last_name.strip().lower()
        and registration.birth_year == participant.birth_year
        and registration.class_id == participant.race_class
    )


def delete_linked_registrations_for_participant_before_start_release(
    db: Session,
    participant: Participant,
) -> int:
    registration_group = get_registration_group_for_class(
        race_class=participant.race_class,
        event_id=participant.event_id,
        db=db,
    )

    if registration_group.released or registration_group.released_at:
        return 0

    registrations = db.query(Registration).filter(
        Registration.event_id == participant.event_id,
        Registration.start_number == participant.start_number,
    ).all()

    deleted_count = 0

    for registration in registrations:
        if not registration_matches_participant(registration, participant):
            continue

        delete_registration_relations(db, registration)
        db.delete(registration)
        deleted_count += 1

    return deleted_count


def delete_registration_relations(
    db: Session,
    registration: Registration,
):
    db.query(RegistrationSignature).filter(
        RegistrationSignature.registration_id == registration.id,
    ).delete(synchronize_session=False)
    db.query(RegistrationProtocol).filter(
        RegistrationProtocol.registration_id == registration.id,
    ).delete(synchronize_session=False)
    db.query(RegistrationAccessToken).filter(
        RegistrationAccessToken.registration_id == registration.id,
    ).delete(synchronize_session=False)


def delete_registrations_for_event(
    db: Session,
    event_id: int,
) -> int:
    registrations = db.query(Registration).filter(
        Registration.event_id == event_id,
    ).all()

    deleted_count = 0

    for registration in registrations:
        delete_registration_relations(db, registration)
        db.delete(registration)
        deleted_count += 1

    return deleted_count


def is_registration_deletable(
    registration: Registration,
    db: Session,
) -> bool:
    registration_group = get_registration_group_for_class(
        race_class=registration.class_id,
        event_id=registration.event_id,
        db=db,
    )

    return not registration_group.released and not registration_group.released_at


def get_points_table_c(place: int | None, has_result: bool) -> int:
    if not has_result or place is None:
        return 0

    return POINTS_TABLE_C.get(place, 1)


def sort_place_or_last(place) -> int:
    if isinstance(place, int):
        return place

    return 999


def build_participant_points(
    event: Event,
    db: Session,
) -> dict[int, dict]:
    points_by_participant = {}

    for race_class in [1, 2, 3, 4, 5]:
        participants = db.query(Participant).filter(
            Participant.event_id == event.id,
            Participant.race_class == race_class,
            Participant.active == True,
        ).all()

        pairings = db.query(Pairing).filter(
            Pairing.event_id == event.id,
            Pairing.race_class == race_class,
        ).all()

        result_rows = build_class_results(
            participants=participants,
            pairings=pairings,
        )

        for row in result_rows:
            participant = row["participant"]
            points_by_participant[participant.id] = {
                "place": row["place"],
                "points": get_points_table_c(
                    place=row["place"],
                    has_result=not row.get("withdrawn", False),
                ),
                "total_display": row["total_display"],
                "status": row.get("status") or get_participant_status(participant),
                "withdrawn": row.get("withdrawn", False),
            }

    return points_by_participant


def build_team_standings(
    event: Event,
    teams: list[Team],
    db: Session,
) -> list[dict]:
    points_by_participant = build_participant_points(event, db)
    standings = []

    for team in teams:
        member_rows = []

        for member in team.members:
            participant = member.participant

            if not participant:
                continue

            participant_points = points_by_participant.get(
                participant.id,
                {
                    "place": None,
                    "points": 0,
                    "total_display": "—",
                    "status": get_participant_status(participant),
                    "withdrawn": participant.withdrawn is True,
                },
            )

            member_rows.append(
                {
                    "participant": participant,
                    "driver_name": f"{participant.first_name} {participant.last_name}",
                    "start_number": participant.start_number,
                    "race_class": participant.race_class,
                    "place": participant_points["place"],
                    "points": participant_points["points"],
                    "total_display": participant_points["total_display"],
                    "status": participant_points["status"],
                    "withdrawn": participant_points["withdrawn"],
                    "counted": False,
                }
            )

        member_rows.sort(
            key=lambda row: (
                -row["points"],
                sort_place_or_last(row["place"]),
                row["start_number"],
            )
        )

        counted_points = 0

        for index, member_row in enumerate(member_rows):
            if index < 3:
                member_row["counted"] = True
                counted_points += member_row["points"]

        standings.append(
            {
                "place": None,
                "team": team,
                "members": member_rows,
                "counted_members": [row for row in member_rows if row["counted"]],
                "dropped_members": [row for row in member_rows if not row["counted"]],
                "points": counted_points,
            }
        )

    standings.sort(
        key=lambda row: (
            -row["points"],
            row["team"].name.lower(),
        )
    )

    for index, row in enumerate(standings, start=1):
        row["place"] = index

    return standings


# ------------------------------------------------------------
# Zeit / Lauf-Helfer
# ------------------------------------------------------------

def format_time_ms(time_ms: int | None) -> str:
    return format_centiseconds_time_ms(time_ms)


def get_penalty_for_sector(result: RunResult, sector: int) -> int:
    return getattr(result, f"sector_{sector}_penalty", 0) or 0


def set_penalty_for_sector(result: RunResult, sector: int, value: int):
    setattr(result, f"sector_{sector}_penalty", value)


def prepare_result_for_display(result: RunResult, sectors: list[int]) -> RunResult:
    sector_penalties = {}

    for sector in sectors:
        sector_penalties[sector] = get_penalty_for_sector(result, sector)

    total_penalty_seconds = sum(sector_penalties.values()) + (result.general_penalty or 0)

    if result.participant and result.participant.withdrawn:
        valued_time_ms = None
    elif result.raw_time_ms is not None:
        scoring_raw_time_ms = normalize_time_to_centiseconds_ms(result.raw_time_ms)
        valued_time_ms = scoring_raw_time_ms + (total_penalty_seconds * 1000)
    else:
        valued_time_ms = None

    result.sector_penalties = sector_penalties
    result.total_penalty_seconds = total_penalty_seconds
    result.valued_time_ms = valued_time_ms
    result.valued_time_display = format_time_ms(valued_time_ms)

    return result


def result_has_repeatable_values(result: RunResult) -> bool:
    if result.raw_time_ms is not None:
        return True

    if result.general_penalty:
        return True

    for sector in range(1, 11):
        if get_penalty_for_sector(result, sector):
            return True

    return False


def get_digital_penalty_status(
    run_penalties: list[RunPenalty],
    penalty_type: str,
    lane: str,
    sector: int | None = None,
) -> dict:
    matching_penalty = None

    for penalty in run_penalties:
        if penalty.penalty_type != penalty_type or penalty.lane != lane:
            continue

        if penalty_type == "sector" and penalty.sector != sector:
            continue

        if penalty_type == "general" and penalty.sector not in [None, 0]:
            continue

        matching_penalty = penalty
        break

    if not matching_penalty:
        return {
            "label": "Allgemein" if penalty_type == "general" else f"Position {sector}",
            "points": None,
            "has_submission": False,
            "status": "keine Meldung",
            "changed": False,
        }

    points = matching_penalty.points or 0
    changed = bool(matching_penalty.updated_at)

    return {
        "label": "Allgemein" if penalty_type == "general" else f"Position {sector}",
        "points": points,
        "has_submission": True,
        "status": f"{points} FP übermittelt",
        "changed": changed,
    }


def build_digital_penalty_statuses(
    race_run: RaceRun,
    result: RunResult,
    sectors: list[int],
    run_penalties: list[RunPenalty],
) -> list[dict]:
    lane = "A" if result.lane == "lane_a" else "B"
    statuses = [
        get_digital_penalty_status(
            run_penalties=run_penalties,
            penalty_type="general",
            lane=lane,
        )
    ]

    for sector in sectors:
        statuses.append(
            get_digital_penalty_status(
                run_penalties=run_penalties,
                penalty_type="sector",
                lane=lane,
                sector=sector,
            )
        )

    return statuses


def get_lane_results_for_run(run: RaceRun) -> tuple[RunResult | None, RunResult | None]:
    lane_a = None
    lane_b = None

    for result in run.results:
        if result.lane == "lane_a":
            lane_a = result

        if result.lane == "lane_b":
            lane_b = result

    return lane_a, lane_b


def get_round_display(round_name: str) -> tuple[int, str]:
    if round_name in ["vorlauf", "viertelfinale"]:
        return 1, "Viertelfinale"

    if round_name == "halbfinale":
        return 2, "Halbfinale"

    if round_name == "finale":
        return 3, "Finale"

    return 0, round_name


def reset_run_values_for_repeat(
    race_run: RaceRun,
    results: list[RunResult],
    repeat_scope: str = "both",
):
    if repeat_scope not in ["lane_a", "lane_b", "both"]:
        repeat_scope = "both"

    reset_lanes = ["lane_a", "lane_b"] if repeat_scope == "both" else [repeat_scope]

    race_run.status = "created"
    race_run.confirmed = False
    race_run.traffic_light_status = "off"
    race_run.false_start_lane = "none"
    race_run.started_at = None
    race_run.finished_at = None

    if "lane_a" in reset_lanes:
        race_run.lane_a_time_ms = None

    if "lane_b" in reset_lanes:
        race_run.lane_b_time_ms = None

    race_run.reset_count = (race_run.reset_count or 0) + 1
    race_run.last_reset_at = now_string()
    race_run.last_reset_by = "Wiederholung"

    for result in results:
        if result.lane not in reset_lanes:
            continue

        result.raw_time_ms = None
        result.general_penalty = 0
        result.confirmed = False

        for sector in range(1, 11):
            set_penalty_for_sector(result, sector, 0)


def repeat_scope_setting_key(run_id: int) -> str:
    return f"repeat_scope_run_{run_id}"


def normalize_repeat_scope(value: str | None) -> str:
    value = (value or "both").strip().lower()

    if value in ["lane_a", "lane_b", "both"]:
        return value

    return "both"


def get_run_repeat_scope(db: Session, run_id: int) -> str:
    return normalize_repeat_scope(
        get_setting(db, repeat_scope_setting_key(run_id), "both")
    )


def is_lane_editable_for_repeat_scope(lane: str, repeat_scope: str) -> bool:
    return repeat_scope == "both" or lane == repeat_scope


# ------------------------------------------------------------
# Monitor-Helfer
# ------------------------------------------------------------

def get_monitor_penalty_sum(result: RunResult | None, db: Session) -> int:
    if result is None:
        return 0

    penalty = result.general_penalty or 0

    for sector in range(1, 11):
        penalty += get_penalty_for_sector(result, sector)

    return penalty


def normalize_penalty_input(value: str | None) -> int:
    if value is None or str(value).strip() == "":
        return 0

    penalty = max(0, int(value))
    return ((penalty + 2) // 5) * 5


def get_monitor_valued_time_ms_for_raw(
    result: RunResult | None,
    db: Session,
    raw_time_ms: int | None,
) -> int | None:
    if result is None or raw_time_ms is None:
        return None

    if result.participant and result.participant.withdrawn:
        return None

    scoring_raw_time_ms = normalize_time_to_centiseconds_ms(raw_time_ms)

    if scoring_raw_time_ms is None:
        return None

    return scoring_raw_time_ms + (get_monitor_penalty_sum(result, db) * 1000)


def get_monitor_valued_time_ms(result: RunResult | None, db: Session) -> int | None:
    return get_monitor_valued_time_ms_for_raw(
        result=result,
        db=db,
        raw_time_ms=result.raw_time_ms if result else None,
    )


def monitor_driver_name(participant: Participant | None) -> str:
    if participant is None:
        return "-"
    
    # Don't show eliminated drivers in monitor
    if participant.withdrawn:
        return "-"

    return f"#{participant.start_number} {participant.first_name} {participant.last_name}"


def monitor_driver_display_name(participant: Participant | None) -> str:
    if participant is None or participant.withdrawn:
        return "-"

    first_name = (participant.first_name or "").strip()
    last_name = (participant.last_name or "").strip()

    if first_name and last_name:
        return f"{first_name} {last_name}"

    return first_name or last_name or "-"


def monitor_result_payload(
    result: RunResult | None,
    db: Session,
    raw_time_ms_override: int | None = None,
) -> dict:
    if result is None:
        return {
            "driver": "-",
            "start_number": None,
            "last_name": "-",
            "display_name": "-",
            "raw_time": "-",
            "penalty": "-",
            "valued_time": "-",
        }

    participant = result.participant
    if participant is None or participant.withdrawn:
        start_number = None
        last_name = "-"
        display_name = "-"
    else:
        start_number = participant.start_number
        last_name = participant.last_name
        display_name = monitor_driver_display_name(participant)

    raw_time_ms = (
        raw_time_ms_override
        if isinstance(raw_time_ms_override, int) and raw_time_ms_override > 0
        else result.raw_time_ms
    )

    return {
        "driver": monitor_driver_name(participant),
        "start_number": start_number,
        "last_name": last_name,
        "display_name": display_name,
        "raw_time": format_time_ms(raw_time_ms),
        "penalty": get_monitor_penalty_sum(result, db),
        "valued_time": format_time_ms(
            get_monitor_valued_time_ms_for_raw(result, db, raw_time_ms)
        ),
    }


def monitor_run_payload(
    race_run: RaceRun,
    pairing: Pairing,
    db: Session,
    live_status: dict | None = None,
) -> dict:
    lane_a_result, lane_b_result = get_lane_results_for_run(race_run)
    round_number, round_label = get_round_display(race_run.round_name)
    live_times = live_status.get("times", {}) if live_status else {}
    repeat_scope = get_run_repeat_scope(db, race_run.id)
    use_live_times = bool(
        live_status
        and live_status.get("ok")
        and not race_run.confirmed
        and race_run.status in ["running", "started"]
    )
    lane_a_live_ms = (
        live_times.get("lane_a_ms")
        if use_live_times and is_lane_editable_for_repeat_scope("lane_a", repeat_scope)
        else None
    )
    lane_b_live_ms = (
        live_times.get("lane_b_ms")
        if use_live_times and is_lane_editable_for_repeat_scope("lane_b", repeat_scope)
        else None
    )

    return {
        "id": race_run.id,
        "race_class": race_run.race_class,
        "round_name": race_run.round_name,
        "round_label": round_label,
        "round_number": round_number,
        "pairing_number": pairing.pairing_number,
        "run_number": race_run.run_number,
        "status": race_run.status,
        "traffic_light_status": race_run.traffic_light_status or "off",
        "confirmed": race_run.confirmed,
        "lane_a": monitor_result_payload(lane_a_result, db, lane_a_live_ms),
        "lane_b": monitor_result_payload(lane_b_result, db, lane_b_live_ms),
    }


def get_monitor_order_key(race_run: RaceRun, pairing: Pairing) -> tuple:
    round_number, _round_label = get_round_display(race_run.round_name)

    return (
        round_number,
        race_run.race_class,
        pairing.pairing_number,
        race_run.run_number,
    )


def get_monitor_runs(
    event_id: int,
    db: Session,
) -> list[tuple[RaceRun, Pairing]]:
    pairings = db.query(Pairing).filter(
        Pairing.event_id == event_id,
    ).all()

    items: list[tuple[RaceRun, Pairing]] = []

    for pairing in pairings:
        for race_run in pairing.runs:
            items.append((race_run, pairing))

    items.sort(
        key=lambda item: get_monitor_order_key(
            race_run=item[0],
            pairing=item[1],
        )
    )

    return items


def get_run_start_order_key(race_run: RaceRun, pairing: Pairing) -> tuple:
    round_number, _round_label = get_round_display(race_run.round_name)

    return (
        round_number,
        pairing.pairing_number,
        race_run.run_number,
    )


def get_start_ordered_class_runs(
    event_id: int,
    race_class: int,
    db: Session,
) -> list[tuple[RaceRun, Pairing]]:
    pairings = db.query(Pairing).filter(
        Pairing.event_id == event_id,
        Pairing.race_class == race_class,
    ).all()

    items: list[tuple[RaceRun, Pairing]] = []

    for pairing in pairings:
        for race_run in pairing.runs:
            items.append((race_run, pairing))

    items.sort(
        key=lambda item: get_run_start_order_key(
            race_run=item[0],
            pairing=item[1],
        )
    )

    return items


def pick_next_unconfirmed_run(
    run_items: list[tuple[RaceRun, Pairing]],
) -> tuple[RaceRun, Pairing] | None:
    for race_run, pairing in run_items:
        if not race_run.confirmed:
            return race_run, pairing
    return None


def build_run_navigation_payload(
    race_run: RaceRun,
    pairing: Pairing,
) -> dict:
    round_number, round_label = get_round_display(race_run.round_name)

    return {
        "id": race_run.id,
        "race_class": race_run.race_class,
        "round_number": round_number,
        "round_label": round_label,
        "pairing_number": pairing.pairing_number,
        "run_number": race_run.run_number,
        "status": race_run.status,
    }


def get_class_round_config(round_key: str) -> dict | None:
    for config in CLASS_ROUND_CONFIGS:
        if config["key"] == round_key:
            return config

    return None


def participant_start_label(participant: Participant | None) -> str:
    if participant is None or participant.withdrawn:
        return "-"

    return f"#{participant.start_number} {participant.first_name} {participant.last_name}"


def pick_monitor_current_run(
    run_items: list[tuple[RaceRun, Pairing]],
    active_run_id: int | None = None,
) -> tuple[RaceRun, Pairing] | None:
    """
    Aktuellen Monitor-Lauf bestimmen.

    Die aktiv gesetzte Lauf-ID hat Vorrang. Wenn keine aktive ID gesetzt ist,
    zeigt der Monitor den ersten unbestätigten Lauf in fachlicher Reihenfolge.
    Dadurch bleibt die Monitoranzeige auch vor dem Drücken von START nutzbar.
    """
    if active_run_id:
        for race_run, pairing in run_items:
            if race_run.id == active_run_id and not race_run.confirmed:
                return race_run, pairing

    for race_run, pairing in run_items:
        if not race_run.confirmed:
            return race_run, pairing

    return None


def pick_monitor_next_runs(
    run_items: list[tuple[RaceRun, Pairing]],
    db: Session,
    current_run: RaceRun | None,
) -> list[dict]:
    """
    Nächste Starter klassenübergreifend in Startreihenfolge anzeigen.

    Bahnwechsel/Rückläufe derselben Paarung werden für die Teilnehmeranzeige
    ausgeblendet. Die Rennläufe selbst bleiben unverändert.
    """
    if current_run is None:
        return []

    next_runs = []
    current_found = False
    displayed_pairing_ids: set[int] = set()

    for race_run, pairing in run_items:
        if race_run.id == current_run.id:
            current_found = True
            displayed_pairing_ids.add(pairing.id)
            continue

        if not current_found:
            continue

        if race_run.confirmed:
            continue

        if pairing.id in displayed_pairing_ids:
            continue

        displayed_pairing_ids.add(pairing.id)
        next_runs.append(monitor_run_payload(race_run, pairing, db))

    return next_runs


def get_monitor_result_classes(race_class: int | None) -> set[int] | None:
    if race_class in [1, 2]:
        return {1, 2}

    if race_class in [3, 4]:
        return {3, 4}

    if race_class == 5:
        return {5}

    return None


def get_monitor_live_result_runs(
    run_items: list[tuple[RaceRun, Pairing]],
    db: Session,
    focus_race_classes: set[int] | None = None,
    preferred_round_number: int | None = None,
    limit: int = 20,
) -> list[dict]:
    participant_rows = {}
    available_rounds_with_results = set()

    for race_run, _pairing in run_items:
        normalized_round = "viertelfinale" if race_run.round_name == ROUND_PRELIMINARY else race_run.round_name

        if normalized_round not in ["viertelfinale", ROUND_SEMIFINAL]:
            continue

        if focus_race_classes is not None and race_run.race_class not in focus_race_classes:
            continue

        if race_run.results:
            round_number, _round_label = get_round_display(race_run.round_name)

            if any(result.raw_time_ms is not None for result in race_run.results):
                available_rounds_with_results.add(round_number)

    if not available_rounds_with_results:
        return []

    if preferred_round_number in available_rounds_with_results:
        display_round_number = preferred_round_number
    else:
        available_round_numbers = [
            round_number
            for round_number in available_rounds_with_results
            if preferred_round_number is None or round_number <= preferred_round_number
        ]

        if not available_round_numbers:
            available_round_numbers = list(available_rounds_with_results)

        display_round_number = max(available_round_numbers)

    for race_run, _pairing in run_items:
        normalized_round = "viertelfinale" if race_run.round_name == ROUND_PRELIMINARY else race_run.round_name

        if normalized_round not in ["viertelfinale", ROUND_SEMIFINAL]:
            continue

        round_number, round_label = get_round_display(race_run.round_name)

        if focus_race_classes is not None and race_run.race_class not in focus_race_classes:
            continue

        if display_round_number != round_number:
            continue

        for result in race_run.results:
            if result.raw_time_ms is None:
                continue

            valued_time_ms = get_monitor_valued_time_ms(result, db)

            if valued_time_ms is None:
                continue

            row_key = (
                result.participant_id,
                race_run.race_class,
                round_number,
            )
            existing = participant_rows.get(row_key)

            if existing is None:
                existing = {
                    "driver": monitor_driver_name(result.participant),
                    "race_class": race_run.race_class,
                    "round_label": round_label,
                    "round_number": round_number,
                    "raw_time_ms": 0,
                    "penalty": 0,
                    "valued_time_ms": 0,
                    "runs_count": 0,
                }
                participant_rows[row_key] = existing

            existing["raw_time_ms"] += normalize_time_to_centiseconds_ms(result.raw_time_ms) or 0
            existing["penalty"] += get_monitor_penalty_sum(result, db)
            existing["valued_time_ms"] += valued_time_ms
            existing["runs_count"] += 1

    rows = sorted(
        [
            {
                **row,
                "raw_time": format_time_ms(row["raw_time_ms"]),
                "valued_time": format_time_ms(row["valued_time_ms"]),
            }
            for row in participant_rows.values()
        ],
        key=lambda row: (
            row["race_class"],
            row["valued_time_ms"],
            row["penalty"],
            row["driver"],
        )
    )

    current_class = None
    previous_key = None
    current_place = 0
    class_position = 0

    for row in rows:
        if row["race_class"] != current_class:
            current_class = row["race_class"]
            class_position = 1
            current_place = 1
            previous_key = (row["race_class"], row["valued_time_ms"], row["penalty"])
        else:
            class_position += 1
            key = (row["race_class"], row["valued_time_ms"], row["penalty"])

            if key != previous_key:
                current_place = class_position
                previous_key = key

        row["place"] = current_place

    return rows[:limit]


def build_monitor_logo_signal_payload(
    settings: dict,
    setting: MonitorDisplaySetting,
    current_run: RaceRun | None,
) -> dict:
    visible = bool(setting.show_logo_signal)
    status = "off"

    logo_status = None

    if visible and settings.get("logo_enabled") == "true":
        logo_status = get_logo_status_payload()
        outputs = logo_status.get("outputs", {})

        if outputs.get("red"):
            status = "red"
        elif outputs.get("yellow"):
            status = "orange"
        elif outputs.get("green"):
            status = "green"

    if status == "off" and visible and current_run is not None:
        status = current_run.traffic_light_status or "off"

    if status == "yellow":
        status = "orange"

    if status not in ["red", "orange", "green"]:
        status = "off"

    return {
        "visible": visible,
        "status": status,
        "label": {
            "red": "ROT",
            "orange": "ORANGE",
            "green": "GRÜN",
            "off": "AUS",
        }.get(status, "AUS"),
        "mode": settings.get("logo_access_mode") or "inactive",
        "hardware": logo_status,
    }


def build_monitor_payload(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting | None = None,
) -> dict:
    if setting is None:
        setting = get_or_create_monitor_display_setting(event, db)

    active_run_id_str = get_setting(db, "startdisplay_active_run_id")
    active_run_id = int(active_run_id_str) if active_run_id_str and active_run_id_str.isdigit() else None

    run_items = get_monitor_runs(
        event_id=event.id,
        db=db,
    )

    current_item = pick_monitor_current_run(run_items, active_run_id=active_run_id)

    if current_item is not None:
        current_run, current_pairing = current_item
        live_status = sync_logo_times_for_run(current_run, db)
        current_payload = monitor_run_payload(
            current_run,
            current_pairing,
            db,
            live_status=live_status,
        )
        current_round_number, _current_round_label = get_round_display(current_run.round_name)

        if current_run.round_name == ROUND_FINAL:
            current_round_number = get_round_display(ROUND_SEMIFINAL)[0]
    else:
        current_payload = None
        current_run = None
        current_round_number = None

    logo_settings = get_logo_settings(db)

    return {
        "event": {
            "id": event.id,
            "event_number": event.event_number,
            "name": event.name,
            "date": event.event_date or "",
            "organizer_club": event.organizer_club or "",
            "active": bool(event.active),
        },
        "server_time": now_string(),
        "current_run": current_payload,
        "logo_signal": build_monitor_logo_signal_payload(
            settings=logo_settings,
            setting=setting,
            current_run=current_run,
        ),
        "next_runs": pick_monitor_next_runs(
            run_items=run_items,
            db=db,
            current_run=current_run,
        ),
        "live_results": get_monitor_live_result_runs(
            run_items=run_items,
            db=db,
            focus_race_classes=get_monitor_result_classes(
                current_run.race_class if current_run is not None else None
            ),
            preferred_round_number=current_round_number,
            limit=20,
        ),
        "final_note": "Finale wird nicht live angezeigt.",
    }


MONITOR_ROUND_OPTIONS = [
    {
        "key": ROUND_PRELIMINARY,
        "label": "Viertelfinale",
    },
    {
        "key": ROUND_SEMIFINAL,
        "label": "Halbfinale",
    },
    {
        "key": ROUND_FINAL,
        "label": "Finale",
    },
]


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


def build_monitor_result_rows_for_round(
    event: Event,
    db: Session,
    race_class: int,
    round_name: str,
) -> list[dict]:
    round_key_by_name = {
        ROUND_PRELIMINARY: "quarterfinal",
        ROUND_SEMIFINAL: "semifinal",
        ROUND_FINAL: "final",
    }
    round_key = round_key_by_name.get(round_name)

    if round_key is None:
        return []

    participants = db.query(Participant).filter(
        Participant.event_id == event.id,
        Participant.race_class == race_class,
        Participant.active == True,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == event.id,
        Pairing.race_class == race_class,
    ).all()

    compact_rows = build_compact_round_results(
        participants=participants,
        pairings=pairings,
        round_key=round_key,
    )

    return [
        {
            "place": row["place"],
            "start_number": row["start_number"],
            "driver": row["driver_name"],
            "run_1_lane_label": row["round_detail"]["run_1_lane_label"],
            "run_1_raw_display": row["round_detail"]["run_1_raw_display"],
            "run_1_penalty": row["round_detail"]["run_1_penalty"],
            "run_2_lane_label": row["round_detail"]["run_2_lane_label"],
            "run_2_raw_display": row["round_detail"]["run_2_raw_display"],
            "run_2_penalty": row["round_detail"]["run_2_penalty"],
            "round_total_display": row["round_detail"]["round_total_display"],
        }
        for row in compact_rows
    ]


def build_monitor_result_sections(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting,
) -> list[dict]:
    selected_classes = [
        int(item)
        for item in parse_monitor_csv(setting.display_classes)
        if item.isdigit() and int(item) in [1, 2, 3, 4, 5]
    ]
    selected_rounds = [
        item
        for item in parse_monitor_csv(setting.display_rounds)
        if item in [ROUND_PRELIMINARY, ROUND_SEMIFINAL, ROUND_FINAL]
    ]
    sections = []

    for race_class in selected_classes:
        for round_name in selected_rounds:
            rows = build_monitor_result_rows_for_round(
                event=event,
                db=db,
                race_class=race_class,
                round_name=round_name,
            )

            if not rows:
                continue

            _round_number, round_label = get_round_display(round_name)
            sections.append(
                {
                    "title": f"Ergebnisse Klasse {race_class} · {round_label}",
                    "rows": rows,
                }
            )

    return sections


def build_monitor_team_rows(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting,
) -> list[dict]:
    if not setting.show_team_results:
        return []

    teams = db.query(Team).filter(
        Team.event_id == event.id,
    ).order_by(
        Team.name,
    ).all()
    standings = build_team_standings(
        event=event,
        teams=teams,
        db=db,
    )

    return [
        {
            "place": row["place"],
            "team_name": row["team"].name,
            "points": row["points"],
            "members": [
                {
                    "driver": member["driver_name"],
                    "start_number": member["start_number"],
                    "race_class": member["race_class"],
                    "place": member["place"],
                    "points": member["points"],
                    "status": member["status"],
                    "withdrawn": member["withdrawn"],
                    "counted": member["counted"],
                }
                for member in row["members"]
            ],
        }
        for row in standings
    ]


def build_monitor_overall_sections(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting,
) -> list[dict]:
    if not setting.show_overall_results:
        return []

    selected_classes = [
        int(item)
        for item in parse_monitor_csv(setting.display_classes)
        if item.isdigit() and int(item) in [1, 2, 3, 4, 5]
    ]
    sections = []

    for race_class in selected_classes:
        pairings = db.query(Pairing).filter(
            Pairing.event_id == event.id,
            Pairing.race_class == race_class,
        ).all()
        rows_by_round = build_class_export_rows(pairings)
        overall_rows = [
            row
            for row in build_overall_rows(rows_by_round)
            if row.get("place") not in ["", None]
        ]

        if not overall_rows:
            continue

        sections.append(
            {
                "title": f"Gesamtwertung Klasse {race_class}",
                "rows": [
                    {
                        "place": row["place"],
                        "race_class": race_class,
                        "start_number": row["start_number"],
                        "driver": row["driver_name"],
                        "quarterfinal": row["quarterfinal_display"] or "-",
                        "semifinal": row["semifinal_display"] or "-",
                        "final": row["final_display"] or "-",
                        "total_penalty": row["total_penalty"],
                        "total": row["total_display"] or "-",
                    }
                    for row in overall_rows
                ],
            }
        )

    return sections


def build_monitor_output_payload(
    event: Event,
    db: Session,
) -> dict:
    setting = get_or_create_monitor_display_setting(event, db)
    race_payload = build_monitor_payload(event, db, setting=setting)

    if not setting.show_race_control:
        race_payload["current_run"] = None
        race_payload["next_runs"] = []

    race_payload["display_setting"] = monitor_display_setting_payload(setting)
    race_payload["result_sections"] = build_monitor_result_sections(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["overall_sections"] = build_monitor_overall_sections(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["team_results"] = build_monitor_team_rows(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["statistics"] = (
        build_result_statistics(
            event=event,
            db=db,
        )
        if setting.show_statistics
        else None
    )
    race_payload["marshal_qr"] = get_monitor_marshal_qr_payload(event, db)
    race_payload["checkin_qr"] = get_monitor_checkin_qr_payload(event, setting)
    race_payload["live_results"] = []

    return race_payload


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


CHECKIN_QR_BASE_URL = "http://race.local:8010"


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


# ------------------------------------------------------------
# Basis
# ------------------------------------------------------------

@app.get("/")
def read_root():
    return RedirectResponse(url="/ui/events")


@app.get("/health")
def health_check(
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    return {
        "status": "ok",
        "event_year": get_event_year(active_event),
        "active_event": active_event.name if active_event else None,
        "active_event_closed": active_event.closed if active_event else None,
    }


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------

@app.get("/ui")
def ui_dashboard_redirect(
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/dashboard",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/dashboard")
def ui_event_dashboard(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    registration_groups = ensure_registration_groups(db, active_event.id)

    total_participants = db.query(Participant).filter(
        Participant.event_id == active_event.id
    ).count()

    pending_checkin_registrations = db.query(Registration).filter(
        Registration.event_id == active_event.id,
        Registration.registration_status.in_(["submitted", "under_review", "correction_required"]),
    ).count()

    class_counts = {}

    for race_class in [1, 2, 3, 4, 5]:
        class_counts[race_class] = db.query(Participant).filter(
            Participant.event_id == active_event.id,
            Participant.race_class == race_class,
            Participant.active == True,
        ).count()

    dashboard_registration_groups = []

    for group in registration_groups:
        race_classes = [
            int(item.strip())
            for item in (group.classes or "").split(",")
            if item.strip().isdigit()
        ]
        completed_classes = 0
        class_progress = []

        for race_class in race_classes:
            participants_count = class_counts.get(race_class, 0)
            pairings = db.query(Pairing).filter(
                Pairing.event_id == active_event.id,
                Pairing.race_class == race_class,
            ).all()
            final_exists = round_exists(pairings, ROUND_FINAL)
            final_complete = is_round_complete(pairings, ROUND_FINAL)
            class_completed = final_exists and final_complete

            if class_completed:
                completed_classes += 1
                class_status_label = "Abgeschlossen"
                class_status_type = "complete"
            elif participants_count == 0:
                class_status_label = "Keine Teilnehmer"
                class_status_type = "open"
            elif not pairings:
                class_status_label = "Noch nicht gestartet"
                class_status_type = "open"
            else:
                class_status_label = "Offen"
                class_status_type = "open"

            class_progress.append(
                {
                    "race_class": race_class,
                    "participants_count": participants_count,
                    "completed": class_completed,
                    "status_label": class_status_label,
                    "status_type": class_status_type,
                }
            )

        total_classes = len(race_classes)
        all_classes_completed = (
            total_classes > 0
            and completed_classes == total_classes
        )

        if all_classes_completed:
            status_label = "Abgeschlossen"
            status_type = "complete"
            detail_text = "Alle Klassen dieser Startgruppe sind abgeschlossen."
        elif group.released:
            status_label = "Startfreigabe erteilt"
            status_type = "released"
            detail_text = "Rennbetrieb offen."
        else:
            status_label = "Startfreigabe noch nicht erteilt"
            status_type = "open"
            detail_text = "Vorbereitung / Nennung noch offen."

        dashboard_registration_groups.append(
            {
                "group_key": group.group_key,
                "name": group.name,
                "classes": group.classes,
                "released": group.released,
                "registration_deadline": group.registration_deadline,
                "total_classes": total_classes,
                "completed_classes": completed_classes,
                "class_progress": class_progress,
                "all_classes_completed": all_classes_completed,
                "status_label": status_label,
                "status_type": status_type,
                "detail_text": detail_text,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_event": active_event,
            "total_participants": total_participants,
            "pending_checkin_registrations": pending_checkin_registrations,
            "class_counts": class_counts,
            "event_year": get_event_year(active_event),
            "current_time": now_string(),
            "registration_groups": registration_groups,
            "dashboard_registration_groups": dashboard_registration_groups,
        },
    )


# ------------------------------------------------------------
# Veranstaltungen
# ------------------------------------------------------------

@app.get("/ui/events")
def ui_events(
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)
    event_tab = request.query_params.get("tab", "open")

    if event_tab not in {"open", "new", "closed"}:
        event_tab = "open"

    open_events = db.query(Event).filter(
        Event.closed == False
    ).order_by(
        Event.active.desc(),
        Event.event_date.asc(),
        Event.id.desc(),
    ).all()

    closed_events = db.query(Event).filter(
        Event.closed == True
    ).order_by(
        Event.id.desc()
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="events.html",
        context={
            "active_event": active_event,
            "open_events": open_events,
            "closed_events": closed_events,
            "event_tab": event_tab,
            "error": None,
            "event_year": get_event_year(active_event),
        },
    )


@app.get("/ui/help")
def ui_help(
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    return templates.TemplateResponse(
        request=request,
        name="help.html",
        context={
            "active_event": active_event,
            "event_year": get_event_year(active_event),
        },
    )


@app.post("/ui/events/new")
def ui_create_event(
    request: Request,
    event_number: str = Form(...),
    name: str = Form(...),
    event_date: str = Form(""),
    location: str = Form(""),
    organizer_club: str = Form(""),
    sectors_per_lane: int = Form(1),
    registration_deadline_class_1_2: str = Form(""),
    registration_deadline_class_3_4: str = Form(""),
    registration_deadline_class_5: str = Form(""),
    db: Session = Depends(get_db),
):
    event_number = event_number.strip()
    name = name.strip()

    if sectors_per_lane < 1:
        sectors_per_lane = 1

    if sectors_per_lane > 10:
        sectors_per_lane = 10

    existing = db.query(Event).filter(
        Event.event_number == event_number
    ).first()

    if existing:
        active_event = get_active_event(db)

        open_events = db.query(Event).filter(
            Event.closed == False
        ).order_by(
            Event.active.desc(),
            Event.event_date.asc(),
            Event.id.desc(),
        ).all()

        closed_events = db.query(Event).filter(
            Event.closed == True
        ).order_by(
            Event.id.desc()
        ).all()

        return templates.TemplateResponse(
            request=request,
            name="events.html",
            context={
                "active_event": active_event,
                "open_events": open_events,
                "closed_events": closed_events,
                "event_tab": "new",
                "error": "Diese Veranstaltungsnummer ist bereits vergeben.",
                "event_year": get_event_year(active_event),
            },
        )

    event = Event(
        event_number=event_number,
        name=name,
        event_date=event_date.strip() or None,
        location=location.strip() or None,
        organizer_club=organizer_club.strip() or None,
        sectors_per_lane=sectors_per_lane,
        marshal_enabled=False,
        active=False,
        closed=False,
        closed_at=None,
        closed_by=None,
        closed_reason=None,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    ensure_registration_groups(db, event.id)

    registration_deadlines = {
        "class_1_2": registration_deadline_class_1_2,
        "class_3_4": registration_deadline_class_3_4,
        "class_5": registration_deadline_class_5,
    }

    if not any((value or "").strip() for value in registration_deadlines.values()):
        registration_deadlines = get_default_registration_deadlines_for_event_date(
            event.event_date,
        )

    update_registration_group_deadlines(
        db=db,
        event_id=event.id,
        deadlines=registration_deadlines,
    )

    return RedirectResponse(
        url=f"/ui/events/{event.id}/dashboard",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/activate")
def ui_activate_event(
    event_id: int,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)

    if event.closed:
        raise HTTPException(
            status_code=400,
            detail="Geschlossene Veranstaltung kann nicht aktiv gesetzt werden.",
        )

    db.query(Event).update(
        {
            Event.active: False
        }
    )

    event.active = True
    db.commit()

    ensure_registration_groups(db, event.id)

    return RedirectResponse(
        url=f"/ui/events/{event.id}/dashboard",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/edit")
def ui_edit_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)

    if event.closed:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return templates.TemplateResponse(
        request=request,
        name="event_edit.html",
        context={
            "active_event": event,
            "event": event,
            "registration_deadlines": get_registration_deadlines_for_event(db, event.id),
            "error": None,
            "event_year": get_event_year(event),
        },
    )


@app.post("/ui/events/{event_id}/edit")
def ui_update_event(
    event_id: int,
    request: Request,
    event_number: str = Form(...),
    name: str = Form(...),
    event_date: str = Form(""),
    location: str = Form(""),
    organizer_club: str = Form(""),
    sectors_per_lane: int = Form(1),
    registration_deadline_class_1_2: str = Form(""),
    registration_deadline_class_3_4: str = Form(""),
    registration_deadline_class_5: str = Form(""),
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    ensure_event_is_open(event)

    if sectors_per_lane < 1:
        sectors_per_lane = 1

    if sectors_per_lane > 10:
        sectors_per_lane = 10

    event_number = event_number.strip()

    existing = db.query(Event).filter(
        Event.event_number == event_number,
        Event.id != event_id,
    ).first()

    if existing:
        return templates.TemplateResponse(
            request=request,
            name="event_edit.html",
            context={
                "active_event": event,
                "event": event,
                "registration_deadlines": {
                    "class_1_2": normalize_registration_deadline(registration_deadline_class_1_2) or "",
                    "class_3_4": normalize_registration_deadline(registration_deadline_class_3_4) or "",
                    "class_5": normalize_registration_deadline(registration_deadline_class_5) or "",
                },
                "error": "Diese Veranstaltungsnummer ist bereits vergeben.",
                "event_year": get_event_year(event),
            },
        )

    event.event_number = event_number
    event.name = name.strip()
    event.event_date = event_date.strip() or None
    event.location = location.strip() or None
    event.organizer_club = organizer_club.strip() or None
    event.sectors_per_lane = sectors_per_lane

    update_registration_group_deadlines(
        db=db,
        event_id=event.id,
        deadlines={
            "class_1_2": registration_deadline_class_1_2,
            "class_3_4": registration_deadline_class_3_4,
            "class_5": registration_deadline_class_5,
        },
        commit=False,
    )

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{event.id}/dashboard",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/close")
def ui_close_event(
    event_id: int,
    closed_by: str = Form(...),
    closed_reason: str = Form(...),
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)

    valid_reasons = ["beendet", "abgesagt", "abgebrochen"]

    if closed_reason not in valid_reasons:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Schließgrund",
        )

    event.closed = True
    event.closed_at = now_string()
    event.closed_by = closed_by.strip()
    event.closed_reason = closed_reason
    event.active = False

    db.commit()

    return RedirectResponse(
        url="/ui/events",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/delete")
def ui_delete_event(
    event_id: int,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)

    db.query(RunResult).filter(
        RunResult.event_id == event.id
    ).delete()

    db.query(RunPenalty).filter(
        RunPenalty.event_id == event.id
    ).delete()

    db.query(RaceRun).filter(
        RaceRun.event_id == event.id
    ).delete()

    db.query(Pairing).filter(
        Pairing.event_id == event.id
    ).delete()

    db.query(RegistrationGroup).filter(
        RegistrationGroup.event_id == event.id
    ).delete()

    db.query(RegistrationNotification).filter(
        RegistrationNotification.event_id == event.id
    ).delete()

    delete_registrations_for_event(db, event.id)

    db.query(MonitorDisplaySetting).filter(
        MonitorDisplaySetting.event_id == event.id
    ).delete()

    db.query(TeamMember).filter(
        TeamMember.event_id == event.id
    ).delete()

    db.query(Team).filter(
        Team.event_id == event.id
    ).delete()

    db.query(Participant).filter(
        Participant.event_id == event.id
    ).delete()

    db.delete(event)
    db.commit()

    return RedirectResponse(
        url="/ui/events",
        status_code=303,
    )


# ------------------------------------------------------------
# Teilnehmer je Veranstaltung
# ------------------------------------------------------------

@app.get("/ui/participants")
def ui_participant_list_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/participants")


@app.get("/ui/participants/new")
def ui_new_participant_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/participants/new")


@app.get("/ui/participants/{participant_id}/edit")
def ui_edit_participant_redirect(
    participant_id: int,
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, f"/participants/{participant_id}/edit")


@app.get("/ui/events/{event_id}/participants")
def ui_event_participant_list(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id
    ).order_by(
        Participant.race_class,
        Participant.start_number,
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="participant_list.html",
        context={
            "active_event": active_event,
            "participants": participants,
            "event_year": get_event_year(active_event),
        },
    )


def require_registration(event_id: int, registration_id: int, db: Session):
    registration = db.query(Registration).filter(
        Registration.id == registration_id,
        Registration.event_id == event_id,
    ).first()

    if not registration:
        raise HTTPException(
            status_code=404,
            detail="Registrierung nicht gefunden",
        )

    return registration


def validate_registration_email(send_protocol_email: bool, email: str | None) -> bool:
    if not send_protocol_email:
        return True

    clean_email = (email or "").strip()
    return "@" in clean_email and "." in clean_email


REGISTRATION_STATUS_LABELS = {
    "draft": "Entwurf",
    "submitted": "Eingereicht",
    "under_review": "In Prüfung",
    "approved": "Bestätigt",
    "rejected": "Abgelehnt",
    "correction_required": "Korrektur erforderlich",
    "cancelled": "Storniert",
}


REGISTRATION_STATUS_ORDER = {
    "under_review": 1,
    "submitted": 2,
    "correction_required": 3,
    "draft": 4,
    "rejected": 5,
    "cancelled": 6,
    "approved": 7,
}


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


@app.get("/ui/events/{event_id}/participants/new")
def ui_event_new_participant(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    settings = get_app_settings(db)

    if active_event.closed:
        return RedirectResponse(
            url=f"/ui/events/{event_id}/participants",
            status_code=303,
        )

    return templates.TemplateResponse(
        request=request,
        name="participant_form.html",
        context={
            "active_event": active_event,
            "error": None,
            "success": None,
            "event_year": get_event_year(active_event),
            "youth_card_scan_enabled": settings.get("youth_card_scan_enabled") == "true",
        },
    )


@app.post("/ui/events/{event_id}/participants/new")
def ui_event_create_participant(
    event_id: int,
    request: Request,
    start_number: int = Form(...),
    driver_profile_id: int | None = Form(None),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_year: int = Form(...),
    club: str = Form(""),
    team: str = Form(""),
    adac_number: str = Form(""),
    youth_license_number: str = Form(""),
    youth_card_qr_url: str = Form(""),
    youth_card_valid_until: str = Form(""),
    youth_card_checked: str = Form("false"),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)

    existing = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.start_number == start_number,
    ).first()

    if existing:
        return templates.TemplateResponse(
            request=request,
            name="participant_form.html",
            context={
                "active_event": active_event,
                "error": "Diese Startnummer ist bereits vergeben.",
                "success": None,
                "event_year": get_event_year(active_event),
                "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
            },
        )

    if driver_profile_id:
        existing_driver_registration = db.query(Participant).filter(
            Participant.event_id == active_event.id,
            Participant.driver_profile_id == driver_profile_id,
        ).first()

        if existing_driver_registration:
            return templates.TemplateResponse(
                request=request,
                name="participant_form.html",
                context={
                    "active_event": active_event,
                    "error": "Dieser Fahrer ist für diese Veranstaltung bereits erfasst.",
                    "success": None,
                    "event_year": get_event_year(active_event),
                    "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
                },
            )

    try:
        race_class = determine_race_class(
            birth_year=birth_year,
            event_year=get_event_year(active_event),
        )
    except ValueError as error:
        return templates.TemplateResponse(
            request=request,
            name="participant_form.html",
            context={
                "active_event": active_event,
                "error": str(error),
                "success": None,
                "event_year": get_event_year(active_event),
                "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
            },
        )

    registration_group = get_registration_group_for_class(
        race_class=race_class,
        event_id=active_event.id,
        db=db,
    )

    if registration_group.released:
        return templates.TemplateResponse(
            request=request,
            name="participant_form.html",
            context={
                "active_event": active_event,
                "error": "Registrierung nach Nennschluss nicht möglich.",
                "success": None,
                "event_year": get_event_year(active_event),
                "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
            },
        )

    clean_first_name = first_name.strip()
    clean_last_name = last_name.strip()
    clean_club = clean_optional_form_value(club)
    clean_team = clean_optional_form_value(team)
    clean_adac_number = clean_optional_form_value(adac_number)
    clean_youth_license_number = clean_optional_form_value(youth_license_number)
    clean_youth_card_qr_url = clean_optional_form_value(youth_card_qr_url)
    clean_youth_card_valid_until = clean_optional_form_value(youth_card_valid_until)
    clean_youth_card_checked = parse_form_bool(youth_card_checked)
    clean_youth_card_checked_at = get_youth_card_checked_at(
        clean_youth_card_checked
    )

    if driver_profile_id:
        driver_profile = db.query(DriverProfile).filter(
            DriverProfile.id == driver_profile_id,
            DriverProfile.active == True,
        ).first()

        if not driver_profile:
            return templates.TemplateResponse(
                request=request,
                name="participant_form.html",
                context={
                    "active_event": active_event,
                    "error": "Ausgewählter Fahrer wurde nicht gefunden.",
                    "success": None,
                    "event_year": get_event_year(active_event),
                    "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
                },
            )

        driver_profile.default_start_number = start_number
        driver_profile.first_name = clean_first_name
        driver_profile.last_name = clean_last_name
        driver_profile.birth_year = birth_year
        driver_profile.club = clean_club
        driver_profile.team = clean_team
        driver_profile.adac_number = clean_adac_number
        driver_profile.youth_license_number = clean_youth_license_number
        driver_profile.youth_card_qr_url = clean_youth_card_qr_url
        driver_profile.youth_card_valid_until = clean_youth_card_valid_until
        driver_profile.youth_card_checked = clean_youth_card_checked
        driver_profile.youth_card_checked_at = get_youth_card_checked_at(
            checked=clean_youth_card_checked,
            existing_checked_at=driver_profile.youth_card_checked_at,
        )
        db.flush()
    else:
        driver_profile = find_or_create_driver_profile(
            db=db,
            first_name=clean_first_name,
            last_name=clean_last_name,
            birth_year=birth_year,
            start_number=start_number,
            club=clean_club,
            team=clean_team,
            adac_number=clean_adac_number,
            youth_license_number=clean_youth_license_number,
            youth_card_qr_url=clean_youth_card_qr_url,
            youth_card_valid_until=clean_youth_card_valid_until,
            youth_card_checked=clean_youth_card_checked,
            youth_card_checked_at=clean_youth_card_checked_at,
        )

    participant = Participant(
        event_id=active_event.id,
        driver_profile_id=driver_profile.id,
        start_number=start_number,
        first_name=clean_first_name,
        last_name=clean_last_name,
        birth_year=birth_year,
        race_class=race_class,
        club=clean_club,
        team=clean_team,
        adac_number=clean_adac_number,
        youth_license_number=clean_youth_license_number,
        youth_card_qr_url=clean_youth_card_qr_url,
        youth_card_valid_until=clean_youth_card_valid_until,
        youth_card_checked=clean_youth_card_checked,
        youth_card_checked_at=clean_youth_card_checked_at,
        checked_in=False,
        withdrawn=False,
        withdrawn_reason=None,
        withdrawn_at=None,
        withdrawn_by=None,
        active=True,
    )

    db.add(participant)
    db.commit()

    return templates.TemplateResponse(
        request=request,
        name="participant_form.html",
        context={
            "active_event": active_event,
            "error": None,
            "success": f"Teilnehmer wurde gespeichert und Klasse {race_class} zugeordnet.",
            "event_year": get_event_year(active_event),
            "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
        },
    )


@app.post("/ui/participants/new")
def ui_create_participant_legacy(
    request: Request,
    start_number: int = Form(...),
    driver_profile_id: int | None = Form(None),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_year: int = Form(...),
    club: str = Form(""),
    team: str = Form(""),
    adac_number: str = Form(""),
    youth_license_number: str = Form(""),
    youth_card_qr_url: str = Form(""),
    youth_card_valid_until: str = Form(""),
    youth_card_checked: str = Form("false"),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_create_participant(
        event_id=active_event.id,
        request=request,
        start_number=start_number,
        driver_profile_id=driver_profile_id,
        first_name=first_name,
        last_name=last_name,
        birth_year=birth_year,
        club=club,
        team=team,
        adac_number=adac_number,
        youth_license_number=youth_license_number,
        youth_card_qr_url=youth_card_qr_url,
        youth_card_valid_until=youth_card_valid_until,
        youth_card_checked=youth_card_checked,
        db=db,
    )


@app.get("/ui/events/{event_id}/participants/{participant_id}/edit")
def ui_event_edit_participant(
    event_id: int,
    participant_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    settings = get_app_settings(db)

    if active_event.closed:
        return RedirectResponse(
            url=f"/ui/events/{event_id}/participants",
            status_code=303,
        )

    participant = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.id == participant_id,
    ).first()

    if not participant:
        raise HTTPException(
            status_code=404,
            detail="Teilnehmer nicht gefunden",
        )

    return templates.TemplateResponse(
        request=request,
        name="participant_edit.html",
        context={
            "active_event": active_event,
            "participant": participant,
            "event_year": get_event_year(active_event),
            "error": None,
            "youth_card_scan_enabled": settings.get("youth_card_scan_enabled") == "true",
        },
    )


@app.post("/ui/events/{event_id}/participants/{participant_id}/edit")
def ui_event_update_participant(
    event_id: int,
    participant_id: int,
    request: Request,
    start_number: int = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_year: int = Form(...),
    club: str = Form(""),
    team: str = Form(""),
    adac_number: str = Form(""),
    youth_license_number: str = Form(""),
    youth_card_qr_url: str = Form(""),
    youth_card_valid_until: str = Form(""),
    youth_card_checked: str = Form("false"),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)

    participant = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.id == participant_id,
    ).first()

    if not participant:
        raise HTTPException(
            status_code=404,
            detail="Teilnehmer nicht gefunden",
        )

    existing = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.start_number == start_number,
        Participant.id != participant_id,
    ).first()

    if existing:
        return templates.TemplateResponse(
            request=request,
            name="participant_edit.html",
            context={
                "active_event": active_event,
                "participant": participant,
                "event_year": get_event_year(active_event),
                "error": "Diese Startnummer ist bereits vergeben.",
                "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
            },
        )

    try:
        race_class = determine_race_class(
            birth_year=birth_year,
            event_year=get_event_year(active_event),
        )
    except ValueError as error:
        return templates.TemplateResponse(
            request=request,
            name="participant_edit.html",
            context={
                "active_event": active_event,
                "participant": participant,
                "event_year": get_event_year(active_event),
                "error": str(error),
                "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
            },
        )

    old_race_class = participant.race_class
    new_race_class = race_class

    if old_race_class != new_race_class:
        registration_group = get_registration_group_for_class(
            race_class=new_race_class,
            event_id=active_event.id,
            db=db,
        )

        if registration_group.released:
            return templates.TemplateResponse(
                request=request,
                name="participant_edit.html",
                context={
                    "active_event": active_event,
                    "participant": participant,
                    "event_year": get_event_year(active_event),
                    "error": "Registrierung nach Nennschluss nicht möglich.",
                    "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
                },
            )

    clean_first_name = first_name.strip()
    clean_last_name = last_name.strip()
    clean_club = clean_optional_form_value(club)
    clean_team = clean_optional_form_value(team)
    clean_adac_number = clean_optional_form_value(adac_number)
    clean_youth_license_number = clean_optional_form_value(youth_license_number)
    clean_youth_card_qr_url = clean_optional_form_value(youth_card_qr_url)
    clean_youth_card_valid_until = clean_optional_form_value(youth_card_valid_until)
    clean_youth_card_checked = parse_form_bool(youth_card_checked)
    clean_youth_card_checked_at = get_youth_card_checked_at(
        checked=clean_youth_card_checked,
        existing_checked_at=participant.youth_card_checked_at,
    )

    driver_profile = find_or_create_driver_profile(
        db=db,
        first_name=clean_first_name,
        last_name=clean_last_name,
        birth_year=birth_year,
        start_number=start_number,
        club=clean_club,
        team=clean_team,
        adac_number=clean_adac_number,
        youth_license_number=clean_youth_license_number,
        youth_card_qr_url=clean_youth_card_qr_url,
        youth_card_valid_until=clean_youth_card_valid_until,
        youth_card_checked=clean_youth_card_checked,
        youth_card_checked_at=clean_youth_card_checked_at,
    )

    participant.driver_profile_id = driver_profile.id
    participant.start_number = start_number
    participant.first_name = clean_first_name
    participant.last_name = clean_last_name
    participant.birth_year = birth_year
    participant.race_class = race_class
    participant.club = clean_club
    participant.team = clean_team
    participant.adac_number = clean_adac_number
    participant.youth_license_number = clean_youth_license_number
    participant.youth_card_qr_url = clean_youth_card_qr_url
    participant.youth_card_valid_until = clean_youth_card_valid_until
    participant.youth_card_checked = clean_youth_card_checked
    participant.youth_card_checked_at = clean_youth_card_checked_at

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/participants",
        status_code=303,
    )


@app.post("/ui/participants/{participant_id}/edit")
def ui_update_participant_legacy(
    participant_id: int,
    request: Request,
    start_number: int = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_year: int = Form(...),
    club: str = Form(""),
    team: str = Form(""),
    adac_number: str = Form(""),
    youth_license_number: str = Form(""),
    youth_card_qr_url: str = Form(""),
    youth_card_valid_until: str = Form(""),
    youth_card_checked: str = Form("false"),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_update_participant(
        event_id=active_event.id,
        participant_id=participant_id,
        request=request,
        start_number=start_number,
        first_name=first_name,
        last_name=last_name,
        birth_year=birth_year,
        club=club,
        team=team,
        adac_number=adac_number,
        youth_license_number=youth_license_number,
        youth_card_qr_url=youth_card_qr_url,
        youth_card_valid_until=youth_card_valid_until,
        youth_card_checked=youth_card_checked,
        db=db,
    )


@app.post("/ui/events/{event_id}/participants/{participant_id}/delete")
def ui_event_delete_participant(
    event_id: int,
    participant_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)

    participant = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.id == participant_id,
    ).first()

    if participant:
        delete_linked_registrations_for_participant_before_start_release(
            db=db,
            participant=participant,
        )
        db.query(TeamMember).filter(
            TeamMember.event_id == active_event.id,
            TeamMember.participant_id == participant.id,
        ).delete()
        db.delete(participant)
        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/participants",
        status_code=303,
    )


@app.post("/ui/participants/{participant_id}/delete")
def ui_delete_participant_legacy(
    participant_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_delete_participant(
        event_id=active_event.id,
        participant_id=participant_id,
        db=db,
    )


@app.get("/ui/driver-profiles")
def ui_driver_profiles_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/driver-profiles")


@app.get("/ui/events/{event_id}/driver-profiles")
def ui_event_driver_profiles(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    driver_profiles = db.query(DriverProfile).filter(
        DriverProfile.active == True,
    ).order_by(
        DriverProfile.last_name,
        DriverProfile.first_name,
        DriverProfile.birth_year,
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="driver_profiles.html",
        context={
            "active_event": active_event,
            "driver_profiles": driver_profiles,
        },
    )


@app.get("/ui/events/{event_id}/help")
def ui_event_help_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    require_event(event_id, db)
    return RedirectResponse(url="/ui/help", status_code=303)


@app.get("/ui/events/{event_id}/start-approval")
def ui_event_start_approval_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/registration",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/race")
def ui_event_race_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/team-results")
def ui_event_team_results_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/teams",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/driver-database")
def ui_event_driver_database_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/driver-profiles",
        status_code=303,
    )


@app.get("/ui/events/{event_id}/driver-profiles/{driver_profile_id}/edit")
def ui_event_edit_driver_profile(
    event_id: int,
    driver_profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    driver_profile = db.query(DriverProfile).filter(
        DriverProfile.id == driver_profile_id,
        DriverProfile.active == True,
    ).first()

    if not driver_profile:
        raise HTTPException(
            status_code=404,
            detail="Teilnehmerdatensatz nicht gefunden",
        )

    settings = get_app_settings(db)

    return templates.TemplateResponse(
        request=request,
        name="driver_profile_edit.html",
        context={
            "active_event": active_event,
            "driver_profile": driver_profile,
            "event_year": get_event_year(active_event),
            "error": None,
            "youth_card_scan_enabled": settings.get("youth_card_scan_enabled") == "true",
        },
    )


@app.post("/ui/events/{event_id}/driver-profiles/{driver_profile_id}/edit")
def ui_event_update_driver_profile(
    event_id: int,
    driver_profile_id: int,
    request: Request,
    default_start_number: str = Form(""),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_year: int = Form(...),
    club: str = Form(""),
    adac_number: str = Form(""),
    youth_license_number: str = Form(""),
    youth_card_qr_url: str = Form(""),
    youth_card_valid_until: str = Form(""),
    youth_card_checked: str = Form("false"),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    driver_profile = db.query(DriverProfile).filter(
        DriverProfile.id == driver_profile_id,
        DriverProfile.active == True,
    ).first()

    if not driver_profile:
        raise HTTPException(
            status_code=404,
            detail="Teilnehmerdatensatz nicht gefunden",
        )

    try:
        determine_race_class(
            birth_year=birth_year,
            event_year=get_event_year(active_event),
        )
    except ValueError as error:
        return templates.TemplateResponse(
            request=request,
            name="driver_profile_edit.html",
            context={
                "active_event": active_event,
                "driver_profile": driver_profile,
                "event_year": get_event_year(active_event),
                "error": str(error),
                "youth_card_scan_enabled": get_app_settings(db).get("youth_card_scan_enabled") == "true",
            },
        )

    clean_default_start_number = clean_optional_form_value(default_start_number)

    driver_profile.default_start_number = (
        int(clean_default_start_number)
        if clean_default_start_number
        else None
    )
    driver_profile.first_name = first_name.strip()
    driver_profile.last_name = last_name.strip()
    driver_profile.birth_year = birth_year
    driver_profile.club = clean_optional_form_value(club)
    driver_profile.team = None
    driver_profile.adac_number = clean_optional_form_value(adac_number)
    driver_profile.youth_license_number = clean_optional_form_value(youth_license_number)
    driver_profile.youth_card_qr_url = clean_optional_form_value(youth_card_qr_url)
    driver_profile.youth_card_valid_until = clean_optional_form_value(youth_card_valid_until)
    driver_profile.youth_card_checked = parse_form_bool(youth_card_checked)
    driver_profile.youth_card_checked_at = get_youth_card_checked_at(
        checked=driver_profile.youth_card_checked,
        existing_checked_at=driver_profile.youth_card_checked_at,
    )

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/driver-profiles",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/driver-profiles/{driver_profile_id}/delete")
def ui_event_delete_driver_profile(
    event_id: int,
    driver_profile_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    driver_profile = db.query(DriverProfile).filter(
        DriverProfile.id == driver_profile_id,
        DriverProfile.active == True,
    ).first()

    if driver_profile:
        driver_profile.active = False
        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/driver-profiles",
        status_code=303,
    )


@app.post("/ui/events/{event_id}/participants/{participant_id}/withdraw")
def ui_event_withdraw_participant(
    event_id: int,
    participant_id: int,
    withdrawn_reason: str = Form(...),
    withdrawn_by: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)

    valid_reasons = [
        "Verletzung",
        "Disqualifizierung",
        "Aufgabe",
        "Entscheidung Rennleitung",
    ]

    if withdrawn_reason not in valid_reasons:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Ausscheidegrund",
        )

    updated = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.id == participant_id,
    ).update(
        {
            Participant.withdrawn: True,
            Participant.withdrawn_reason: withdrawn_reason,
            Participant.withdrawn_at: now_string(),
            Participant.withdrawn_by: withdrawn_by.strip() or None,
        },
        synchronize_session=False,
    )

    if updated == 0:
        raise HTTPException(
            status_code=404,
            detail="Teilnehmer nicht gefunden",
        )

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/participants",
        status_code=303,
    )


@app.post("/ui/participants/{participant_id}/withdraw")
def ui_withdraw_participant_legacy(
    participant_id: int,
    withdrawn_reason: str = Form(...),
    withdrawn_by: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_withdraw_participant(
        event_id=active_event.id,
        participant_id=participant_id,
        withdrawn_reason=withdrawn_reason,
        withdrawn_by=withdrawn_by,
        db=db,
    )


@app.post("/ui/events/{event_id}/participants/{participant_id}/withdraw/revoke")
def ui_event_revoke_withdraw_participant(
    event_id: int,
    participant_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_open(active_event)

    updated = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.id == participant_id,
    ).update(
        {
            Participant.withdrawn: False,
            Participant.withdrawn_reason: None,
            Participant.withdrawn_at: None,
            Participant.withdrawn_by: None,
        },
        synchronize_session=False,
    )

    if updated == 0:
        raise HTTPException(
            status_code=404,
            detail="Teilnehmer nicht gefunden",
        )

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/participants",
        status_code=303,
    )


@app.post("/ui/participants/{participant_id}/withdraw/revoke")
def ui_revoke_withdraw_participant_legacy(
    participant_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_revoke_withdraw_participant(
        event_id=active_event.id,
        participant_id=participant_id,
        db=db,
    )


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
        "portrait"
        if form.get("display_orientation") == "portrait"
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


def reset_active_run_hardware(event_id: int, db: Session) -> None:
    active_run_id = None
    active_run_id_str = get_setting(db, "startdisplay_active_run_id")

    if active_run_id_str and active_run_id_str.isdigit():
        active_run_id = int(active_run_id_str)

    if active_run_id is None:
        return

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == event_id,
        RaceRun.id == active_run_id,
    ).first()

    if not race_run:
        return

    race_run.traffic_light_status = "off"
    race_run.false_start_lane = "none"
    race_run.last_reset_at = now_string()
    race_run.last_reset_by = "Zeitmessanlage"
    race_run.reset_count = (race_run.reset_count or 0) + 1

    if race_run.status in ["running", "started"]:
        race_run.status = "ready"

    db.commit()


@app.post("/ui/events/{event_id}/timekeeper/class/{race_class}/reset")
def ui_event_timekeeper_reset(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

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

LOGO_DEFAULT_SETTINGS = {
    "logo_enabled": "false",
    "logo_simulation_mode": "false",
    "logo_ip": "192.168.2.20",
    "logo_port": "502",
    "logo_timeout_ms": "1000",
    "logo_poll_interval_ms": "500",
    "logo_access_mode": "modbus_tcp",
    "logo_password": "",

    "logo_time_register_start": "0",
    "logo_output_coil_start": "8192",
    "logo_marker_coil_start": "8256",
    "logo_web_start_coil": "8262",
    "logo_web_reset_coil": "8264",
    "logo_pulse_duration": "0.2",
    "logo_pulse_duration_ms": "200",

    "logo_start_range": "132",
    "logo_start_address": "24",

    "logo_reset_range": "132",
    "logo_reset_address": "32",

    "logo_time_lane_1_range": "19",
    "logo_time_lane_1_address": "0",

    "logo_time_lane_2_range": "19",
    "logo_time_lane_2_address": "16",

    "logo_ready_range": "130",
    "logo_ready_address": "0",

    "logo_go_range": "130",
    "logo_go_address": "1",

    "logo_attention_range": "130",
    "logo_attention_address": "2",

    "logo_light_barrier_1_range": "131",
    "logo_light_barrier_1_address": "0",

    "logo_light_barrier_2_range": "131",
    "logo_light_barrier_2_address": "1",

    "logo_error_range": "131",
    "logo_error_address": "2",

    "logo_false_start_1_range": "131",
    "logo_false_start_1_address": "3",

    "logo_false_start_2_range": "131",
    "logo_false_start_2_address": "4",

    "logo_last_check_at": "",
    "logo_last_check_ok": "",
    "logo_last_error": "",
}

YOUTH_CARD_DEFAULT_SETTINGS = {
    "youth_card_scan_enabled": "false",
}

CLUB_LOGO_DEFAULT_SETTINGS = {
    "club_logo_path": "",
    "club_logo_original_filename": "",
    "club_logo_uploaded_at": "",
}

STARTDISPLAY_DEFAULT_SETTINGS = {
    "startdisplay_enabled": "false",
    "startdisplay_host": "",
    "startdisplay_port": "80",
    "startdisplay_endpoint": "/display",
    "startdisplay_last_send": "",
    "startdisplay_last_success": "",
    "startdisplay_last_error": "",
    "startdisplay_last_lane_a": "",
    "startdisplay_last_lane_b": "",
    "startdisplay_last_payload": "",
    "startdisplay_last_test_payload": "",
}


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


def parse_logo_poll_interval_ms(settings: dict | None) -> int:
    settings = settings or {}

    try:
        interval_ms = int(str(settings.get("logo_poll_interval_ms") or "200").strip())
    except (TypeError, ValueError):
        interval_ms = 200

    return 500


def build_empty_logo_status(enabled: bool, message: str, ok: bool = False) -> dict:
    return {
        "enabled": enabled,
        "ok": ok,
        "online": False,
        "message": message,
        "times": {
            "lane_a_raw": None,
            "lane_b_raw": None,
            "lane_a_centiseconds": None,
            "lane_b_centiseconds": None,
            "lane_a_ms": None,
            "lane_b_ms": None,
            "lane_a_seconds": None,
            "lane_b_seconds": None,
            "lane_a_display": None,
            "lane_b_display": None,
        },
        "outputs": {
            "yellow": False,
            "green": False,
            "red": False,
        },
        "markers": {
            "lane_a_running": False,
            "lane_b_running": False,
            "reset_available": False,
            "false_start_a": False,
            "false_start_b": False,
            "light_barrier_fault": False,
        },
    }


class LogoStatusCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._client = None
        self._client_key = None
        self._settings: dict = dict(LOGO_DEFAULT_SETTINGS)
        self._poll_interval_ms = parse_logo_poll_interval_ms(self._settings)
        self._status = self._with_cache_metadata(
            build_empty_logo_status(False, "LOGO-Status wird geladen.", ok=True),
            source="cache-init",
        )

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="logo-status-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        with self._client_lock:
            self._drop_client_locked()

    def get_status(self) -> dict:
        with self._lock:
            status = copy.deepcopy(self._status)

        cached_at_monotonic = status.get("_cached_at_monotonic")
        if isinstance(cached_at_monotonic, (int, float)):
            status["cache_age_ms"] = round((time.monotonic() - cached_at_monotonic) * 1000)

        status.pop("_cached_at_monotonic", None)
        return status

    def _run(self):
        settings_refresh_after = 0.0

        while not self._stop_event.is_set():
            loop_started_at = time.monotonic()
            settings = self._settings

            if loop_started_at >= settings_refresh_after:
                settings = self._load_settings()
                settings_refresh_after = loop_started_at + 5.0

            poll_interval_ms = parse_logo_poll_interval_ms(settings)

            try:
                logo_client = build_logo_client(settings)

                if not logo_client.config.enabled:
                    with self._client_lock:
                        self._drop_client_locked()
                    status = build_empty_logo_status(False, "LOGO ist deaktiviert.", ok=True)
                else:
                    with self._client_lock:
                        client = self._get_or_connect_locked(logo_client, settings)
                        status = self._read_status_with_client(logo_client, client)
            except Exception as error:
                with self._client_lock:
                    self._drop_client_locked()

                status = self._error_status(settings, error)

            self._store_status(
                status=status,
                settings=settings,
                poll_interval_ms=poll_interval_ms,
            )
            self._stop_event.wait(poll_interval_ms / 1000)

    def pulse_web_start(self, settings: dict) -> dict:
        return self._pulse_web_coil(settings, "start", "WEB Start")

    def pulse_web_reset(self, settings: dict) -> dict:
        return self._pulse_web_coil(settings, "reset", "WEB Reset")

    def _pulse_web_coil(self, settings: dict, coil_name: str, label: str) -> dict:
        logo_client = build_logo_client(settings)

        if not logo_client.config.enabled:
            return build_empty_logo_status(False, "LOGO ist deaktiviert.", ok=True)

        coil_address = (
            logo_client.config.web_start_coil
            if coil_name == "start"
            else logo_client.config.web_reset_coil
        )

        if coil_address is None:
            return {
                "enabled": True,
                "ok": False,
                "online": False,
                "message": f"{label}-Schreibadresse ist nicht konfiguriert.",
            }

        try:
            with self._client_lock:
                client = self._get_or_connect_locked(logo_client, settings)
                self._write_coil_or_raise(client, coil_address, True)
                time.sleep(logo_client.config.pulse_duration)
                self._write_coil_or_raise(client, coil_address, False)

            return {
                "enabled": True,
                "ok": True,
                "online": True,
                "message": f"{label}-Impuls gesendet.",
                "coil": coil_address,
            }
        except Exception as error:
            with self._client_lock:
                self._drop_client_locked()

            return {
                "enabled": True,
                "ok": False,
                "online": False,
                "message": f"{label}-Impuls fehlgeschlagen: {error}",
                "coil": coil_address,
            }

    def _get_or_connect_locked(self, logo_client, settings: dict):
        next_client_key = self._connection_key(settings)

        if self._client is not None and self._client_key != next_client_key:
            self._drop_client_locked()

        if self._client is None:
            self._client = logo_client._connect()
            self._client_key = next_client_key

        return self._client

    @staticmethod
    def _write_coil_or_raise(client, coil_address: int, value: bool):
        response = client.write_coil(coil_address, value)

        if response is None or getattr(response, "isError", lambda: True)():
            raise RuntimeError("Modbus-Schreibantwort ist fehlerhaft.")

    def _drop_client_locked(self):
        if self._client is not None:
            self._close_client(self._client)
            self._client = None
            self._client_key = None

    @staticmethod
    def _connection_key(settings: dict) -> tuple:
        return (
            str(settings.get("logo_enabled", "")).strip().lower(),
            str(settings.get("logo_ip", "")).strip(),
            str(settings.get("logo_port", "")).strip(),
            str(settings.get("logo_timeout_ms", "")).strip(),
            str(settings.get("logo_time_register_start", "")).strip(),
            str(settings.get("logo_output_coil_start", "")).strip(),
            str(settings.get("logo_marker_coil_start", "")).strip(),
        )

    @staticmethod
    def _close_client(client):
        try:
            client.close()
        except Exception:
            pass

    @staticmethod
    def _read_status_with_client(logo_client, client) -> dict:
        return {
            "enabled": True,
            "ok": True,
            "online": True,
            "message": "LOGO-Status gelesen.",
            "times": logo_client.read_times(client),
            "outputs": logo_client.read_outputs(client),
            "markers": logo_client.read_markers(client),
        }

    @staticmethod
    def _error_status(settings: dict, error: Exception) -> dict:
        return build_empty_logo_status(
            enabled=str(settings.get("logo_enabled", "")).strip().lower() == "true",
            ok=False,
            message=f"LOGO-Status konnte nicht gelesen werden: {error}",
        )

    def _load_settings(self) -> dict:
        db = SessionLocal()
        try:
            return get_logo_settings(db)
        except Exception as error:
            self._store_status(
                status=build_empty_logo_status(
                    False,
                    f"LOGO-Einstellungen konnten nicht geladen werden: {error}",
                ),
                settings=dict(LOGO_DEFAULT_SETTINGS),
                poll_interval_ms=parse_logo_poll_interval_ms(LOGO_DEFAULT_SETTINGS),
            )
            return dict(LOGO_DEFAULT_SETTINGS)
        finally:
            db.close()

    def _store_status(self, status: dict, settings: dict, poll_interval_ms: int):
        with self._lock:
            self._settings = dict(settings)
            self._poll_interval_ms = poll_interval_ms
            self._status = self._with_cache_metadata(status, source="poller")

    def _with_cache_metadata(self, status: dict, source: str) -> dict:
        payload = copy.deepcopy(status)
        payload["cached"] = True
        payload["cache_source"] = source
        payload["cached_at"] = datetime.now().isoformat(timespec="seconds")
        payload["_cached_at_monotonic"] = time.monotonic()
        payload["poll_interval_ms"] = self._poll_interval_ms
        return payload


logo_status_cache = LogoStatusCache()


@app.on_event("startup")
def start_logo_status_cache():
    logo_status_cache.start()


@app.on_event("shutdown")
def stop_logo_status_cache():
    logo_status_cache.stop()


def get_logo_status_payload(db: Session | None = None) -> dict:
    return logo_status_cache.get_status()


LOGO_RUN_DB_SYNC_LOCK = threading.Lock()
LOGO_RUN_DB_SYNC_STATE: dict[int, dict] = {}
LOGO_RUN_DB_SYNC_MIN_INTERVAL_SECONDS = 1.5


def should_sync_logo_run_times(run_id: int, lane_a_ms, lane_b_ms) -> bool:
    now = time.monotonic()
    values = (lane_a_ms, lane_b_ms)

    with LOGO_RUN_DB_SYNC_LOCK:
        state = LOGO_RUN_DB_SYNC_STATE.get(run_id)

        if state:
            if state.get("values") == values:
                return False

            if now - state.get("synced_at", 0) < LOGO_RUN_DB_SYNC_MIN_INTERVAL_SECONDS:
                return False

        LOGO_RUN_DB_SYNC_STATE[run_id] = {
            "values": values,
            "synced_at": now,
        }
        return True


def sync_logo_times_for_run(
    race_run: RaceRun,
    db: Session,
    status: dict | None = None,
) -> dict:
    if status is None:
        status = get_logo_status_payload(db)

    if not (
        status.get("ok")
        and not race_run.confirmed
        and race_run.status in ["running", "started"]
    ):
        return status

    lane_a_result, lane_b_result = get_lane_results_for_run(race_run)
    repeat_scope = get_run_repeat_scope(db, race_run.id)
    times = status.get("times", {})
    lane_a_ms = times.get("lane_a_ms")
    lane_b_ms = times.get("lane_b_ms")
    should_commit = should_sync_logo_run_times(
        run_id=race_run.id,
        lane_a_ms=lane_a_ms,
        lane_b_ms=lane_b_ms,
    )

    if not should_commit:
        return status

    dirty = False

    if (
        is_lane_editable_for_repeat_scope("lane_a", repeat_scope)
        and isinstance(lane_a_ms, int)
        and lane_a_ms > 0
        and lane_a_result
    ):
        lane_a_result.raw_time_ms = lane_a_ms
        race_run.lane_a_time_ms = lane_a_ms
        dirty = True

    if (
        is_lane_editable_for_repeat_scope("lane_b", repeat_scope)
        and isinstance(lane_b_ms, int)
        and lane_b_ms > 0
        and lane_b_result
    ):
        lane_b_result.raw_time_ms = lane_b_ms
        race_run.lane_b_time_ms = lane_b_ms
        dirty = True

    if dirty:
        db.commit()
        db.refresh(race_run)

    return status


def pulse_logo_web_start(db: Session) -> dict:
    return logo_status_cache.pulse_web_start(get_logo_settings(db))


def pulse_logo_web_reset(db: Session) -> dict:
    return logo_status_cache.pulse_web_reset(get_logo_settings(db))


def pulse_logo_web_start_background():
    db = SessionLocal()
    try:
        pulse_logo_web_start(db)
    finally:
        db.close()


def pulse_logo_web_reset_background():
    db = SessionLocal()
    try:
        pulse_logo_web_reset(db)
    finally:
        db.close()


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


# Startdisplay Service Functions
def send_start_display_numbers(lane_a_number: int | None, lane_b_number: int | None) -> bool:
    """Send start numbers to ESP32 display"""
    db = SessionLocal()
    try:
        # Get settings
        enabled = get_setting(db, "startdisplay_enabled", "false")
        if enabled != "true":
            return True  # Not an error, just not enabled
        
        host = get_setting(db, "startdisplay_host", "").strip()
        if not host:
            return True  # Not configured
        
        port = get_setting(db, "startdisplay_port", "80").strip()
        endpoint = get_setting(db, "startdisplay_endpoint", "/display").strip()
        
        # Build URL
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        
        url = f"http://{host}:{port}{endpoint}"
        
        payload = {
            "mode": "start_numbers" if lane_a_number is not None or lane_b_number is not None else "idle",
            "lane_a_start_number": lane_a_number,
            "lane_b_start_number": lane_b_number,
        }
        
        # Save status
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_setting(db, "startdisplay_last_send", now)
        set_setting(db, "startdisplay_last_lane_a", str(lane_a_number) if lane_a_number is not None else "")
        set_setting(db, "startdisplay_last_lane_b", str(lane_b_number) if lane_b_number is not None else "")
        # Send request
        import httpx
        import json
        headers = {"Content-Type": "application/json"}
        data = json.dumps(payload)
        
        with httpx.Client(timeout=1.0) as client:
            response = client.post(url, data=data, headers=headers)
            
            if response.status_code == 200:
                set_setting(db, "startdisplay_last_success", now)
                set_setting(db, "startdisplay_last_error", "")
                set_setting(db, "startdisplay_last_payload", f"A:{lane_a_number if lane_a_number is not None else '--'} B:{lane_b_number if lane_b_number is not None else '--'}")
                return True
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                set_setting(db, "startdisplay_last_error", error_msg)
                return False
                
    except httpx.TimeoutException:
        error_msg = "Timeout after 1s"
        set_setting(db, "startdisplay_last_error", error_msg)
        set_setting(db, "startdisplay_last_payload", f"A:{lane_a_number if lane_a_number is not None else '--'} B:{lane_b_number if lane_b_number is not None else '--'}")
        return False
    except httpx.ConnectError:
        error_msg = "Connection failed"
        set_setting(db, "startdisplay_last_error", error_msg)
        set_setting(db, "startdisplay_last_payload", f"A:{lane_a_number if lane_a_number is not None else '--'} B:{lane_b_number if lane_b_number is not None else '--'}")
        return False
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        set_setting(db, "startdisplay_last_error", error_msg)
        return False
    finally:
        db.commit()
        db.close()


def send_start_display_for_run(event_id: int, run_id: int) -> bool:
    """Send start numbers of a specific run to the display"""
    db = SessionLocal()
    try:
        race_run = db.query(RaceRun).filter(
            RaceRun.event_id == event_id,
            RaceRun.id == run_id,
        ).first()

        if not race_run:
            return False

        lane_a_result, lane_b_result = get_lane_results_for_run(race_run)

        lane_a_number = None
        lane_b_number = None

        if lane_a_result and lane_a_result.participant and not lane_a_result.participant.withdrawn:
            lane_a_number = lane_a_result.participant.start_number

        if lane_b_result and lane_b_result.participant and not lane_b_result.participant.withdrawn:
            lane_b_number = lane_b_result.participant.start_number

        return send_start_display_numbers(lane_a_number, lane_b_number)
    finally:
        db.close()


def get_club_logo_path() -> str:
    db = SessionLocal()

    try:
        return get_club_logo_path_from_db(db)
    finally:
        db.close()


def get_club_logo_file_path_from_db(db: Session) -> str | None:
    logo_path = get_club_logo_path_from_db(db)

    if not logo_path:
        return None

    relative_path = logo_path.lstrip("/")

    if not relative_path.startswith("static/"):
        return None

    file_path = Path("backend") / relative_path

    if not file_path.exists():
        return None

    return str(file_path)


templates.env.globals["get_club_logo_path"] = get_club_logo_path


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


def format_storage_size(size_bytes: int) -> str:
    size_gib = size_bytes / (1024 ** 3)
    return f"{size_gib:.1f} GB"


def get_system_storage_status() -> dict:
    usage = shutil.disk_usage("/")
    used = usage.total - usage.free

    if usage.total > 0:
        used_percent = round((used / usage.total) * 100)
    else:
        used_percent = 0

    return {
        "mount": "/",
        "total": format_storage_size(usage.total),
        "used": format_storage_size(used),
        "free": format_storage_size(usage.free),
        "used_percent": used_percent,
    }


def read_cpu_times() -> tuple[int, int]:
    with open("/proc/stat", "r", encoding="utf-8") as stat_file:
        values = stat_file.readline().split()[1:]

    cpu_values = [int(value) for value in values]
    idle = cpu_values[3] + cpu_values[4]
    total = sum(cpu_values)

    return idle, total


def get_cpu_usage_percent() -> int | None:
    try:
        idle_before, total_before = read_cpu_times()
        time.sleep(0.1)
        idle_after, total_after = read_cpu_times()
    except OSError:
        return None

    total_delta = total_after - total_before
    idle_delta = idle_after - idle_before

    if total_delta <= 0:
        return None

    return round((1 - (idle_delta / total_delta)) * 100)


def get_cpu_temperature() -> str:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as temp_file:
            raw_temperature = temp_file.read().strip()
    except OSError:
        return "-"

    if not raw_temperature:
        return "-"

    temperature_celsius = int(raw_temperature) / 1000
    return f"{temperature_celsius:.1f} °C"


def get_memory_status() -> dict:
    values = {}

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as meminfo_file:
            for line in meminfo_file:
                key, raw_value = line.split(":", 1)
                values[key] = int(raw_value.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {
            "total": "-",
            "used": "-",
            "free": "-",
            "used_percent": "-",
        }

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = total - available

    if total > 0:
        used_percent = round((used / total) * 100)
    else:
        used_percent = 0

    return {
        "total": format_storage_size(total),
        "used": format_storage_size(used),
        "free": format_storage_size(available),
        "used_percent": used_percent,
    }


def read_text_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except OSError:
        return None


def get_ip_command() -> str | None:
    ip_command = shutil.which("ip")

    if ip_command:
        return ip_command

    for path in ["/usr/sbin/ip", "/sbin/ip", "/bin/ip", "/usr/bin/ip"]:
        if os.path.exists(path):
            return path

    return None


def get_interface_ipv4_address(interface_name: str) -> str:
    ip_command = get_ip_command()

    if not ip_command:
        return "-"

    try:
        result = subprocess.run(
            [ip_command, "-o", "-4", "addr", "show", "dev", interface_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "-"

    if result.returncode != 0:
        return "-"

    for line in result.stdout.splitlines():
        parts = line.split()

        if "inet" in parts:
            inet_index = parts.index("inet")

            try:
                return parts[inet_index + 1].split("/")[0]
            except IndexError:
                return "-"

    return "-"


def get_host_ipv4_addresses() -> list[str]:
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    addresses = []

    for raw_address in result.stdout.split():
        address = raw_address.strip()

        if "." in address and not address.startswith("127."):
            addresses.append(address)

    return addresses


def get_primary_ipv4_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1)
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
    except OSError:
        return None

    if address and not address.startswith("127."):
        return address

    return None


def list_network_interfaces() -> list[str]:
    try:
        interfaces = os.listdir("/sys/class/net")
    except OSError:
        return []

    return sorted(interface for interface in interfaces if interface != "lo")


def is_wireless_interface(interface_name: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{interface_name}/wireless")


def get_default_route_interface() -> str | None:
    ip_command = get_ip_command()

    if ip_command:
        try:
            result = subprocess.run(
                [ip_command, "route", "show", "default"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None

        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()

                if "dev" in parts:
                    try:
                        return parts[parts.index("dev") + 1]
                    except IndexError:
                        pass

    try:
        with open("/proc/net/route", "r", encoding="utf-8") as route_file:
            for line in route_file.readlines()[1:]:
                parts = line.split()

                if len(parts) > 2 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        return None

    return None


def get_wifi_signal_strength(interface_name: str) -> str:
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8") as wireless_file:
            lines = wireless_file.readlines()
    except OSError:
        return "-"

    for line in lines:
        line = line.strip()

        if not line.startswith(f"{interface_name}:"):
            continue

        values = line.replace(".", "").split()

        try:
            link_quality = float(values[2])
        except (IndexError, ValueError):
            return "-"

        signal_percent = max(0, min(100, round((link_quality / 70) * 100)))
        return f"{signal_percent}%"

    return "-"


def get_network_interface_status(interface_name: str, label: str, wireless: bool = False) -> dict:
    mac_address = read_text_file(f"/sys/class/net/{interface_name}/address") or "-"
    operstate = read_text_file(f"/sys/class/net/{interface_name}/operstate") or "-"
    carrier = read_text_file(f"/sys/class/net/{interface_name}/carrier")
    ip_address = get_interface_ipv4_address(interface_name)

    link_connected = carrier == "1" or operstate == "up"
    connected = ip_address != "-"

    return {
        "name": interface_name,
        "label": label,
        "mac_address": mac_address,
        "ip_address": ip_address,
        "connected": connected,
        "link_connected": link_connected,
        "status": "Verbunden" if connected else "Nicht verbunden",
        "signal_strength": get_wifi_signal_strength(interface_name) if wireless else "-",
    }


def get_empty_network_interface_status(interface_name: str, label: str) -> dict:
    return {
        "name": interface_name,
        "label": label,
        "mac_address": "-",
        "ip_address": "-",
        "connected": False,
        "link_connected": False,
        "status": "Nicht verbunden",
        "signal_strength": "-",
    }


def choose_network_interface_status(
    statuses: list[dict],
    default_interface: str | None,
    fallback_name: str,
    label: str,
    wireless: bool = False,
) -> dict:
    for status in statuses:
        if status["name"] == default_interface:
            return status

    for status in statuses:
        if status["connected"]:
            return status

    for status in statuses:
        if status["name"] == fallback_name:
            return status

    if statuses:
        return statuses[0]

    return get_empty_network_interface_status(fallback_name, label)


def get_network_status() -> dict:
    interfaces = list_network_interfaces()
    default_interface = get_default_route_interface()
    ethernet_interfaces = [
        interface for interface in interfaces
        if not is_wireless_interface(interface)
    ]
    wifi_interfaces = [
        interface for interface in interfaces
        if is_wireless_interface(interface)
    ]

    ethernet_statuses = [
        get_network_interface_status(interface, "Ethernet")
        for interface in ethernet_interfaces
    ]
    wifi_statuses = [
        get_network_interface_status(interface, "WLAN", wireless=True)
        for interface in wifi_interfaces
    ]

    ethernet = choose_network_interface_status(
        statuses=ethernet_statuses,
        default_interface=default_interface,
        fallback_name="eth0",
        label="Ethernet",
    )
    wifi = choose_network_interface_status(
        statuses=wifi_statuses,
        default_interface=default_interface,
        fallback_name="wlan0",
        label="WLAN",
        wireless=True,
    )

    host_ipv4_addresses = get_host_ipv4_addresses()
    primary_ipv4_address = get_primary_ipv4_address()

    if primary_ipv4_address and primary_ipv4_address not in host_ipv4_addresses:
        host_ipv4_addresses.insert(0, primary_ipv4_address)

    if (
        default_interface == ethernet["name"]
        and ethernet["ip_address"] == "-"
        and host_ipv4_addresses
    ):
        ethernet["ip_address"] = host_ipv4_addresses[0]
        ethernet["connected"] = True
        ethernet["status"] = "Verbunden"
    elif (
        default_interface == wifi["name"]
        and wifi["ip_address"] == "-"
        and host_ipv4_addresses
    ):
        wifi["ip_address"] = host_ipv4_addresses[0]
        wifi["connected"] = True
        wifi["status"] = "Verbunden"
    elif (
        ethernet["link_connected"]
        and ethernet["ip_address"] == "-"
        and host_ipv4_addresses
    ):
        ethernet["ip_address"] = host_ipv4_addresses[0]
        ethernet["connected"] = True
        ethernet["status"] = "Verbunden"
    elif (
        wifi["link_connected"]
        and wifi["ip_address"] == "-"
        and host_ipv4_addresses
        and not ethernet["link_connected"]
    ):
        wifi["ip_address"] = host_ipv4_addresses[0]
        wifi["connected"] = True
        wifi["status"] = "Verbunden"

    if ethernet["connected"]:
        active_connection = "Ethernet"
    elif wifi["connected"]:
        active_connection = "WLAN"
    else:
        active_connection = "Nicht verbunden"

    return {
        "active_connection": active_connection,
        "ethernet": ethernet,
        "wifi": wifi,
    }


def get_system_status() -> dict:
    current_time = datetime.now()

    return {
        "current_time": current_time.strftime("%d.%m.%Y %H:%M:%S"),
        "current_time_input": current_time.strftime("%Y-%m-%dT%H:%M"),
        "storage": get_system_storage_status(),
        "cpu_usage_percent": get_cpu_usage_percent(),
        "cpu_temperature": get_cpu_temperature(),
        "memory": get_memory_status(),
        "network": get_network_status(),
    }


def restart_quad_race_service_delayed():
    time.sleep(1)

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


def parse_system_time_input(value: str | None) -> datetime:
    cleaned_value = (value or "").strip()

    for date_format in [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]:
        try:
            return datetime.strptime(cleaned_value, date_format)
        except ValueError:
            continue

    raise ValueError("Ungültiges Zeitformat")


def format_system_command_error(command_label: str, result: subprocess.CompletedProcess) -> str:
    raw_message = (result.stderr or result.stdout or "").strip()
    normalized_message = raw_message.lower()

    if "password is required" in normalized_message or "a terminal is required" in normalized_message:
        return (
            f"{command_label} darf ohne Passwort nicht ausgeführt werden. "
            "Bitte sudoers für den Dienst prüfen."
        )

    if "not in the sudoers" in normalized_message or "not allowed" in normalized_message:
        return (
            f"{command_label} ist für den Dienst nicht freigegeben. "
            "Bitte sudoers für den Dienst prüfen."
        )

    if "no new privileges" in normalized_message:
        return (
            f"{command_label} kann in dieser Laufzeitumgebung nicht mit sudo ausgeführt werden."
        )

    if "operation not permitted" in normalized_message:
        return (
            f"{command_label} wurde vom Betriebssystem verweigert. "
            "Bitte Berechtigungen zum Setzen der Systemzeit prüfen."
        )

    if raw_message:
        return f"{command_label} fehlgeschlagen: {raw_message}"

    return f"{command_label} fehlgeschlagen (Exit-Code {result.returncode})."


def run_system_time_command(command: list[str], command_label: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, f"{command_label} hat zu lange gedauert."
    except OSError as error:
        return False, f"{command_label} konnte nicht gestartet werden: {error}"

    if result.returncode != 0:
        return False, format_system_command_error(command_label, result)

    return True, ""


def set_system_time(target_time: datetime) -> tuple[bool, str]:
    if target_time.year < 2020 or target_time.year > 2099:
        return False, "Jahr außerhalb des erlaubten Bereichs."

    formatted_time = target_time.strftime("%Y-%m-%d %H:%M:%S")

    run_system_time_command(
        ["sudo", "-n", "timedatectl", "set-ntp", "false"],
        "Automatische Zeitsynchronisation deaktivieren",
    )

    set_attempts = [
        (
            ["sudo", "-n", "timedatectl", "set-time", formatted_time],
            "Systemzeit setzen (timedatectl)",
        ),
        (
            ["sudo", "-n", "date", "-s", formatted_time],
            "Systemzeit setzen (date)",
        ),
    ]
    error_messages = []

    for command, command_label in set_attempts:
        ok, message = run_system_time_command(command, command_label)

        if ok:
            run_system_time_command(
                ["sudo", "-n", "hwclock", "-w"],
                "Hardware-Uhr speichern",
            )

            return True, formatted_time

        error_messages.append(message)

    return False, " / ".join(error_messages)


def remove_existing_club_logos():
    CLUB_LOGO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for logo_file in CLUB_LOGO_UPLOAD_DIR.glob("club_logo.*"):
        if logo_file.is_file():
            logo_file.unlink(missing_ok=True)


def clear_club_logo_settings(db: Session):
    set_setting(db, "club_logo_path", "")
    set_setting(db, "club_logo_original_filename", "")
    set_setting(db, "club_logo_uploaded_at", "")


def validate_club_logo_upload(file: UploadFile, file_bytes: bytes) -> str:
    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()

    if extension not in CLUB_LOGO_ALLOWED_EXTENSIONS:
        raise ValueError("Ungültiger Dateityp. Erlaubt sind PNG, JPG und JPEG.")

    if len(file_bytes) > CLUB_LOGO_MAX_SIZE_BYTES:
        raise ValueError("Die Datei ist zu groß. Maximal erlaubt sind 2 MB.")

    if not file_bytes:
        raise ValueError("Die Datei ist leer.")

    return extension


def validate_database_upload(file: UploadFile, file_bytes: bytes) -> str:
    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()

    if extension not in DATABASE_IMPORT_ALLOWED_EXTENSIONS:
        raise ValueError("Ungültiger Dateityp. Erlaubt sind SQLite-Datenbanken.")

    if len(file_bytes) > DATABASE_IMPORT_MAX_SIZE_BYTES:
        raise ValueError("Die Datei ist zu groß.")

    if not file_bytes:
        raise ValueError("Die Datei ist leer.")

    return extension


def validate_sqlite_database(path: Path) -> bool:
    try:
        connection = sqlite3.connect(path)

        try:
            integrity_result = connection.execute(
                "PRAGMA integrity_check;"
            ).fetchone()

            if not integrity_result or integrity_result[0] != "ok":
                return False

            table_rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
            table_names = {row[0] for row in table_rows}
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return False

    return REQUIRED_DATABASE_TABLES.issubset(table_names)


def get_database_backup_items() -> list[dict]:
    DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_items = []

    for backup_file in sorted(
        DATABASE_BACKUP_DIR.glob("*.sqlite"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:5]:
        stat = backup_file.stat()
        backup_items.append(
            {
                "name": backup_file.name,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M:%S"),
                "size_mb": f"{stat.st_size / (1024 * 1024):.1f} MB",
            }
        )

    return backup_items


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

from backend.export_routes import router as export_router
app.include_router(export_router)

from backend.results_routes import router as results_router
app.include_router(results_router)

from backend.marshal_routes import router as marshal_router
app.include_router(marshal_router)
