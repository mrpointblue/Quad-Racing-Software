from fastapi import APIRouter
from fastapi import Depends
from backend.models import Participant
from fastapi.responses import RedirectResponse
from fastapi import Request
from sqlalchemy.orm import Session
from backend.models import Team
from backend.models import TeamMember
from backend.database import get_db
from backend.common import ensure_event_is_open, get_event_year, now_string, redirect_to_active_event_path, require_event
from backend.application.teams import build_team_standings
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/teams")
def ui_teams_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/teams")

@router.get("/ui/events/{event_id}/teams")
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

@router.post("/ui/events/{event_id}/teams/new")
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

@router.post("/ui/events/{event_id}/teams/{team_id}/delete")
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
