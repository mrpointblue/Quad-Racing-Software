from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from backend.models import RaceRun
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from backend.database import get_db
from urllib.parse import quote
from backend.common import now_string, require_event
from backend.application.logo_runtime import get_logo_status_payload, pulse_logo_web_reset, pulse_logo_web_start
from backend.application.logo_sync import sync_logo_times_for_run
from backend.application.settings_service import save_logo_settings, set_setting


router = APIRouter()


@router.post("/ui/settings/logo")
def ui_save_logo_connection(
    logo_enabled: str = Form("false"),
    logo_simulation_mode: str = Form("false"),
    logo_ip: str = Form(...),
    logo_port: int = Form(...),
    logo_timeout_ms: int = Form(...),
    logo_poll_interval_ms: int = Form(200),
    logo_access_mode: str = Form("modbus_tcp"),
    logo_password: str = Form(""),
    logo_time_register_start: int = Form(0),
    logo_output_coil_start: int = Form(8192),
    logo_marker_coil_start: int = Form(8256),
    logo_web_start_coil: str = Form("8262"),
    logo_web_reset_coil: str = Form("8264"),
    logo_pulse_duration: str = Form("0.2"),
    db: Session = Depends(get_db),
):
    if logo_port < 1 or logo_port > 65535:
        return RedirectResponse(
            url="/ui/settings?error=Ungültiger Port",
            status_code=303,
        )

    if logo_timeout_ms < 100:
        logo_timeout_ms = 100

    if logo_timeout_ms > 10000:
        logo_timeout_ms = 10000

    if logo_poll_interval_ms < 500:
        logo_poll_interval_ms = 500

    if logo_poll_interval_ms > 500:
        logo_poll_interval_ms = 500

    if logo_access_mode not in ["modbus_tcp"]:
        return RedirectResponse(
            url="/ui/settings?error=Ungültige Zugriffsart",
            status_code=303,
        )

    try:
        pulse_duration_value = max(0.05, min(2.0, float(str(logo_pulse_duration).replace(",", "."))))
    except ValueError:
        pulse_duration_value = 0.2

    save_logo_settings(
        db=db,
        form_values={
            "logo_enabled": logo_enabled,
            "logo_simulation_mode": logo_simulation_mode,
            "logo_ip": logo_ip.strip(),
            "logo_port": str(logo_port),
            "logo_timeout_ms": str(logo_timeout_ms),
            "logo_poll_interval_ms": str(logo_poll_interval_ms),
            "logo_access_mode": logo_access_mode,
            "logo_password": logo_password.strip(),
            "logo_time_register_start": str(logo_time_register_start),
            "logo_output_coil_start": str(logo_output_coil_start),
            "logo_marker_coil_start": str(logo_marker_coil_start),
            "logo_web_start_coil": logo_web_start_coil.strip(),
            "logo_web_reset_coil": logo_web_reset_coil.strip(),
            "logo_pulse_duration": f"{pulse_duration_value:.2f}",
            "logo_pulse_duration_ms": str(round(pulse_duration_value * 1000)),
        },
    )

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Verbindung gespeichert",
        status_code=303,
    )

@router.post("/ui/settings/logo-mapping")
def ui_save_logo_mapping(
    logo_start_range: int = Form(...),
    logo_start_address: int = Form(...),

    logo_reset_range: int = Form(...),
    logo_reset_address: int = Form(...),

    logo_time_lane_1_range: int = Form(...),
    logo_time_lane_1_address: int = Form(...),

    logo_time_lane_2_range: int = Form(...),
    logo_time_lane_2_address: int = Form(...),

    logo_ready_range: int = Form(...),
    logo_ready_address: int = Form(...),

    logo_go_range: int = Form(...),
    logo_go_address: int = Form(...),

    logo_attention_range: int = Form(...),
    logo_attention_address: int = Form(...),

    logo_light_barrier_1_range: int = Form(...),
    logo_light_barrier_1_address: int = Form(...),

    logo_light_barrier_2_range: int = Form(...),
    logo_light_barrier_2_address: int = Form(...),

    logo_error_range: int = Form(...),
    logo_error_address: int = Form(...),

    logo_false_start_1_range: int = Form(...),
    logo_false_start_1_address: int = Form(...),

    logo_false_start_2_range: int = Form(...),
    logo_false_start_2_address: int = Form(...),

    db: Session = Depends(get_db),
):
    save_logo_settings(
        db=db,
        form_values={
            "logo_start_range": str(logo_start_range),
            "logo_start_address": str(logo_start_address),

            "logo_reset_range": str(logo_reset_range),
            "logo_reset_address": str(logo_reset_address),

            "logo_time_lane_1_range": str(logo_time_lane_1_range),
            "logo_time_lane_1_address": str(logo_time_lane_1_address),

            "logo_time_lane_2_range": str(logo_time_lane_2_range),
            "logo_time_lane_2_address": str(logo_time_lane_2_address),

            "logo_ready_range": str(logo_ready_range),
            "logo_ready_address": str(logo_ready_address),

            "logo_go_range": str(logo_go_range),
            "logo_go_address": str(logo_go_address),

            "logo_attention_range": str(logo_attention_range),
            "logo_attention_address": str(logo_attention_address),

            "logo_light_barrier_1_range": str(logo_light_barrier_1_range),
            "logo_light_barrier_1_address": str(logo_light_barrier_1_address),

            "logo_light_barrier_2_range": str(logo_light_barrier_2_range),
            "logo_light_barrier_2_address": str(logo_light_barrier_2_address),

            "logo_error_range": str(logo_error_range),
            "logo_error_address": str(logo_error_address),

            "logo_false_start_1_range": str(logo_false_start_1_range),
            "logo_false_start_1_address": str(logo_false_start_1_address),

            "logo_false_start_2_range": str(logo_false_start_2_range),
            "logo_false_start_2_address": str(logo_false_start_2_address),
        },
    )

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Mapping gespeichert",
        status_code=303,
    )

@router.post("/ui/settings/logo/check")
def ui_logo_connection_check(
    db: Session = Depends(get_db),
):
    result = get_logo_status_payload()
    message = result.get("message", "Statusprüfung durchgeführt")
    ok = bool(result.get("ok"))

    set_setting(db, "logo_last_check_at", now_string())
    set_setting(db, "logo_last_check_ok", "true" if ok else "false")
    set_setting(db, "logo_last_error", "" if ok else message)
    db.commit()

    status_key = "success" if ok else "error"

    return RedirectResponse(
        url=f"/ui/settings?{status_key}={quote(message)}",
        status_code=303,
    )

@router.get("/api/logo/status")
def api_logo_status(
    db: Session = Depends(get_db),
):
    return get_logo_status_payload(db)

@router.get("/api/events/{event_id}/timekeeper/run/{run_id}/logo-status")
def api_timekeeper_run_logo_status(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    active_event = require_event(event_id, db)
    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == active_event.id,
        RaceRun.id == run_id,
    ).first()

    if not race_run:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")

    status = sync_logo_times_for_run(race_run, db)

    status["run"] = {
        "id": race_run.id,
        "status": race_run.status,
        "confirmed": race_run.confirmed,
    }

    return status

@router.post("/api/logo/test-connection")
def api_logo_test_connection(
    db: Session = Depends(get_db),
):
    return get_logo_status_payload(db)

@router.post("/api/logo/web-start")
def api_logo_web_start(
    db: Session = Depends(get_db),
):
    return pulse_logo_web_start(db)

@router.post("/api/logo/web-reset")
def api_logo_web_reset(
    db: Session = Depends(get_db),
):
    return pulse_logo_web_reset(db)

@router.post("/ui/settings/logo/web-start")
def ui_logo_web_start(
    db: Session = Depends(get_db),
):
    result = pulse_logo_web_start(db)
    status_key = "success" if result.get("ok") else "error"
    return RedirectResponse(
        url=f"/ui/settings?{status_key}={quote(result.get('message', 'WEB Start getestet'))}",
        status_code=303,
    )

@router.post("/ui/settings/logo/web-reset")
def ui_logo_web_reset(
    db: Session = Depends(get_db),
):
    result = pulse_logo_web_reset(db)
    status_key = "success" if result.get("ok") else "error"
    return RedirectResponse(
        url=f"/ui/settings?{status_key}={quote(result.get('message', 'WEB Reset getestet'))}",
        status_code=303,
    )

@router.post("/ui/settings/logo/reset-status")
def ui_logo_reset_status(
    db: Session = Depends(get_db),
):
    set_setting(db, "logo_last_check_at", "")
    set_setting(db, "logo_last_check_ok", "")
    set_setting(db, "logo_last_error", "")
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=LOGO-Status zurückgesetzt",
        status_code=303,
    )
