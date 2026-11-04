from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from backend.models import Pairing
from backend.schemas import PairingResponse
from backend.models import Participant
from backend.round_logic import ROUND_FINAL
from backend.round_logic import ROUND_PRELIMINARY
from backend.round_logic import ROUND_SEMIFINAL
from fastapi.responses import RedirectResponse
from fastapi import Request
from sqlalchemy.orm import Session
from backend.round_logic import create_final_pairings
from backend.pairing_logic import create_random_preliminary_pairings
from backend.round_logic import create_semifinal_pairings
from backend.database import get_db
from backend.round_logic import has_unresolved_ranking_ties
from backend.round_logic import is_round_complete
from backend.round_logic import round_exists
from backend.common import ensure_event_is_active, ensure_event_is_open, get_active_event, get_event_year, is_event_date_reached, redirect_to_active_event_path, require_active_event, require_event
from backend.constants import CLASS_ROUND_CONFIGS, ROUND_ORDER
from backend.application.monitor_output import build_monitor_output_payload
from backend.application.registration_service import ensure_registration_groups, get_registration_group_for_class, is_race_class_released
from backend.application.runs import get_class_round_config, get_lane_results_for_run, get_round_display, participant_start_label
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/classes")
def ui_classes_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/classes")

@router.get("/ui/events/{event_id}/classes")
def ui_event_classes(
    event_id: int,
    request: Request,
    tie_error: str | None = None,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    ensure_registration_groups(db, active_event.id)

    class_data = []
    runs_by_class = {}
    run_sections_by_class = {}
    active_run_by_class = {}

    run_status_order = {
        "running": 1,
        "finished": 2,
        "ready": 3,
        "created": 4,
        "confirmed": 9,
    }

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

        preliminary_pairings = [
            pairing for pairing in pairings
            if pairing.round_name in [ROUND_PRELIMINARY, "viertelfinale"]
        ]

        semifinal_pairings = [
            pairing for pairing in pairings
            if pairing.round_name == ROUND_SEMIFINAL
        ]

        final_pairings = [
            pairing for pairing in pairings
            if pairing.round_name == ROUND_FINAL
        ]

        group = get_registration_group_for_class(
            race_class,
            active_event.id,
            db,
        )

        preliminary_created = len(preliminary_pairings) > 0
        semifinal_created = len(semifinal_pairings) > 0
        final_created = len(final_pairings) > 0

        preliminary_complete = is_round_complete(
            pairings=pairings,
            round_name=ROUND_PRELIMINARY,
        ) or is_round_complete(
            pairings=pairings,
            round_name="viertelfinale",
        )

        semifinal_complete = is_round_complete(
            pairings=pairings,
            round_name=ROUND_SEMIFINAL,
        )

        final_complete = is_round_complete(
            pairings=pairings,
            round_name=ROUND_FINAL,
        )

        next_action_available = False

        if (
            not preliminary_created
            and participants_count > 0
            and group.released
            and is_event_date_reached(active_event)
            and not active_event.closed
        ):
            next_action_available = True

        if preliminary_complete and not semifinal_created and not active_event.closed:
            next_action_available = True

        if semifinal_complete and not final_created and not active_event.closed:
            next_action_available = True

        current_round_label = None
        current_round_count = 0

        if final_created:
            current_round_label = "Finale"
            current_round_count = len(final_pairings)
        elif semifinal_created:
            current_round_label = "Halbfinale"
            current_round_count = len(semifinal_pairings)
        elif preliminary_created:
            current_round_label = "Viertelfinale"
            current_round_count = len(preliminary_pairings)

        class_sort_order = 5

        if final_complete:
            class_sort_order = 9
        elif next_action_available:
            class_sort_order = 2
        elif preliminary_created or semifinal_created or final_created:
            class_sort_order = 3
        elif participants_count > 0:
            class_sort_order = 4

        class_data.append(
            {
                "race_class": race_class,
                "participants_count": participants_count,
                "preliminary_created": preliminary_created,
                "preliminary_count": len(preliminary_pairings),
                "preliminary_complete": preliminary_complete,
                "semifinal_created": semifinal_created,
                "semifinal_complete": semifinal_complete,
                "final_created": final_created,
                "final_complete": final_complete,
                "current_round_label": current_round_label,
                "current_round_count": current_round_count,
                "registration_released": group.released,
                "registration_group_name": group.name,
                "event_closed": active_event.closed,
                "next_action_available": next_action_available,
                "class_sort_order": class_sort_order,
            }
        )

        sorted_pairings = sorted(
            pairings,
            key=lambda pairing: (
                ROUND_ORDER.get(pairing.round_name, 99),
                pairing.pairing_number,
            ),
        )

        run_items = []

        for pairing in sorted_pairings:
            sorted_runs = sorted(pairing.runs, key=lambda run: run.run_number)

            for run in sorted_runs:
                lane_a, lane_b = get_lane_results_for_run(run)
                round_number, round_label = get_round_display(run.round_name)

                if run.confirmed:
                    status_order = 9
                else:
                    status_order = run_status_order.get(run.status, 5)

                run_items.append(
                    {
                        "pairing": pairing,
                        "run": run,
                        "lane_a": lane_a,
                        "lane_b": lane_b,
                        "round_number": round_number,
                        "round_label": round_label,
                        "status_order": status_order,
                    }
                )

        start_ordered_run_items = sorted(
            run_items,
            key=lambda item: (
                item["round_number"],
                item["pairing"].pairing_number,
                item["run"].run_number,
            ),
        )

        next_startable_run_id = None

        for run_item in start_ordered_run_items:
            if not run_item["run"].confirmed:
                next_startable_run_id = run_item["run"].id
                break

        for run_item in run_items:
            run = run_item["run"]
            run_item["is_next_startable"] = (
                next_startable_run_id is not None
                and run.id == next_startable_run_id
            )
            run_item["is_open_allowed"] = (
                run.confirmed
                or run_item["is_next_startable"]
            )

        run_items = sorted(
            run_items,
            key=lambda item: (
                item["status_order"],
                item["round_number"],
                item["pairing"].pairing_number,
                item["run"].run_number,
            ),
        )

        round_sections = []

        for round_config in CLASS_ROUND_CONFIGS:
            section_runs = [
                run_item for run_item in run_items
                if run_item["run"].round_name in round_config["round_names"]
            ]

            if not section_runs:
                continue

            section_runs = sorted(
                section_runs,
                key=lambda item: (
                    0 if item["is_next_startable"] else 1,
                    item["status_order"],
                    item["pairing"].pairing_number,
                    item["run"].run_number,
                ),
            )

            round_sections.append(
                {
                    "key": round_config["key"],
                    "label": round_config["label"],
                    "number": round_config["number"],
                    "runs": section_runs,
                    "complete": all(
                        run_item["run"].confirmed
                        for run_item in section_runs
                    ),
                }
            )

        active_run = None

        for run_item in run_items:
            if run_item["is_next_startable"]:
                active_run = run_item
                break

        active_run_by_class[race_class] = active_run
        runs_by_class[race_class] = run_items
        run_sections_by_class[race_class] = round_sections

    class_data = sorted(
        class_data,
        key=lambda item: (
            item["class_sort_order"],
            item["race_class"],
        ),
    )

    return templates.TemplateResponse(
        request=request,
        name="classes.html",
        context={
            "active_event": active_event,
            "class_data": class_data,
            "runs_by_class": runs_by_class,
            "run_sections_by_class": run_sections_by_class,
            "active_run_by_class": active_run_by_class,
            "event_year": get_event_year(active_event),
            "event_date_reached": is_event_date_reached(active_event),
            "tie_error": tie_error,
        },
    )

@router.post("/ui/events/{event_id}/classes/{race_class}/start")
def ui_event_start_class(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    if not is_event_date_reached(active_event):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if race_class not in [1, 2, 3, 4, 5]:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Klasse",
        )

    if not is_race_class_released(race_class, active_event.id, db):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/registration",
            status_code=303,
        )

    existing_pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
        Pairing.round_name.in_([ROUND_PRELIMINARY, "viertelfinale"]),
    ).count()

    if existing_pairings == 0:
        participants = db.query(Participant).filter(
            Participant.event_id == active_event.id,
            Participant.race_class == race_class,
            Participant.active == True,
            Participant.withdrawn == False,
        ).all()

        if len(participants) == 0:
            return RedirectResponse(
                url=f"/ui/events/{active_event.id}/classes",
                status_code=303,
            )

        pairings = create_random_preliminary_pairings(
            participants=participants,
            race_class=race_class,
            event_id=active_event.id,
        )

        for pairing in pairings:
            db.add(pairing)

        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )

@router.post("/ui/classes/{race_class}/start")
def ui_start_class_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_class(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )

@router.get("/ui/events/{event_id}/classes/{race_class}/round/{round_key}/pairings/print")
def ui_event_class_round_pairings_print(
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

    round_config = get_class_round_config(round_key)

    if round_config is None:
        raise HTTPException(
            status_code=400,
            detail="Ungültige Runde",
        )

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
        Pairing.round_name.in_(round_config["round_names"]),
    ).order_by(
        Pairing.pairing_number,
    ).all()

    rows = []

    for pairing in pairings:
        for race_run in sorted(pairing.runs, key=lambda run: run.run_number):
            lane_a, lane_b = get_lane_results_for_run(race_run)

            rows.append(
                {
                    "pairing_number": pairing.pairing_number,
                    "run_number": race_run.run_number,
                    "round_number": round_config["number"],
                    "lane_a": participant_start_label(
                        lane_a.participant if lane_a else None
                    ),
                    "lane_b": participant_start_label(
                        lane_b.participant if lane_b else None
                    ),
                }
            )

    return templates.TemplateResponse(
        request=request,
        name="class_pairings_print.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "round_label": round_config["label"],
            "rows": rows,
            "event_year": get_event_year(active_event),
        },
    )

@router.get("/ui/events/{event_id}/classes/pairings/print")
def ui_event_all_pairings_print(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
    ).all()

    rows = []

    for pairing in pairings:
        round_number, round_label = get_round_display(pairing.round_name)

        for race_run in sorted(pairing.runs, key=lambda run: run.run_number):
            lane_a, lane_b = get_lane_results_for_run(race_run)

            rows.append(
                {
                    "race_class": pairing.race_class,
                    "round_number": round_number,
                    "round_label": round_label,
                    "pairing_number": pairing.pairing_number,
                    "run_number": race_run.run_number,
                    "lane_a": participant_start_label(
                        lane_a.participant if lane_a else None
                    ),
                    "lane_b": participant_start_label(
                        lane_b.participant if lane_b else None
                    ),
                }
            )

    rows.sort(
        key=lambda row: (
            row["round_number"],
            row["race_class"],
            row["pairing_number"],
            row["run_number"],
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="all_pairings_print.html",
        context={
            "active_event": active_event,
            "rows": rows,
            "event_year": get_event_year(active_event),
        },
    )

@router.get("/ui/events/{event_id}/pairings-display")
def ui_event_pairings_display(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    current_active_event = get_active_event(db)

    if current_active_event is not None and current_active_event.id != active_event.id:
        return RedirectResponse(
            url=f"/ui/events/{current_active_event.id}/pairings-display",
            status_code=303,
        )

    return templates.TemplateResponse(
        request=request,
        name="pairings_display.html",
        context={
            "active_event": active_event,
            "monitor_data": build_monitor_output_payload(active_event, db),
            "event_year": get_event_year(active_event),
        },
    )

@router.get("/ui/pairings-display")
def ui_pairings_display_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/pairings-display")

@router.post("/ui/events/{event_id}/classes/{race_class}/semifinal/start")
def ui_event_start_semifinal(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
        Participant.withdrawn == False,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    if round_exists(pairings, ROUND_SEMIFINAL):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if not (
        is_round_complete(pairings, ROUND_PRELIMINARY)
        or is_round_complete(pairings, "viertelfinale")
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if has_unresolved_ranking_ties(
        participants=participants,
        pairings=pairings,
        included_rounds=[ROUND_PRELIMINARY],
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes?tie_error=semifinal",
            status_code=303,
        )

    semifinal_pairings = create_semifinal_pairings(
        event_id=active_event.id,
        race_class=race_class,
        participants=participants,
        pairings=pairings,
    )

    for pairing in semifinal_pairings:
        db.add(pairing)

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )

@router.post("/ui/classes/{race_class}/semifinal/start")
def ui_start_semifinal_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_semifinal(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )

@router.post("/ui/events/{event_id}/classes/{race_class}/final/start")
def ui_event_start_final(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    participants = db.query(Participant).filter(
        Participant.event_id == active_event.id,
        Participant.race_class == race_class,
        Participant.active == True,
        Participant.withdrawn == False,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).all()

    if round_exists(pairings, ROUND_FINAL):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if not is_round_complete(pairings, ROUND_SEMIFINAL):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes",
            status_code=303,
        )

    if has_unresolved_ranking_ties(
        participants=participants,
        pairings=pairings,
        included_rounds=[ROUND_PRELIMINARY, ROUND_SEMIFINAL],
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/classes?tie_error=final",
            status_code=303,
        )

    final_pairings = create_final_pairings(
        event_id=active_event.id,
        race_class=race_class,
        participants=participants,
        pairings=pairings,
    )

    for pairing in final_pairings:
        db.add(pairing)

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/classes",
        status_code=303,
    )

@router.post("/ui/classes/{race_class}/final/start")
def ui_start_final_legacy(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_final(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )

@router.post("/pairings/preliminary/class/{race_class}", response_model=list[PairingResponse])
def generate_preliminary_pairings(
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    ui_event_start_class(
        event_id=active_event.id,
        race_class=race_class,
        db=db,
    )

    return db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
        Pairing.round_name.in_([ROUND_PRELIMINARY, "viertelfinale"]),
    ).all()
