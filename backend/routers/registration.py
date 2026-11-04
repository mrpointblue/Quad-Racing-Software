from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from sqlalchemy.exc import IntegrityError
from backend.models import Participant
from fastapi.responses import RedirectResponse
from backend.models import Registration
from fastapi import Request
from sqlalchemy.orm import Session
from backend.models import TeamMember
from datetime import datetime
from backend.database import get_db
from backend.services.registration_profiles import save_registration_driver_profile
from backend.common import clean_optional_form_value, determine_race_class, ensure_event_is_open, get_event_year, now_string, parse_form_bool, require_event
from backend.constants import REGISTRATION_STATUS_LABELS, REGISTRATION_STATUS_ORDER
from backend.application.registration_service import delete_registration_relations, is_registration_deletable, registration_matches_participant, require_registration, validate_registration_email
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/events/{event_id}/registrations")
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

@router.post("/ui/events/{event_id}/registrations/{registration_id}/delete")
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

@router.get("/ui/events/{event_id}/registrations/{registration_id}")
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

@router.post("/ui/events/{event_id}/registrations/{registration_id}/approve")
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

@router.post("/ui/events/{event_id}/registrations/{registration_id}/update")
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

@router.post("/ui/events/{event_id}/registrations/{registration_id}/reject")
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

@router.post("/ui/events/{event_id}/registrations/{registration_id}/correction-required")
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
