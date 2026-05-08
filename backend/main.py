import shutil
import time

from datetime import datetime, date

from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import Base, engine, get_db
from backend.template_helpers import register_template_helpers
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
    MonitorDisplaySetting,
    Team,
    TeamMember,
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
)
from backend.results_logic import build_class_results


EVENT_YEAR = datetime.now().year


app = FastAPI(
    title="Quad Parallel Race",
    version="1.0.0",
)

Base.metadata.create_all(bind=engine)


def ensure_schema_column(table_name: str, column_name: str, column_definition: str):
    with engine.connect() as connection:
        columns = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
        column_names = [column[1] for column in columns]

        if column_name not in column_names:
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )
            connection.commit()


ensure_schema_column(
    table_name="monitor_display_settings",
    column_name="display_orientation",
    column_definition="VARCHAR DEFAULT 'landscape'",
)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")
templates = Jinja2Templates(directory="backend/templates")
register_template_helpers(templates)


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
        f"Nennfreigabe erfolgt: {group.name} wurde freigegeben. "
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
) -> DriverProfile:
    first_name = first_name.strip()
    last_name = last_name.strip()

    driver_profile = db.query(DriverProfile).filter(
        DriverProfile.first_name == first_name,
        DriverProfile.last_name == last_name,
        DriverProfile.birth_year == birth_year,
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
    driver_profile.active = True

    db.flush()

    return driver_profile


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
            detail="Nennfreigabe-Gruppe nicht gefunden",
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


def get_points_table_c(place: int | None, has_result: bool) -> int:
    if not has_result or place is None:
        return 0

    return POINTS_TABLE_C.get(place, 1)


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
                    has_result=True,
                ),
                "total_display": row["total_display"],
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
                    "counted": False,
                }
            )

        member_rows.sort(
            key=lambda row: (
                -row["points"],
                row["place"] is None,
                row["place"] if row["place"] is not None else 999,
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
    if time_ms is None:
        return "—"

    total_centiseconds = round(time_ms / 10)
    minutes = total_centiseconds // 6000
    seconds = (total_centiseconds % 6000) // 100
    hundredths = total_centiseconds % 100

    return f"{minutes:02d}:{seconds:02d},{hundredths:02d}"


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
        valued_time_ms = result.raw_time_ms + (total_penalty_seconds * 1000)
    else:
        valued_time_ms = None

    result.sector_penalties = sector_penalties
    result.total_penalty_seconds = total_penalty_seconds
    result.valued_time_ms = valued_time_ms
    result.valued_time_display = format_time_ms(valued_time_ms)

    return result


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
):
    race_run.status = "created"
    race_run.confirmed = False
    race_run.traffic_light_status = "off"
    race_run.false_start_lane = "none"
    race_run.started_at = None
    race_run.finished_at = None
    race_run.lane_a_time_ms = None
    race_run.lane_b_time_ms = None
    race_run.reset_count = (race_run.reset_count or 0) + 1
    race_run.last_reset_at = now_string()
    race_run.last_reset_by = "Wiederholung"

    for result in results:
        result.raw_time_ms = None
        result.general_penalty = 0
        result.confirmed = False

        for sector in range(1, 11):
            set_penalty_for_sector(result, sector, 0)


# ------------------------------------------------------------
# Monitor-Helfer
# ------------------------------------------------------------

def get_monitor_penalty_sum(result: RunResult | None) -> int:
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


def get_monitor_valued_time_ms(result: RunResult | None) -> int | None:
    if result is None or result.raw_time_ms is None:
        return None

    if result.participant and result.participant.withdrawn:
        return None

    return result.raw_time_ms + (get_monitor_penalty_sum(result) * 1000)


def monitor_driver_name(participant: Participant | None) -> str:
    if participant is None:
        return "-"

    return f"#{participant.start_number} {participant.first_name} {participant.last_name}"


def monitor_result_payload(result: RunResult | None) -> dict:
    if result is None:
        return {
            "driver": "-",
            "raw_time": "-",
            "penalty": "-",
            "valued_time": "-",
        }

    return {
        "driver": monitor_driver_name(result.participant),
        "raw_time": format_time_ms(result.raw_time_ms),
        "penalty": get_monitor_penalty_sum(result),
        "valued_time": format_time_ms(get_monitor_valued_time_ms(result)),
    }


def monitor_run_payload(
    race_run: RaceRun,
    pairing: Pairing,
) -> dict:
    lane_a_result, lane_b_result = get_lane_results_for_run(race_run)
    round_number, round_label = get_round_display(race_run.round_name)

    return {
        "id": race_run.id,
        "race_class": race_run.race_class,
        "round_name": race_run.round_name,
        "round_label": round_label,
        "round_number": round_number,
        "pairing_number": pairing.pairing_number,
        "run_number": race_run.run_number,
        "status": race_run.status,
        "confirmed": race_run.confirmed,
        "lane_a": monitor_result_payload(lane_a_result),
        "lane_b": monitor_result_payload(lane_b_result),
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


def pick_monitor_current_run(
    run_items: list[tuple[RaceRun, Pairing]],
) -> tuple[RaceRun, Pairing] | None:
    for status in ["running", "finished", "ready"]:
        for race_run, pairing in run_items:
            if not race_run.confirmed and race_run.status == status:
                return race_run, pairing

    for race_run, pairing in run_items:
        if not race_run.confirmed:
            return race_run, pairing

    return None


def pick_monitor_next_runs(
    run_items: list[tuple[RaceRun, Pairing]],
    current_run_id: int | None,
    limit: int = 3,
) -> list[dict]:
    next_runs = []

    for race_run, pairing in run_items:
        if race_run.confirmed:
            continue

        if current_run_id is not None and race_run.id == current_run_id:
            continue

        next_runs.append(monitor_run_payload(race_run, pairing))

        if len(next_runs) >= limit:
            break

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

            valued_time_ms = get_monitor_valued_time_ms(result)

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

            existing["raw_time_ms"] += result.raw_time_ms
            existing["penalty"] += get_monitor_penalty_sum(result)
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
            row["driver"],
        )
    )

    current_class = None
    current_place = 0

    for row in rows:
        if row["race_class"] != current_class:
            current_class = row["race_class"]
            current_place = 1
        else:
            current_place += 1

        row["place"] = current_place

    return rows[:limit]


def build_monitor_payload(
    event: Event,
    db: Session,
) -> dict:
    run_items = get_monitor_runs(
        event_id=event.id,
        db=db,
    )

    current_item = pick_monitor_current_run(run_items)
    current_run_id = None

    if current_item is not None:
        current_run, current_pairing = current_item
        current_run_id = current_run.id
        current_payload = monitor_run_payload(current_run, current_pairing)
        current_round_number, _current_round_label = get_round_display(current_run.round_name)

        if current_run.round_name == ROUND_FINAL:
            current_round_number = get_round_display(ROUND_SEMIFINAL)[0]
    else:
        current_payload = None
        current_run = None
        current_round_number = None

    return {
        "event": {
            "id": event.id,
            "event_number": event.event_number,
            "name": event.name,
            "date": event.event_date or "",
            "organizer_club": event.organizer_club or "",
        },
        "server_time": now_string(),
        "current_run": current_payload,
        "next_runs": pick_monitor_next_runs(
            run_items=run_items,
            current_run_id=current_run_id,
            limit=3,
        ),
        "live_results": get_monitor_live_result_runs(
            run_items=run_items,
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
        show_team_results=False,
        display_orientation="landscape",
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
        "show_team_results": bool(setting.show_team_results),
        "display_orientation": setting.display_orientation or "landscape",
    }


def build_monitor_result_rows_for_round(
    event: Event,
    db: Session,
    race_class: int,
    round_name: str,
) -> list[dict]:
    round_names = [round_name]

    if round_name == ROUND_PRELIMINARY:
        round_names = [ROUND_PRELIMINARY, "vorlauf", "viertelfinale"]

    pairings = db.query(Pairing).filter(
        Pairing.event_id == event.id,
        Pairing.race_class == race_class,
        Pairing.round_name.in_(round_names),
    ).all()

    participant_rows = {}
    _round_number, round_label = get_round_display(round_name)

    for pairing in pairings:
        for race_run in pairing.runs:
            for result in race_run.results:
                if result.raw_time_ms is None:
                    continue

                valued_time_ms = get_monitor_valued_time_ms(result)

                if valued_time_ms is None:
                    continue

                row = participant_rows.get(result.participant_id)

                if row is None:
                    row = {
                        "place": None,
                        "driver": monitor_driver_name(result.participant),
                        "race_class": race_class,
                        "round_label": round_label,
                        "raw_time_ms": 0,
                        "penalty": 0,
                        "valued_time_ms": 0,
                    }
                    participant_rows[result.participant_id] = row

                row["raw_time_ms"] += result.raw_time_ms
                row["penalty"] += get_monitor_penalty_sum(result)
                row["valued_time_ms"] += valued_time_ms

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
            row["valued_time_ms"],
            row["driver"],
        ),
    )

    for index, row in enumerate(rows, start=1):
        row["place"] = index

    return rows


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
                    "counted": member["counted"],
                }
                for member in row["members"]
            ],
        }
        for row in standings
    ]


def build_monitor_output_payload(
    event: Event,
    db: Session,
) -> dict:
    setting = get_or_create_monitor_display_setting(event, db)
    race_payload = build_monitor_payload(event, db)

    if not setting.show_race_control:
        race_payload["current_run"] = None
        race_payload["next_runs"] = []

    race_payload["display_setting"] = monitor_display_setting_payload(setting)
    race_payload["result_sections"] = build_monitor_result_sections(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["team_results"] = build_monitor_team_rows(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["live_results"] = []

    return race_payload


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
        "event_year": EVENT_YEAR,
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

    class_counts = {}

    for race_class in [1, 2, 3, 4, 5]:
        class_counts[race_class] = db.query(Participant).filter(
            Participant.event_id == active_event.id,
            Participant.race_class == race_class,
            Participant.active == True,
        ).count()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_event": active_event,
            "total_participants": total_participants,
            "class_counts": class_counts,
            "event_year": EVENT_YEAR,
            "current_time": now_string(),
            "registration_groups": registration_groups,
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
            "error": None,
            "event_year": EVENT_YEAR,
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
    sectors_per_lane: int = Form(4),
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
                "error": "Diese Veranstaltungsnummer ist bereits vergeben.",
                "event_year": EVENT_YEAR,
            },
        )

    has_active_event = db.query(Event).filter(
        Event.active == True
    ).first() is not None

    event = Event(
        event_number=event_number,
        name=name,
        event_date=event_date.strip() or None,
        location=location.strip() or None,
        organizer_club=organizer_club.strip() or None,
        sectors_per_lane=sectors_per_lane,
        active=not has_active_event,
        closed=False,
        closed_at=None,
        closed_by=None,
        closed_reason=None,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    ensure_registration_groups(db, event.id)

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
            "error": None,
            "event_year": EVENT_YEAR,
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
    sectors_per_lane: int = Form(4),
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
                "error": "Diese Veranstaltungsnummer ist bereits vergeben.",
                "event_year": EVENT_YEAR,
            },
        )

    event.event_number = event_number
    event.name = name.strip()
    event.event_date = event_date.strip() or None
    event.location = location.strip() or None
    event.organizer_club = organizer_club.strip() or None
    event.sectors_per_lane = sectors_per_lane

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

    was_active = event.active

    event.closed = True
    event.closed_at = now_string()
    event.closed_by = closed_by.strip()
    event.closed_reason = closed_reason
    event.active = False

    db.commit()

    if was_active:
        next_event = db.query(Event).filter(
            Event.closed == False
        ).order_by(
            Event.event_date.asc(),
            Event.id.desc(),
        ).first()

        if next_event:
            next_event.active = True
            db.commit()
            ensure_registration_groups(db, next_event.id)

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

    db.query(RaceRun).filter(
        RaceRun.event_id == event.id
    ).delete()

    db.query(Pairing).filter(
        Pairing.event_id == event.id
    ).delete()

    db.query(RegistrationGroup).filter(
        RegistrationGroup.event_id == event.id
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

    was_active = event.active

    db.delete(event)
    db.commit()

    if was_active:
        next_event = db.query(Event).filter(
            Event.closed == False
        ).order_by(
            Event.event_date.asc(),
            Event.id.desc(),
        ).first()

        if next_event:
            next_event.active = True
            db.commit()
            ensure_registration_groups(db, next_event.id)

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
            "event_year": EVENT_YEAR,
        },
    )


@app.get("/ui/events/{event_id}/participants/new")
def ui_event_new_participant(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

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
            "event_year": EVENT_YEAR,
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
                "event_year": EVENT_YEAR,
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
                    "event_year": EVENT_YEAR,
                },
            )

    try:
        race_class = determine_race_class(birth_year)
    except ValueError as error:
        return templates.TemplateResponse(
            request=request,
            name="participant_form.html",
            context={
                "active_event": active_event,
                "error": str(error),
                "success": None,
                "event_year": EVENT_YEAR,
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
                "event_year": EVENT_YEAR,
            },
        )

    clean_first_name = first_name.strip()
    clean_last_name = last_name.strip()
    clean_club = club.strip() or None
    clean_team = team.strip() or None
    clean_adac_number = adac_number.strip() or None
    clean_youth_license_number = youth_license_number.strip() or None

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
                    "event_year": EVENT_YEAR,
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
            "event_year": EVENT_YEAR,
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
            "event_year": EVENT_YEAR,
            "error": None,
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
                "event_year": EVENT_YEAR,
                "error": "Diese Startnummer ist bereits vergeben.",
            },
        )

    try:
        race_class = determine_race_class(birth_year)
    except ValueError as error:
        return templates.TemplateResponse(
            request=request,
            name="participant_edit.html",
            context={
                "active_event": active_event,
                "participant": participant,
                "event_year": EVENT_YEAR,
                "error": str(error),
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
                    "event_year": EVENT_YEAR,
                    "error": "Registrierung nach Nennschluss nicht möglich.",
                },
            )

    clean_first_name = first_name.strip()
    clean_last_name = last_name.strip()
    clean_club = club.strip() or None
    clean_team = team.strip() or None
    clean_adac_number = adac_number.strip() or None
    clean_youth_license_number = youth_license_number.strip() or None

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
# Nennfreigabe je Veranstaltung
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
            "event_year": EVENT_YEAR,
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
            detail="Nennfreigabe-Gruppe nicht gefunden",
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
            detail="Nennfreigabe-Gruppe nicht gefunden",
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
            "event_year": EVENT_YEAR,
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
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    ensure_registration_groups(db, active_event.id)

    class_data = []
    runs_by_class = {}
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

        run_items = sorted(
            run_items,
            key=lambda item: (
                item["status_order"],
                item["round_number"],
                item["pairing"].pairing_number,
                item["run"].run_number,
            ),
        )

        active_run = None

        for run_item in run_items:
            if not run_item["run"].confirmed:
                active_run = run_item
                break

        active_run_by_class[race_class] = active_run
        runs_by_class[race_class] = run_items

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
            "active_run_by_class": active_run_by_class,
            "event_year": EVENT_YEAR,
            "event_date_reached": is_event_date_reached(active_event),
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
            "event_year": EVENT_YEAR,
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
    setting.display_classes = ",".join(selected_classes)
    setting.display_rounds = ",".join(selected_rounds)
    setting.show_team_results = form.get("show_team_results") == "1"
    setting.display_orientation = (
        "portrait"
        if form.get("display_orientation") == "portrait"
        else "landscape"
    )
    setting.updated_at = now_string()

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor-output",
        status_code=303,
    )


@app.get("/ui/monitor")
def ui_monitor_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/monitor")


@app.get("/ui/events/{event_id}/monitor")
def ui_event_monitor(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
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
            "event_year": EVENT_YEAR,
        },
    )


@app.get("/api/events/{event_id}/monitor")
def api_event_monitor(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
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
            "event_year": EVENT_YEAR,
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

    prepared_results = [
        prepare_result_for_display(result, sectors)
        for result in results
    ]

    run_items = get_monitor_runs(
        event_id=active_event.id,
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

    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={
            "active_event": active_event,
            "pairing": pairing,
            "race_run": race_run,
            "results": prepared_results,
            "sectors": sectors,
            "next_unconfirmed_run": next_unconfirmed_payload,
            "is_next_startable_run": is_next_startable_run,
            "start_error": request.query_params.get("start_error"),
            "event_year": EVENT_YEAR,
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
        get_monitor_runs(
            event_id=active_event.id,
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

        db.commit()

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

    if not race_run.confirmed:
        race_run.traffic_light_status = "off"
        race_run.false_start_lane = "none"
        race_run.last_reset_at = now_string()
        race_run.last_reset_by = "Zeitmessanlage"
        race_run.reset_count = (race_run.reset_count or 0) + 1

        if race_run.status == "running":
            race_run.status = "ready"

        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
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

    reset_run_values_for_repeat(
        race_run=race_run,
        results=results,
    )

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )


@app.post("/ui/timekeeper/run/{run_id}/repeat")
def ui_repeat_run_legacy(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_repeat_run(
        event_id=active_event.id,
        run_id=run_id,
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

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).all()

    for result in results:
        if result.participant and result.participant.withdrawn:
            if can_update_times:
                result.raw_time_ms = None
                result.general_penalty = 0

                for sector in range(1, 11):
                    set_penalty_for_sector(result, sector, 0)

            continue

        if can_update_times:
            raw_time_value = form.get(f"raw_time_ms_{result.id}")

            if raw_time_value is None or str(raw_time_value).strip() == "":
                result.raw_time_ms = None
            else:
                new_time_ms = max(0, int(raw_time_value))

                if new_time_ms > 0:
                    result.raw_time_ms = new_time_ms

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

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
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

    db.commit()

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
        race_class = determine_race_class(birth_year)
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
        race_class = determine_race_class(participant.birth_year)
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
    "logo_simulation_mode": "true",
    "logo_ip": "169.254.180.254",
    "logo_port": "502",
    "logo_timeout_ms": "2000",
    "logo_access_mode": "logo_web",
    "logo_password": "",

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
    "logo_last_error": "",
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

    return settings


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
        "logo_start_range",
        "logo_start_address",
        "logo_reset_range",
        "logo_reset_address",
        "logo_time_lane_1_range",
        "logo_time_lane_1_address",
        "logo_time_lane_2_range",
        "logo_time_lane_2_address",
        "logo_ready_range",
        "logo_ready_address",
        "logo_go_range",
        "logo_go_address",
        "logo_attention_range",
        "logo_attention_address",
        "logo_light_barrier_1_range",
        "logo_light_barrier_1_address",
        "logo_light_barrier_2_range",
        "logo_light_barrier_2_address",
        "logo_error_range",
        "logo_error_address",
        "logo_false_start_1_range",
        "logo_false_start_1_address",
        "logo_false_start_2_range",
        "logo_false_start_2_address",
    ]

    configuration_complete = "Ja"

    for key in required_keys:
        if not str(settings.get(key, "")).strip():
            configuration_complete = "Nein"
            break

    if settings.get("logo_enabled") != "true":
        mode = "LOGO deaktiviert"
    elif settings.get("logo_simulation_mode") == "true":
        mode = "Simulationsmodus"
    else:
        mode = "Echte LOGO-Anbindung vorbereitet"

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


def get_system_status() -> dict:
    return {
        "storage": get_system_storage_status(),
        "cpu_usage_percent": get_cpu_usage_percent(),
        "cpu_temperature": get_cpu_temperature(),
        "memory": get_memory_status(),
    }


@app.get("/ui/settings")
def ui_settings(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)
    settings = get_logo_settings(db)
    logo_status = build_logo_status(settings)
    system_status = get_system_status()

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_event": active_event,
            "settings": settings,
            "logo_status": logo_status,
            "system_status": system_status,
            "success": success,
            "error": error,
            "event_year": EVENT_YEAR,
        },
    )


@app.post("/ui/settings/logo")
def ui_save_logo_connection(
    logo_enabled: str = Form("false"),
    logo_simulation_mode: str = Form("true"),
    logo_ip: str = Form(...),
    logo_port: int = Form(...),
    logo_timeout_ms: int = Form(...),
    logo_access_mode: str = Form("logo_web"),
    logo_password: str = Form(""),
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

    if logo_access_mode not in ["logo_web", "modbus_tcp"]:
        return RedirectResponse(
            url="/ui/settings?error=Ungültige Zugriffsart",
            status_code=303,
        )

    save_logo_settings(
        db=db,
        form_values={
            "logo_enabled": logo_enabled,
            "logo_simulation_mode": logo_simulation_mode,
            "logo_ip": logo_ip.strip(),
            "logo_port": str(logo_port),
            "logo_timeout_ms": str(logo_timeout_ms),
            "logo_access_mode": logo_access_mode,
            "logo_password": logo_password.strip(),
        },
    )

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Verbindung gespeichert",
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
    settings = get_logo_settings(db)

    logo_enabled = settings.get("logo_enabled") == "true"
    simulation_mode = settings.get("logo_simulation_mode") == "true"

    if not logo_enabled:
        set_setting(db, "logo_last_check_at", now_string())
        set_setting(db, "logo_last_error", "LOGO ist deaktiviert.")
        db.commit()

        return RedirectResponse(
            url="/ui/settings?success=Statusprüfung durchgeführt",
            status_code=303,
        )

    if simulation_mode:
        set_setting(db, "logo_last_check_at", now_string())
        set_setting(db, "logo_last_error", "")
        db.commit()

        return RedirectResponse(
            url="/ui/settings?success=Simulationsmodus aktiv. Verbindungstest übersprungen.",
            status_code=303,
        )

    set_setting(db, "logo_last_check_at", now_string())
    set_setting(
        db,
        "logo_last_error",
        "Echte LOGO-Kommunikation ist vorbereitet, aber noch nicht aktiviert.",
    )
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=Statusprüfung durchgeführt",
        status_code=303,
    )


@app.post("/ui/settings/logo/reset-status")
def ui_logo_reset_status(
    db: Session = Depends(get_db),
):
    set_setting(db, "logo_last_check_at", "")
    set_setting(db, "logo_last_error", "")
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Status zurückgesetzt",
        status_code=303,
    )


# ------------------------------------------------------------
# Zusatz-Router
# ------------------------------------------------------------

from backend.export_routes import router as export_router
app.include_router(export_router)

from backend.results_routes import router as results_router
app.include_router(results_router)
