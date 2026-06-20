from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.export_logic import build_class_export_rows, build_overall_rows
from backend.models import Event, Participant, Pairing, Team
from backend.results_logic import (
    build_class_results,
    build_compact_round_results,
    build_round_tie_warnings,
    build_tie_warnings,
    get_round_key_config,
)
from backend.statistics_logic import build_result_statistics
from backend.team_logic import build_team_standings
from backend.template_helpers import register_template_helpers
from backend.round_logic import (
    ROUND_PRELIMINARY,
    ROUND_SEMIFINAL,
    ROUND_FINAL,
    is_round_complete,
)


router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")
register_template_helpers(templates)
EVENT_YEAR = datetime.now().year


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


def get_round_status(
    pairings: list[Pairing],
    round_name: str,
) -> str:
    if is_round_complete(
        pairings=pairings,
        round_name=round_name,
    ):
        return "Fertig"

    return "Offen"


# ------------------------------------------------------------
# Legacy-Weiterleitungen
# ------------------------------------------------------------

@router.get("/ui/results")
def ui_results_overview_redirect(
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/results",
        status_code=303,
    )


@router.get("/ui/results/class/{race_class}")
def ui_results_class_redirect(
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
        url=f"/ui/events/{active_event.id}/results/class/{race_class}",
        status_code=303,
    )


@router.get("/ui/results/class/{race_class}/round/{round_key}")
def ui_results_class_round_redirect(
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
        url=f"/ui/events/{active_event.id}/results/class/{race_class}/round/{round_key}",
        status_code=303,
    )


# ------------------------------------------------------------
# Ergebnisse je Veranstaltung
# ------------------------------------------------------------

@router.get("/ui/events/{event_id}/results")
def ui_event_results_overview(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    classes = []
    adac_overall_rows = []
    adac_overall_rows_by_class = {}
    tie_warnings = []

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

        pairings_count = len(pairings)
        rows_by_round = build_class_export_rows(pairings)
        overall_rows = build_overall_rows(rows_by_round)
        overall_rows_count = len(
            [
                row
                for row in overall_rows
                if row.get("place") not in ["", None]
            ]
        )
        adac_overall_rows_by_class[race_class] = overall_rows
        tie_warnings.extend(
            build_tie_warnings(overall_rows, race_class)
        )

        for row in overall_rows:
            adac_overall_rows.append(
                {
                    **row,
                    "race_class": race_class,
                }
            )

        classes.append(
            {
                "race_class": race_class,
                "participants_count": participants_count,
                "pairings_count": pairings_count,
                "overall_rows_count": overall_rows_count,
                "quarterfinal_status": get_round_status(
                    pairings=pairings,
                    round_name=ROUND_PRELIMINARY,
                ),
                "semifinal_status": get_round_status(
                    pairings=pairings,
                    round_name=ROUND_SEMIFINAL,
                ),
                "final_status": get_round_status(
                    pairings=pairings,
                    round_name=ROUND_FINAL,
                ),
            }
        )

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
    result_statistics = build_result_statistics(
        event=active_event,
        adac_overall_rows=adac_overall_rows,
        db=db,
    )

    return templates.TemplateResponse(
        request=request,
        name="results_overview.html",
        context={
            "active_event": active_event,
            "classes": classes,
            "adac_overall_rows": adac_overall_rows,
            "adac_overall_rows_by_class": adac_overall_rows_by_class,
            "team_standings": team_standings,
            "result_statistics": result_statistics,
            "tie_warnings": tie_warnings,
        },
    )


@router.get("/ui/events/{event_id}/results/class/{race_class}")
def ui_event_results_class(
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

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    result_rows = build_class_results(
        participants=participants,
        pairings=pairings,
    )
    tie_warnings = build_tie_warnings(result_rows, race_class)

    return templates.TemplateResponse(
        request=request,
        name="results_class.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "result_rows": result_rows,
            "tie_warnings": tie_warnings,
            "print_mode": False,
        },
    )


@router.get("/ui/events/{event_id}/results/class/{race_class}/round/{round_key}")
def ui_event_results_class_round(
    event_id: int,
    race_class: int,
    round_key: str,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    round_config = get_round_key_config(round_key)

    if not round_config:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Runde",
        )

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    result_rows = build_compact_round_results(
        participants=participants,
        pairings=pairings,
        round_key=round_key,
    )
    tie_warnings = build_round_tie_warnings(
        result_rows=result_rows,
        race_class=race_class,
        round_label=round_config["label"],
    )

    return templates.TemplateResponse(
        request=request,
        name="results_round.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "round_key": round_key,
            "round_label": round_config["label"],
            "result_rows": result_rows,
            "tie_warnings": tie_warnings,
            "event_year": EVENT_YEAR,
            "print_mode": False,
        },
    )


@router.get("/ui/events/{event_id}/print/class/{race_class}/overall")
def ui_event_print_results_class(
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

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    result_rows = build_class_results(
        participants=participants,
        pairings=pairings,
    )
    tie_warnings = build_tie_warnings(result_rows, race_class)

    return templates.TemplateResponse(
        request=request,
        name="results_class.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "result_rows": result_rows,
            "tie_warnings": tie_warnings,
            "print_mode": True,
        },
    )


@router.get("/ui/events/{event_id}/print/class/{race_class}/round/{round_key}")
def ui_event_print_results_class_round(
    event_id: int,
    race_class: int,
    round_key: str,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    round_config = get_round_key_config(round_key)

    if not round_config:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Runde",
        )

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    result_rows = build_compact_round_results(
        participants=participants,
        pairings=pairings,
        round_key=round_key,
    )
    tie_warnings = build_round_tie_warnings(
        result_rows=result_rows,
        race_class=race_class,
        round_label=round_config["label"],
    )

    return templates.TemplateResponse(
        request=request,
        name="results_round.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "round_key": round_key,
            "round_label": round_config["label"],
            "result_rows": result_rows,
            "tie_warnings": tie_warnings,
            "event_year": EVENT_YEAR,
            "print_mode": True,
        },
    )
