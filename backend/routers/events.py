from fastapi import APIRouter
from fastapi import Depends
from backend.models import Event
from fastapi import Form
from fastapi import HTTPException
from backend.models import MonitorDisplaySetting
from backend.models import Pairing
from backend.models import Participant
from backend.round_logic import ROUND_FINAL
from backend.models import RaceRun
from fastapi.responses import RedirectResponse
from backend.models import Registration
from backend.models import RegistrationGroup
from backend.models import RegistrationNotification
from fastapi import Request
from backend.models import RunPenalty
from backend.models import RunResult
from sqlalchemy.orm import Session
from backend.models import Team
from backend.models import TeamMember
from backend.database import get_db
from backend.round_logic import is_round_complete
from backend.round_logic import round_exists
from backend.common import ensure_event_is_open, get_active_event, get_event_year, now_string, require_event
from backend.application.registration_service import delete_registrations_for_event, ensure_registration_groups, get_default_registration_deadlines_for_event_date, get_registration_deadlines_for_event, normalize_registration_deadline, update_registration_group_deadlines
from backend.template_runtime import templates


router = APIRouter()


@router.get("/")
def read_root():
    return RedirectResponse(url="/ui/events")

@router.get("/health")
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

@router.get("/ui")
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

@router.get("/ui/events/{event_id}/dashboard")
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

@router.get("/ui/events")
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

@router.get("/ui/help")
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

@router.post("/ui/events/new")
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

@router.post("/ui/events/{event_id}/activate")
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

@router.get("/ui/events/{event_id}/edit")
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

@router.post("/ui/events/{event_id}/edit")
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

@router.post("/ui/events/{event_id}/close")
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

@router.post("/ui/events/{event_id}/delete")
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
