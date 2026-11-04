from fastapi import APIRouter
from fastapi import Depends
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi import Request
from sqlalchemy.orm import Session
from backend.database import get_db
from urllib.parse import quote
import threading
from backend.common import get_active_event, get_event_year, now_string, redirect_to_active_event_path, require_event
from backend.constants import MONITOR_ROUND_OPTIONS
from backend.application.monitor_output import build_monitor_output_payload
from backend.application.monitor_qr import build_marshal_station_options, get_marshal_qr_expires_at
from backend.application.monitor_settings import get_or_create_monitor_display_setting, parse_monitor_csv
from backend.application.system_actions import restart_monitor_browser_delayed
from backend.template_runtime import templates


router = APIRouter()


@router.get("/ui/monitor-output")
def ui_monitor_output_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/monitor-output")

@router.get("/ui/output-monitor")
def ui_output_monitor_redirect(
    db: Session = Depends(get_db),
):
    return redirect_to_active_event_path(db, "/monitor-output")

@router.get("/ui/events/{event_id}/output-monitor")
def ui_event_output_monitor_redirect(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor-output",
        status_code=303,
    )

@router.get("/ui/events/{event_id}/monitor-output")
def ui_event_monitor_output(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    setting = get_or_create_monitor_display_setting(active_event, db)
    selected_classes = [
        int(item)
        for item in parse_monitor_csv(setting.display_classes)
        if item.isdigit()
    ]
    selected_rounds = parse_monitor_csv(setting.display_rounds)

    return templates.TemplateResponse(
        request=request,
        name="monitor_output.html",
        context={
            "active_event": active_event,
            "setting": setting,
            "selected_classes": selected_classes,
            "selected_rounds": selected_rounds,
            "round_options": MONITOR_ROUND_OPTIONS,
            "monitor_data": build_monitor_output_payload(active_event, db),
            "marshal_station_options": build_marshal_station_options(active_event),
            "marshal_qr_expires_at": get_marshal_qr_expires_at(active_event, db),
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error"),
            "event_year": get_event_year(active_event),
        },
    )

@router.post("/ui/events/{event_id}/monitor-output")
async def ui_event_update_monitor_output(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    setting = get_or_create_monitor_display_setting(active_event, db)
    form = await request.form()

    selected_classes = [
        str(race_class)
        for race_class in [1, 2, 3, 4, 5]
        if form.get(f"class_{race_class}") == "1"
    ]
    selected_rounds = [
        option["key"]
        for option in MONITOR_ROUND_OPTIONS
        if form.get(f"round_{option['key']}") == "1"
    ]

    setting.show_race_control = form.get("show_race_control") == "1"
    setting.show_logo_signal = form.get("show_logo_signal") == "1"
    setting.display_classes = ",".join(selected_classes)
    setting.display_rounds = ",".join(selected_rounds)
    setting.show_overall_results = form.get("show_overall_results") == "1"
    setting.show_team_results = form.get("show_team_results") == "1"
    setting.show_statistics = form.get("show_statistics") == "1"
    setting.show_checkin_qr = form.get("show_checkin_qr") == "1"
    setting.display_orientation = (
        form.get("display_orientation")
        if form.get("display_orientation") in ("landscape", "portrait", "portrait_left")
        else "landscape"
    )
    setting.display_theme = (
        "light"
        if form.get("display_theme") == "light"
        else "dark"
    )
    setting.updated_at = now_string()

    db.commit()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor-output",
        status_code=303,
    )

@router.post("/ui/events/{event_id}/monitor-output/restart-browser")
def ui_event_restart_monitor_browser(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)

    threading.Thread(
        target=restart_monitor_browser_delayed,
        daemon=True,
    ).start()

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor-output?success={quote('Monitor wird neu geladen.')}",
        status_code=303,
    )

@router.get("/ui/active/monitor")
@router.get("/ui/monitor")
def ui_monitor_redirect(
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = get_active_event(db)

    if not active_event:
        return templates.TemplateResponse(
            request=request,
            name="monitor_no_event.html",
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}/monitor",
        status_code=303,
    )

@router.get("/ui/events/{event_id}/monitor")
def ui_event_monitor(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    current_active_event = get_active_event(db)

    if current_active_event is not None and current_active_event.id != active_event.id:
        return RedirectResponse(
            url=f"/ui/events/{current_active_event.id}/monitor",
            status_code=303,
        )

    monitor_data = build_monitor_output_payload(
        event=active_event,
        db=db,
    )

    return templates.TemplateResponse(
        request=request,
        name="monitor.html",
        context={
            "active_event": active_event,
            "monitor_data": monitor_data,
            "event_year": get_event_year(active_event),
        },
    )

@router.get("/api/events/{event_id}/monitor")
def api_event_monitor(
    event_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    current_active_event = get_active_event(db)

    if current_active_event is not None and current_active_event.id != active_event.id:
        active_event = current_active_event

    return JSONResponse(
        content=build_monitor_output_payload(
            event=active_event,
            db=db,
        ),
        headers={
            "Cache-Control": "no-store",
        },
    )
