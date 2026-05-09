from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.export_logic import build_class_export_rows, build_overall_rows
from backend.models import Event, Participant, Pairing, RunResult, Team
from backend.results_logic import (
    build_class_results,
    build_compact_round_results,
    format_time_ms,
    get_round_key_config,
)
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


def get_penalty_sum(result: RunResult) -> int:
    return (
        (result.sector_1_penalty or 0)
        + (result.sector_2_penalty or 0)
        + (result.sector_3_penalty or 0)
        + (result.sector_4_penalty or 0)
        + (result.sector_5_penalty or 0)
        + (result.sector_6_penalty or 0)
        + (result.sector_7_penalty or 0)
        + (result.sector_8_penalty or 0)
        + (result.sector_9_penalty or 0)
        + (result.sector_10_penalty or 0)
        + (result.general_penalty or 0)
    )


def format_result_driver(result: RunResult) -> str:
    participant = result.participant

    if not participant:
        return "-"

    return f"#{participant.start_number} {participant.first_name} {participant.last_name}"


def format_result_run(result: RunResult) -> str:
    race_run = result.race_run

    if not race_run:
        return "-"

    round_labels = {
        "vorlauf": "Viertelfinale",
        ROUND_PRELIMINARY: "Viertelfinale",
        ROUND_SEMIFINAL: "Halbfinale",
        ROUND_FINAL: "Finale",
    }
    round_numbers = {
        "vorlauf": 1,
        ROUND_PRELIMINARY: 1,
        ROUND_SEMIFINAL: 2,
        ROUND_FINAL: 3,
    }
    lane_label = "Bahn 1" if result.lane == "lane_a" else "Bahn 2"

    return (
        f"{round_labels.get(race_run.round_name, race_run.round_name)} "
        f"· Kl. {race_run.race_class} "
        f"· Lauf {round_numbers.get(race_run.round_name, 0)}.{race_run.run_number} "
        f"· {lane_label}"
    )


def build_result_statistics(
    active_event: Event,
    adac_overall_rows: list[dict],
    db: Session,
) -> dict:
    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
    ).all()
    timed_results = [
        result
        for result in results
        if result.raw_time_ms is not None
        and result.participant is not None
        and not result.participant.withdrawn
    ]
    penalty_results = [
        result
        for result in results
        if result.participant is not None
        and not result.participant.withdrawn
    ]
    best_driver = None

    if adac_overall_rows:
        best_row = sorted(
            adac_overall_rows,
            key=lambda row: (
                row.get("total_ms") if row.get("total_ms") is not None else 999999999,
                row.get("total_penalty", 0),
                row.get("start_number", 999999),
            ),
        )[0]
        best_driver = {
            "driver": best_row.get("driver_name", "-"),
            "race_class": best_row.get("race_class", "-"),
            "value": best_row.get("total_display", "-"),
            "detail": f"Platz {best_row.get('place', '-')}",
        }

    fastest_lane_1 = None
    fastest_lane_2 = None

    for lane, target in [
        ("lane_a", "fastest_lane_1"),
        ("lane_b", "fastest_lane_2"),
    ]:
        lane_results = [
            result
            for result in timed_results
            if result.lane == lane
        ]

        if not lane_results:
            continue

        result = min(
            lane_results,
            key=lambda item: (
                item.raw_time_ms,
                item.participant.start_number if item.participant else 999999,
            ),
        )
        row = {
            "driver": format_result_driver(result),
            "value": format_time_ms(result.raw_time_ms),
            "detail": format_result_run(result),
        }

        if target == "fastest_lane_1":
            fastest_lane_1 = row
        else:
            fastest_lane_2 = row

    most_penalties = None
    fewest_penalties = None

    if penalty_results:
        most_result = max(
            penalty_results,
            key=lambda item: (
                get_penalty_sum(item),
                item.participant.start_number if item.participant else 0,
            ),
        )
        fewest_result = min(
            penalty_results,
            key=lambda item: (
                get_penalty_sum(item),
                item.participant.start_number if item.participant else 999999,
            ),
        )
        most_penalties = {
            "driver": format_result_driver(most_result),
            "value": f"{get_penalty_sum(most_result)} FP",
            "detail": format_result_run(most_result),
        }
        fewest_penalties = {
            "driver": format_result_driver(fewest_result),
            "value": f"{get_penalty_sum(fewest_result)} FP",
            "detail": format_result_run(fewest_result),
        }

    return {
        "best_driver": best_driver,
        "fastest_lane_1": fastest_lane_1,
        "fastest_lane_2": fastest_lane_2,
        "most_penalties": most_penalties,
        "fewest_penalties": fewest_penalties,
    }


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
        adac_overall_rows_by_class[race_class] = overall_rows

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
        active_event=active_event,
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

    return templates.TemplateResponse(
        request=request,
        name="results_class.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "result_rows": result_rows,
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

    return templates.TemplateResponse(
        request=request,
        name="results_round.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "round_key": round_key,
            "round_label": round_config["label"],
            "result_rows": result_rows,
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

    return templates.TemplateResponse(
        request=request,
        name="results_class.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "result_rows": result_rows,
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

    return templates.TemplateResponse(
        request=request,
        name="results_round.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "round_key": round_key,
            "round_label": round_config["label"],
            "result_rows": result_rows,
            "event_year": EVENT_YEAR,
            "print_mode": True,
        },
    )
