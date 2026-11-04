from backend.models import DriverProfile
from backend.models import Event
from fastapi import HTTPException
from backend.models import Participant
from backend.models import RaceRun
from backend.models import Registration
from backend.models import RegistrationAccessToken
from backend.models import RegistrationGroup
from backend.models import RegistrationNotification
from backend.models import RegistrationProtocol
from backend.models import RegistrationSignature
from sqlalchemy.orm import Session
from backend.common import now_string, parse_event_date
from backend.constants import REGISTRATION_GROUPS


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
