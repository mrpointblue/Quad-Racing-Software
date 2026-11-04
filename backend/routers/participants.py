from fastapi import APIRouter
from fastapi import Depends
from backend.models import DriverProfile
from fastapi import Form
from fastapi import HTTPException
from backend.models import Participant
from fastapi.responses import RedirectResponse
from fastapi import Request
from sqlalchemy.orm import Session
from backend.models import TeamMember
from backend.database import get_db
from backend.common import clean_optional_form_value, determine_race_class, ensure_event_is_open, get_event_year, get_youth_card_checked_at, now_string, parse_form_bool, redirect_to_active_event_path, require_active_event, require_event
from backend.application.registration_service import delete_linked_registrations_for_participant_before_start_release, find_or_create_driver_profile, get_registration_group_for_class
from backend.application.settings_service import get_app_settings
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/participants")
def ui_participant_list_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/participants")

@router.get("/ui/participants/new")
def ui_new_participant_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/participants/new")

@router.get("/ui/participants/{participant_id}/edit")
def ui_edit_participant_redirect(
    participant_id: int,
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, f"/participants/{participant_id}/edit")

@router.get("/ui/events/{event_id}/participants")
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

@router.get("/ui/events/{event_id}/participants/new")
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

@router.post("/ui/events/{event_id}/participants/new")
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

@router.post("/ui/participants/new")
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

@router.get("/ui/events/{event_id}/participants/{participant_id}/edit")
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

@router.post("/ui/events/{event_id}/participants/{participant_id}/edit")
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

@router.post("/ui/participants/{participant_id}/edit")
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

@router.post("/ui/events/{event_id}/participants/{participant_id}/delete")
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

@router.post("/ui/participants/{participant_id}/delete")
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

@router.post("/ui/events/{event_id}/participants/{participant_id}/withdraw")
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

@router.post("/ui/participants/{participant_id}/withdraw")
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

@router.post("/ui/events/{event_id}/participants/{participant_id}/withdraw/revoke")
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

@router.post("/ui/participants/{participant_id}/withdraw/revoke")
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
