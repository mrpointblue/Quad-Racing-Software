from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from backend.models import RegistrationGroup
from fastapi import Request
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.common import ensure_event_is_active, ensure_event_is_open, get_event_year, now_string, redirect_to_active_event_path, require_active_event, require_event
from backend.application.registration_service import create_registration_notification, ensure_registration_groups, is_registration_group_race_started
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/registration")
def ui_registration_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/registration")

@router.get("/ui/events/{event_id}/registration")
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

@router.post("/ui/events/{event_id}/registration/{group_key}/release")
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

@router.post("/ui/registration/{group_key}/release")
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

@router.post("/ui/events/{event_id}/registration/{group_key}/revoke")
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

@router.post("/ui/registration/{group_key}/revoke")
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
