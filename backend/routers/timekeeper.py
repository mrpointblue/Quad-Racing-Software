from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from backend.models import Pairing
from backend.models import RaceRun
from fastapi.responses import RedirectResponse
from fastapi import Request
from backend.models import RunPenalty
from backend.models import RunResult
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.time_utils import parse_time_input_to_ms
from urllib.parse import quote
import threading
from backend.common import ensure_event_is_active, ensure_event_is_open, get_event_year, now_string, redirect_to_active_event_path, require_active_event, require_event
from backend.application.logo_runtime import get_logo_status_payload, parse_logo_poll_interval_ms, pulse_logo_web_reset, pulse_logo_web_start_background
from backend.application.registration_service import is_race_class_released
from backend.application.runs import build_digital_penalty_statuses, build_run_navigation_payload, get_run_repeat_scope, get_start_ordered_class_runs, is_lane_editable_for_repeat_scope, normalize_penalty_input, normalize_repeat_scope, pick_next_unconfirmed_run, prepare_result_for_display, repeat_scope_setting_key, reset_active_run_hardware, reset_run_values_for_repeat, result_has_repeatable_values, set_penalty_for_sector
from backend.application.settings_service import get_logo_settings, set_setting
from backend.application.startdisplay_dispatch import send_start_display_for_run, send_start_display_numbers
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/timekeeper/class/{race_class}")
def ui_timekeeper_class_redirect(
    race_class: int,
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, f"/timekeeper/class/{race_class}")

@router.get("/ui/events/{event_id}/timekeeper/class/{race_class}")
def ui_event_timekeeper_class(
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

    if not is_race_class_released(race_class, active_event.id, db):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/registration",
            status_code=303,
        )

    pairings = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.race_class == race_class,
    ).order_by(
        Pairing.round_name,
        Pairing.pairing_number,
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="timekeeper.html",
        context={
            "active_event": active_event,
            "race_class": race_class,
            "pairings": pairings,
            "logo_status": get_logo_status_payload(),
            "reset_notice": request.query_params.get("reset") == "1",
            "reset_error": request.query_params.get("reset_error"),
            "event_year": get_event_year(active_event),
        },
    )

@router.get("/ui/timekeeper/run/{run_id}")
def ui_timekeeper_run_redirect(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )

@router.get("/ui/events/{event_id}/timekeeper/run/{run_id}")
def ui_event_timekeeper_run(
    event_id: int,
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    pairing = db.query(Pairing).filter(
        Pairing.event_id == active_event.id,
        Pairing.id == race_run.pairing_id,
    ).first()

    if not pairing:
        raise HTTPException(
            status_code=404,
            detail="Paarung nicht gefunden",
        )

    sector_count = active_event.sectors_per_lane or 4
    sectors = list(range(1, sector_count + 1))

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).order_by(
        RunResult.lane,
    ).all()

    marshal_enabled = bool(active_event.marshal_enabled)
    run_penalties = []

    if marshal_enabled:
        run_penalties = db.query(RunPenalty).filter(
            RunPenalty.event_id == active_event.id,
            RunPenalty.run_id == race_run.id,
        ).all()

    prepared_results = []
    repeat_scope = get_run_repeat_scope(db, race_run.id)
    has_repeatable_results = any(
        result_has_repeatable_values(result)
        for result in results
    )

    for result in results:
        prepared_result = prepare_result_for_display(result, sectors)
        prepared_result.repeat_is_fixed = not is_lane_editable_for_repeat_scope(
            result.lane,
            repeat_scope,
        )
        prepared_result.digital_penalty_statuses = []
        if marshal_enabled:
            prepared_result.digital_penalty_statuses = build_digital_penalty_statuses(
                race_run=race_run,
                result=result,
                sectors=sectors,
                run_penalties=run_penalties,
            )
        prepared_results.append(prepared_result)

    run_items = get_start_ordered_class_runs(
        event_id=active_event.id,
        race_class=race_run.race_class,
        db=db,
    )
    next_unconfirmed_item = pick_next_unconfirmed_run(run_items)
    next_unconfirmed_run = None
    next_unconfirmed_payload = None

    if next_unconfirmed_item is not None:
        next_unconfirmed_run, next_unconfirmed_pairing = next_unconfirmed_item
        next_unconfirmed_payload = build_run_navigation_payload(
            race_run=next_unconfirmed_run,
            pairing=next_unconfirmed_pairing,
        )

    is_next_startable_run = (
        next_unconfirmed_run is not None
        and next_unconfirmed_run.id == race_run.id
    )

    if is_next_startable_run and not race_run.confirmed:
        set_setting(db, "startdisplay_active_event_id", str(active_event.id))
        set_setting(db, "startdisplay_active_run_id", str(race_run.id))
        set_setting(db, "startdisplay_active_set_at", now_string())
        db.commit()

        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, race_run.id),
            daemon=True,
        ).start()

    logo_settings = get_logo_settings(db)
    logo_poll_interval_ms = parse_logo_poll_interval_ms(logo_settings)
    logo_status = get_logo_status_payload()

    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={
            "active_event": active_event,
            "pairing": pairing,
            "race_run": race_run,
            "results": prepared_results,
            "sectors": sectors,
            "marshal_enabled": marshal_enabled,
            "logo_status": logo_status,
            "logo_poll_interval_ms": logo_poll_interval_ms,
            "repeat_scope": repeat_scope,
            "has_repeatable_results": has_repeatable_results,
            "next_unconfirmed_run": next_unconfirmed_payload,
            "is_next_startable_run": is_next_startable_run,
            "start_error": request.query_params.get("start_error"),
            "time_error": request.query_params.get("time_error"),
            "reset_notice": request.query_params.get("reset") == "1",
            "reset_error": request.query_params.get("reset_error"),
            "saved_notice": request.query_params.get("saved") == "1",
            "event_year": get_event_year(active_event),
        },
    )

@router.post("/ui/events/{event_id}/timekeeper/run/{run_id}/start")
def ui_event_start_run(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    next_unconfirmed_item = pick_next_unconfirmed_run(
        get_start_ordered_class_runs(
            event_id=active_event.id,
            race_class=race_run.race_class,
            db=db,
        )
    )

    if (
        next_unconfirmed_item is None
        or next_unconfirmed_item[0].id != race_run.id
    ):
        return RedirectResponse(
            url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?start_error=not-next",
            status_code=303,
        )

    if not race_run.confirmed:
        race_run.status = "running"
        race_run.traffic_light_status = "green"
        race_run.started_at = now_string()

        set_setting(db, "startdisplay_active_event_id", str(active_event.id))
        set_setting(db, "startdisplay_active_run_id", str(race_run.id))
        set_setting(db, "startdisplay_active_set_at", now_string())

        db.commit()

        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, race_run.id),
            daemon=True
        ).start()

        threading.Thread(
            target=pulse_logo_web_start_background,
            daemon=True,
        ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )

@router.post("/ui/timekeeper/run/{run_id}/start")
def ui_start_run_legacy(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_start_run(
        event_id=active_event.id,
        run_id=run_id,
        db=db,
    )

@router.post("/ui/events/{event_id}/timekeeper/run/{run_id}/reset")
def ui_event_reset_run(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    race_run.traffic_light_status = "off"
    race_run.false_start_lane = "none"
    race_run.last_reset_at = now_string()
    race_run.last_reset_by = "Zeitmessanlage"
    race_run.reset_count = (race_run.reset_count or 0) + 1

    if race_run.status in ["running", "started"]:
        race_run.status = "ready"

    db.commit()

    # Update Startdisplay
    threading.Thread(
        target=send_start_display_for_run,
        args=(active_event.id, race_run.id),
        daemon=True
    ).start()

    reset_result = pulse_logo_web_reset(db)
    reset_query = "reset=1" if reset_result.get("ok") else f"reset_error={quote(reset_result.get('message', 'Reset fehlgeschlagen'))}"

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?{reset_query}",
        status_code=303,
    )

@router.post("/ui/events/{event_id}/timekeeper/class/{race_class}/reset")
def ui_event_timekeeper_reset(
    event_id: int,
    race_class: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    reset_active_run_hardware(active_event.id, db)

    reset_result = pulse_logo_web_reset(db)
    reset_query = "reset=1" if reset_result.get("ok") else f"reset_error={quote(reset_result.get('message', 'Reset fehlgeschlagen'))}"

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/class/{race_class}?{reset_query}",
        status_code=303,
    )

@router.post("/ui/timekeeper/run/{run_id}/reset")
def ui_reset_run_legacy(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_reset_run(
        event_id=active_event.id,
        run_id=run_id,
        db=db,
    )

@router.post("/ui/events/{event_id}/timekeeper/run/{run_id}/repeat")
def ui_event_repeat_run(
    event_id: int,
    run_id: int,
    repeat_scope: str = Form("both"),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).all()
    repeat_scope = normalize_repeat_scope(repeat_scope)

    reset_run_values_for_repeat(
        race_run=race_run,
        results=results,
        repeat_scope=repeat_scope,
    )
    set_setting(db, repeat_scope_setting_key(race_run.id), repeat_scope)

    set_setting(db, "startdisplay_active_event_id", str(active_event.id))
    set_setting(db, "startdisplay_active_run_id", str(race_run.id))
    set_setting(db, "startdisplay_active_set_at", now_string())

    db.commit()

    # Update Startdisplay (Resend current run)
    threading.Thread(
        target=send_start_display_for_run,
        args=(active_event.id, race_run.id),
        daemon=True
    ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )

@router.post("/ui/timekeeper/run/{run_id}/repeat")
def ui_repeat_run_legacy(
    run_id: int,
    repeat_scope: str = Form("both"),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_repeat_run(
        event_id=active_event.id,
        run_id=run_id,
        repeat_scope=repeat_scope,
        db=db,
    )

@router.post("/ui/events/{event_id}/timekeeper/run/{run_id}/false-start")
def ui_event_set_false_start(
    event_id: int,
    run_id: int,
    false_start_lane: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    valid_values = ["none", "lane_a", "lane_b", "both"]

    if false_start_lane not in valid_values:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Fehlstartwert",
        )

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    if not race_run.confirmed:
        race_run.false_start_lane = false_start_lane
        db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )

@router.post("/ui/timekeeper/run/{run_id}/false-start")
def ui_set_false_start_legacy(
    run_id: int,
    false_start_lane: str = Form(...),
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_set_false_start(
        event_id=active_event.id,
        run_id=run_id,
        false_start_lane=false_start_lane,
        db=db,
    )

@router.post("/ui/events/{event_id}/timekeeper/run/{run_id}/save")
async def ui_event_save_run(
    event_id: int,
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    form = await request.form()
    can_update_times = not race_run.confirmed and not active_event.closed
    repeat_scope = get_run_repeat_scope(db, race_run.id)

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).all()

    for result in results:
        lane_editable = is_lane_editable_for_repeat_scope(result.lane, repeat_scope)

        if result.participant and result.participant.withdrawn:
            if can_update_times and lane_editable:
                result.raw_time_ms = None

        if can_update_times and lane_editable:
            raw_time_value = form.get(f"raw_time_ms_{result.id}")

            if raw_time_value is None or str(raw_time_value).strip() == "":
                result.raw_time_ms = None
            else:
                try:
                    result.raw_time_ms = parse_time_input_to_ms(raw_time_value)
                except ValueError:
                    return RedirectResponse(
                        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?time_error=invalid",
                        status_code=303,
                    )

        if not active_event.closed and lane_editable:
            for sector in range(1, 11):
                penalty_value = form.get(f"sector_{sector}_penalty_{result.id}")
                set_penalty_for_sector(
                    result,
                    sector,
                    normalize_penalty_input(penalty_value),
                )

            general_penalty_value = form.get(f"general_penalty_{result.id}")
            result.general_penalty = normalize_penalty_input(general_penalty_value)

    lane_a_result = next((r for r in results if r.lane == "lane_a"), None)
    lane_b_result = next((r for r in results if r.lane == "lane_b"), None)

    if can_update_times:
        race_run.lane_a_time_ms = lane_a_result.raw_time_ms if lane_a_result else None
        race_run.lane_b_time_ms = lane_b_result.raw_time_ms if lane_b_result else None
        race_run.status = "finished"
        race_run.traffic_light_status = "off"
        race_run.finished_at = now_string()

    db.commit()

    # Startdisplay nur bei unbestätigten Läufen aktualisieren.
    # Bestätigte Läufe dürfen die Anzeige nicht wieder überschreiben.
    if not race_run.confirmed:
        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, race_run.id),
            daemon=True
        ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}?saved=1",
        status_code=303,
    )

@router.post("/ui/timekeeper/run/{run_id}/save")
async def ui_save_run_legacy(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return await ui_event_save_run(
        event_id=active_event.id,
        run_id=run_id,
        request=request,
        db=db,
    )

@router.post("/ui/events/{event_id}/timekeeper/run/{run_id}/confirm")
def ui_event_confirm_run(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    ensure_event_is_active(active_event)
    ensure_event_is_open(active_event)

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(
            status_code=404,
            detail="Lauf nicht gefunden",
        )

    results = db.query(RunResult).filter(
        RunResult.event_id == active_event.id,
        RunResult.race_run_id == race_run.id,
    ).all()

    for result in results:
        result.confirmed = True

    race_run.confirmed = True
    race_run.status = "confirmed"
    race_run.traffic_light_status = "off"
    set_setting(db, repeat_scope_setting_key(race_run.id), "")

    db.commit()

    run_items = get_start_ordered_class_runs(
        active_event.id,
        race_run.race_class,
        db,
    )
    next_item = pick_next_unconfirmed_run(run_items)

    if next_item:
        next_run, _ = next_item

        set_setting(db, "startdisplay_active_event_id", str(active_event.id))
        set_setting(db, "startdisplay_active_run_id", str(next_run.id))
        set_setting(db, "startdisplay_active_set_at", now_string())
        db.commit()

        threading.Thread(
            target=send_start_display_for_run,
            args=(active_event.id, next_run.id),
            daemon=True
        ).start()
    else:
        set_setting(db, "startdisplay_active_event_id", "")
        set_setting(db, "startdisplay_active_run_id", "")
        set_setting(db, "startdisplay_active_set_at", "")
        db.commit()

        threading.Thread(
            target=send_start_display_numbers,
            args=(None, None),
            daemon=True
        ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/timekeeper/run/{run_id}",
        status_code=303,
    )

@router.post("/ui/timekeeper/run/{run_id}/confirm")
def ui_confirm_run_legacy(
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_active_event(db)

    return ui_event_confirm_run(
        event_id=active_event.id,
        run_id=run_id,
        db=db,
    )
