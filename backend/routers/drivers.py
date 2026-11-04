from fastapi import APIRouter
from fastapi import Depends
from backend.models import DriverProfile
from fastapi import Form
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from fastapi import Request
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.common import clean_optional_form_value, determine_race_class, get_event_year, get_youth_card_checked_at, parse_form_bool, redirect_to_active_event_path, require_event
from backend.application.settings_service import get_app_settings
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/driver-profiles")
def ui_driver_profiles_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/driver-profiles")

@router.get("/ui/events/{event_id}/driver-profiles")
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

@router.get("/ui/events/{event_id}/help")
def ui_event_help_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    require_event(event_id, db)
    return RedirectResponse(url="/ui/help", status_code=303)

@router.get("/ui/events/{event_id}/start-approval")
def ui_event_start_approval_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/registration",
        status_code=303,
    )

@router.get("/ui/events/{event_id}/race")
def ui_event_race_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )

@router.get("/ui/events/{event_id}/team-results")
def ui_event_team_results_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/teams",
        status_code=303,
    )

@router.get("/ui/events/{event_id}/driver-database")
def ui_event_driver_database_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/driver-profiles",
        status_code=303,
    )

@router.get("/ui/events/{event_id}/driver-profiles/{driver_profile_id}/edit")
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

@router.post("/ui/events/{event_id}/driver-profiles/{driver_profile_id}/edit")
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

@router.post("/ui/events/{event_id}/driver-profiles/{driver_profile_id}/delete")
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
