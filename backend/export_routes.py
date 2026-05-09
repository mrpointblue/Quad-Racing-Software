from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Event, Pairing, Participant, Team
from backend.export_logic import (
    build_all_overall_xlsx,
    build_class_xlsx,
    build_overall_xlsx,
    build_round_xlsx,
    build_team_standings_xlsx,
)
from backend.team_logic import build_team_standings


router = APIRouter()


def get_active_event(db: Session) -> Event | None:
    return db.query(Event).filter(Event.active == True).first()


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


def build_xlsx_response(
    xlsx_data: bytes,
    filename: str,
) -> Response:
    return Response(
        content=xlsx_data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


# ------------------------------------------------------------
# Legacy-Weiterleitungen / alte Export-URLs
# ------------------------------------------------------------

@router.get("/ui/export/class/{race_class}/xlsx")
def export_class_xlsx_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/export/class/{race_class}/xlsx",
        status_code=303,
    )


@router.get("/ui/export/class/{race_class}/overall/xlsx")
def export_class_overall_xlsx_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/export/class/{race_class}/overall/xlsx",
        status_code=303,
    )


@router.get("/ui/export/overall/xlsx")
def export_all_overall_xlsx_legacy(
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/export/overall/xlsx",
        status_code=303,
    )


@router.get("/ui/export/class/{race_class}/round/{round_key}/xlsx")
def export_class_round_xlsx_legacy(
    race_class: int,
    round_key: str,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/export/class/{race_class}/round/{round_key}/xlsx",
        status_code=303,
    )


# ------------------------------------------------------------
# Eventbezogene Exporte
# ------------------------------------------------------------

@router.get("/ui/events/{event_id}/export/overall/xlsx")
def export_event_all_overall_xlsx(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    pairings_by_class = {}

    for race_class in [1, 2, 3, 4, 5]:
        pairings_by_class[race_class] = db.query(Pairing).filter(
            Pairing.event_id == active_event.id,
            Pairing.race_class == race_class,
        ).all()

    teams = db.query(Team).filter(
        Team.event_id == active_event.id,
    ).order_by(
        Team.name,
    ).all()

    team_standings = build_team_standings(
        event=active_event,
        teams=teams,
        db=db,
    )

    xlsx_data = build_all_overall_xlsx(
        event=active_event,
        pairings_by_class=pairings_by_class,
        team_standings=team_standings,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"adac_gesamtwertung_alle_klassen_{timestamp}.xlsx"

    return build_xlsx_response(
        xlsx_data=xlsx_data,
        filename=filename,
    )


@router.get("/ui/events/{event_id}/export/teams/xlsx")
def export_event_team_standings_xlsx(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    teams = db.query(Team).filter(
        Team.event_id == active_event.id,
    ).order_by(
        Team.name,
    ).all()

    team_standings = build_team_standings(
        event=active_event,
        teams=teams,
        db=db,
    )

    xlsx_data = build_team_standings_xlsx(
        event=active_event,
        team_standings=team_standings,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"mannschaftswertung_{timestamp}.xlsx"

    return build_xlsx_response(
        xlsx_data=xlsx_data,
        filename=filename,
    )


@router.get("/ui/events/{event_id}/export/class/{race_class}/xlsx")
def export_event_class_xlsx(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    xlsx_data = build_class_xlsx(
        event=active_event,
        race_class=race_class,
        pairings=pairings,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"klasse_{race_class}_komplett_{timestamp}.xlsx"

    return build_xlsx_response(
        xlsx_data=xlsx_data,
        filename=filename,
    )


@router.get("/ui/events/{event_id}/export/class/{race_class}/overall/xlsx")
def export_event_class_overall_xlsx(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    xlsx_data = build_overall_xlsx(
        event=active_event,
        race_class=race_class,
        pairings=pairings,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"gesamtwertung_klasse_{race_class}_{timestamp}.xlsx"

    return build_xlsx_response(
        xlsx_data=xlsx_data,
        filename=filename,
    )


@router.get("/ui/events/{event_id}/export/class/{race_class}/round/{round_key}/xlsx")
def export_event_class_round_xlsx(
    event_id: int,
    race_class: int,
    round_key: str,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    allowed_rounds = {
        "quarterfinal": "viertelfinale",
        "semifinal": "halbfinale",
        "final": "finale",
    }

    if round_key not in allowed_rounds:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Runde",
        )

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
    ).all()

    xlsx_data = build_round_xlsx(
        event=active_event,
        race_class=race_class,
        participants=participants,
        pairings=pairings,
        round_key=round_key,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    round_filename = allowed_rounds[round_key]
    filename = f"{round_filename}_klasse_{race_class}_{timestamp}.xlsx"

    return build_xlsx_response(
        xlsx_data=xlsx_data,
        filename=filename,
    )
