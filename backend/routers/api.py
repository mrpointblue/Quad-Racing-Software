from fastapi import APIRouter
from fastapi import Depends
from backend.models import DriverProfile
from fastapi import HTTPException
from backend.models import Pairing
from backend.schemas import PairingResponse
from backend.models import Participant
from backend.schemas import ParticipantCreate
from backend.schemas import ParticipantResponse
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.common import determine_race_class, ensure_event_is_open, get_active_event, get_event_year, require_active_event, require_event
from backend.application.registration_service import find_or_create_driver_profile, get_registration_group_for_class


router = APIRouter()


@router.get("/api/drivers/search")
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

@router.get("/api/events/{event_id}/participants/driver-profile/{driver_profile_id}/exists")
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

@router.get("/api/participants/driver-profile/{driver_profile_id}/exists")
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

@router.get("/api/events/{event_id}/participants/start-number/{start_number}/exists")
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

@router.get("/api/participants/start-number/{start_number}/exists")
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

@router.get("/api/events/{event_id}/participants/birth-year/{birth_year}/registration-open")
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

@router.get("/api/participants/birth-year/{birth_year}/registration-open")
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

@router.post("/participants", response_model=ParticipantResponse)
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

@router.get("/participants", response_model=list[ParticipantResponse])
def list_participants(db: Session = Depends(get_db)):
    active_event = require_active_event(db)

    return db.query(Participant).filter(
        Participant.event_id == active_event.id
    ).order_by(
        Participant.race_class,
        Participant.start_number,
    ).all()

@router.get("/participants/class/{race_class}", response_model=list[ParticipantResponse])
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

@router.get("/pairings/class/{race_class}", response_model=list[PairingResponse])
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
