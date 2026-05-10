import base64
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from backend.database import get_db
from backend.models import Event, RaceRun, RunPenalty, RunResult, Participant, RegistrationGroup, SystemSetting
from backend.template_helpers import register_template_helpers

router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")
register_template_helpers(templates)


def require_event(event_id: int, db: Session) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Veranstaltung nicht gefunden")
    return event


def is_marshal_enabled(event: Event) -> bool:
    return event.marshal_enabled is True


def get_position_count(event: Event) -> int:
    try:
        return max(1, int(event.sectors_per_lane or 1))
    except (TypeError, ValueError):
        return 1


def normalize_marshal_points(value: int) -> int:
    value = max(0, int(value))
    return ((value + 2) // 5) * 5


def sync_penalty_to_run_result(
    event_id: int,
    run_id: int,
    lane: str,
    penalty_type: str,
    sector: int | None,
    points: int,
    db: Session,
):
    result_lane = "lane_a" if lane == "A" else "lane_b"
    result = db.query(RunResult).filter(
        RunResult.event_id == event_id,
        RunResult.race_run_id == run_id,
        RunResult.lane == result_lane,
    ).first()

    if not result:
        return

    if penalty_type == "general":
        result.general_penalty = points
        return

    if sector is None or sector < 1 or sector > 10:
        return

    setattr(result, f"sector_{sector}_penalty", points)


def get_station_label(penalty_type: str, lane: str, sector: int | None = None) -> str:
    if penalty_type == "general":
        return f"Start-/Zielposten Bahn {lane}"

    return f"Bahn {lane} / Position {sector}"


def get_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def get_station_url(event_id: int, penalty_type: str, lane: str, sector: int | None, base_url: str = "") -> str:
    params = {
        "type": penalty_type,
        "lane": lane,
    }

    if penalty_type == "sector":
        params["sector"] = str(sector)

    path = f"/ui/events/{event_id}/marshal?{urlencode(params)}"

    if base_url:
        return f"{base_url}{path}"

    return path


def build_marshal_stations(event: Event, request: Request | None = None) -> list[dict]:
    base_url = get_base_url(request) if request else ""
    stations = []

    for lane in ["A", "B"]:
        url = get_station_url(event.id, "general", lane, None, base_url)
        stations.append({
            "name": get_station_label("general", lane),
            "url": get_station_url(event.id, "general", lane, None),
            "absolute_url": url,
            "type": "general",
            "lane": lane,
            "sector": None,
        })

        for sector in range(1, get_position_count(event) + 1):
            url = get_station_url(event.id, "sector", lane, sector, base_url)
            stations.append({
                "name": get_station_label("sector", lane, sector),
                "url": get_station_url(event.id, "sector", lane, sector),
                "absolute_url": url,
                "type": "sector",
                "lane": lane,
                "sector": sector,
            })

    return stations


def build_qr_data_uri(value: str) -> str | None:
    try:
        import qrcode
    except ImportError:
        return None

    image = qrcode.make(value)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def build_marshal_qr_context(
    event: Event,
    request: Request,
    penalty_type: str,
    lane: str,
    sector: int | None,
    print_mode: bool,
) -> dict:
    absolute_url = get_station_url(event.id, penalty_type, lane, sector, get_base_url(request))
    station_label = get_station_label(penalty_type, lane, sector)

    return {
        "request": request,
        "event": event,
        "station_label": station_label,
        "station_url": absolute_url,
        "qr_data_uri": build_qr_data_uri(absolute_url),
        "print_mode": print_mode,
    }


def set_monitor_qr_settings(
    db: Session,
    event_id: int,
    values: dict[str, str],
):
    now = datetime.now()
    values[f"marshal_qr_event_{event_id}_expires_at"] = (
        now + timedelta(minutes=5)
    ).isoformat()

    for key, value in values.items():
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not setting:
            setting = SystemSetting(key=key)
            db.add(setting)
        setting.value = value
        setting.updated_at = now.isoformat()


def get_start_approval_status(event: Event, db: Session) -> dict:
    groups = db.query(RegistrationGroup).filter(
        RegistrationGroup.event_id == event.id,
    ).all()

    if not groups:
        return {
            "label": "Keine startbereiten Klassen vorhanden",
            "details": "Für diese Veranstaltung sind noch keine Startgruppen vorbereitet.",
        }

    released_count = sum(1 for group in groups if group.released)

    if released_count == 0:
        return {
            "label": "Startfreigabe noch nicht erteilt",
            "details": "Es ist noch keine Startgruppe für den Rennbetrieb freigegeben.",
        }

    if released_count < len(groups):
        return {
            "label": "Startfreigabe teilweise erteilt",
            "details": f"{released_count} von {len(groups)} Startgruppen sind freigegeben.",
        }

    return {
        "label": "Startfreigabe erteilt",
        "details": "Alle Startgruppen sind freigegeben.",
    }


def normalize_station_type(value: str | None) -> str | None:
    return value.strip().lower() if value else None


def get_lane_driver_id(run: RaceRun, lane: str) -> int | None:
    return run.lane_a_driver_id if lane == "A" else run.lane_b_driver_id


def get_lane_driver(run: RaceRun, lane: str, db: Session) -> Participant | None:
    driver_id = get_lane_driver_id(run, lane)

    if not driver_id:
        return None

    return db.query(Participant).filter(Participant.id == driver_id).first()


def get_marshal_round_number(round_name: str | None) -> int:
    if round_name == "halbfinale":
        return 2

    if round_name == "finale":
        return 3

    return 1


def get_marshal_run_label(run: RaceRun) -> str:
    return f"{get_marshal_round_number(run.round_name)}.{run.run_number}"


def get_current_run_for_lane(event_id: int, lane: str, db: Session):
    lane_driver_column = (
        RaceRun.lane_a_driver_id if lane == "A" else RaceRun.lane_b_driver_id
    )

    running_run = db.query(RaceRun).filter(
        RaceRun.event_id == event_id,
        lane_driver_column.isnot(None),
        RaceRun.confirmed == False,
        RaceRun.status.in_(["running", "started", "finished"]),
    ).order_by(
        RaceRun.id.desc(),
    ).first()

    if running_run:
        return running_run

    return db.query(RaceRun).filter(
        RaceRun.event_id == event_id,
        lane_driver_column.isnot(None),
        RaceRun.confirmed == False,
    ).order_by(
        RaceRun.id.asc(),
    ).first()


def get_penalty_record(
    event_id: int,
    run_id: int,
    lane: str,
    penalty_type: str,
    sector: int | None,
    db: Session,
) -> RunPenalty | None:
    query = db.query(RunPenalty).filter(
        RunPenalty.event_id == event_id,
        RunPenalty.run_id == run_id,
        RunPenalty.lane == lane,
        RunPenalty.penalty_type == penalty_type,
    )

    if penalty_type == "sector":
        query = query.filter(RunPenalty.sector == sector)
    else:
        query = query.filter(
            or_(
                RunPenalty.sector.is_(None),
                RunPenalty.sector == 0,
            )
        )

    return query.first()


def get_penalty_status(run: RaceRun, penalty: RunPenalty | None) -> str:
    if run.confirmed:
        return "gesperrt"

    if not penalty:
        return "offen"

    if penalty.updated_at:
        return "geändert"

    return "übermittelt"


def build_marshal_status_rows(event: Event, db: Session) -> list[dict]:
    rows = []

    for lane in ["A", "B"]:
        run = get_current_run_for_lane(event.id, lane, db)

        if not run:
            continue

        driver = get_lane_driver(run, lane, db)

        if not driver:
            continue

        stations = [
            {
                "penalty_type": "general",
                "type_label": "Allgemein",
                "sector": None,
            }
        ]

        for sector in range(1, get_position_count(event) + 1):
            stations.append(
                {
                    "penalty_type": "sector",
                    "type_label": "Position",
                    "sector": sector,
                }
            )

        for station in stations:
            penalty = get_penalty_record(
                event_id=event.id,
                run_id=run.id,
                lane=lane,
                penalty_type=station["penalty_type"],
                sector=station["sector"],
                db=db,
            )
            rows.append(
                {
                    "run_id": run.id,
                    "run_number": run.run_number,
                    "race_class": run.race_class,
                    "lane": lane,
                    "type_label": station["type_label"],
                    "sector": station["sector"],
                    "start_number": driver.start_number,
                    "driver_name": f"{driver.first_name} {driver.last_name}",
                    "points": penalty.points if penalty else 0,
                    "status": get_penalty_status(run, penalty),
                }
            )

    return rows


@router.get("/ui/events/{event_id}/marshals")
def ui_marshals_overview(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    marshal_enabled = is_marshal_enabled(event)

    stations = build_marshal_stations(event, request) if marshal_enabled else []

    return templates.TemplateResponse(
        request=request,
        name="marshals_overview.html",
        context={
            "request": request,
            "active_event": event,
            "event": event,
            "stations": stations,
            "marshal_enabled": marshal_enabled,
            "status_rows": build_marshal_status_rows(event, db) if marshal_enabled else [],
        },
    )


@router.get("/ui/events/{event_id}/marshal")
def ui_marshal_station(
    event_id: int,
    request: Request,
    type: str = Query(None),
    station_type: str = Query(None),
    lane: str = Query(None),
    sector: int = Query(None),
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    marshal_enabled = is_marshal_enabled(event)
    type = normalize_station_type(type or station_type)

    if not marshal_enabled:
        return templates.TemplateResponse(
            request=request,
            name="marshal_disabled.html",
            context={
                "request": request,
                "event": event,
            },
        )

    if not type:
        # Show role selection
        return templates.TemplateResponse(
            request=request,
            name="marshal_role_selection.html",
            context={
                "request": request,
                "event": event,
                "stations": build_marshal_stations(event),
            },
        )

    if type not in ["sector", "general"]:
        raise HTTPException(status_code=400, detail="Ungültiger Typ")

    if lane not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Ungültige Bahn")

    if type == "sector" and (sector is None or sector < 1 or sector > get_position_count(event)):
        raise HTTPException(status_code=400, detail="Ungültige Position")

    if type == "general":
        sector = None

    # Get current run
    run = get_current_run_for_lane(event_id, lane, db)
    if not run:
        return templates.TemplateResponse(
            request=request,
            name="marshal_no_run.html",
            context={
                "request": request,
                "event": event,
                "lane": lane,
                "type": type,
                "sector": sector,
                "station_label": get_station_label(type, lane, sector),
                "start_approval_status": get_start_approval_status(event, db),
            },
        )

    driver = get_lane_driver(run, lane, db)
    if not driver:
        raise HTTPException(status_code=404, detail="Fahrer nicht gefunden")

    # Get current penalty
    penalty = get_penalty_record(
        event_id=event_id,
        run_id=run.id,
        lane=lane,
        penalty_type=type,
        sector=sector,
        db=db,
    )

    current_points = penalty.points if penalty else 0
    can_edit = not run.confirmed

    return templates.TemplateResponse(
        request=request,
        name="marshal_input.html",
        context={
            "request": request,
            "event": event,
            "run": run,
            "run_label": get_marshal_run_label(run),
            "driver": driver,
            "lane": lane,
            "type": type,
            "sector": sector,
            "current_points": current_points,
            "can_edit": can_edit,
        },
    )


@router.post("/api/events/{event_id}/runs/{run_id}/penalties")
def api_save_penalty(
    event_id: int,
    run_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    if not is_marshal_enabled(event):
        raise HTTPException(
            status_code=403,
            detail="Die digitale Streckenpostenfunktion ist für diese Veranstaltung nicht aktiviert.",
        )
    run = db.query(RaceRun).filter(
        and_(RaceRun.id == run_id, RaceRun.event_id == event_id)
    ).first()
    if not run:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")

    if run.confirmed:
        raise HTTPException(status_code=400, detail="Lauf ist bereits bestätigt")

    penalty_type = normalize_station_type(payload.get("penalty_type"))
    lane = payload.get("lane")

    try:
        points = int(payload.get("points", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Punkte müssen eine Zahl sein")

    sector = payload.get("sector")

    if penalty_type not in ["sector", "general"]:
        raise HTTPException(status_code=400, detail="Ungültiger penalty_type")

    if lane not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Ungültige Bahn")

    if points < 0:
        raise HTTPException(status_code=400, detail="Punkte müssen >= 0 sein")

    points = normalize_marshal_points(points)

    if penalty_type == "sector":
        try:
            sector = int(sector)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Ungültige Position")

        if sector is None or sector < 1 or sector > get_position_count(event):
            raise HTTPException(status_code=400, detail="Ungültige Position")
    else:
        sector = None

    # Check if penalty exists
    penalty = get_penalty_record(
        event_id=event_id,
        run_id=run_id,
        lane=lane,
        penalty_type=penalty_type,
        sector=sector,
        db=db,
    )

    now = datetime.now().isoformat()
    if penalty:
        penalty.points = points
        penalty.updated_at = now
        penalty.updated_by = payload.get("user", "marshal")
    else:
        penalty = RunPenalty(
            event_id=event_id,
            run_id=run_id,
            lane=lane,
            penalty_type=penalty_type,
            sector=sector,
            points=points,
            submitted_at=now,
            created_by=payload.get("user", "marshal"),
        )
        db.add(penalty)

    sync_penalty_to_run_result(
        event_id=event_id,
        run_id=run_id,
        lane=lane,
        penalty_type=penalty_type,
        sector=sector,
        points=points,
        db=db,
    )

    db.commit()
    return {"status": "success"}


def serialize_penalty(penalty: RunPenalty) -> dict:
    return {
        "id": penalty.id,
        "event_id": penalty.event_id,
        "run_id": penalty.run_id,
        "lane": penalty.lane,
        "penalty_type": penalty.penalty_type,
        "sector": penalty.sector,
        "points": penalty.points or 0,
        "has_submission": True,
        "submitted_at": penalty.submitted_at,
        "updated_at": penalty.updated_at,
        "changed": bool(penalty.updated_at),
    }


@router.get("/api/events/{event_id}/runs/{run_id}/penalties")
def api_get_run_penalties(
    event_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    require_event(event_id, db)
    run = db.query(RaceRun).filter(
        RaceRun.id == run_id,
        RaceRun.event_id == event_id,
    ).first()

    if not run:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")

    penalties = db.query(RunPenalty).filter(
        RunPenalty.event_id == event_id,
        RunPenalty.run_id == run_id,
    ).all()

    return {
        "penalties": [serialize_penalty(penalty) for penalty in penalties],
    }


@router.get("/api/events/{event_id}/penalties")
def api_get_event_penalties(
    event_id: int,
    db: Session = Depends(get_db),
):
    require_event(event_id, db)
    penalties = db.query(RunPenalty).filter(
        RunPenalty.event_id == event_id,
    ).all()

    return {
        "penalties": [serialize_penalty(penalty) for penalty in penalties],
    }


@router.get("/api/events/{event_id}/marshal/current-run")
def api_get_current_run(
    event_id: int,
    type: str = Query(None),
    station_type: str = Query(None),
    lane: str = Query(...),
    sector: int = Query(None),
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    type = normalize_station_type(type or station_type)

    if not is_marshal_enabled(event):
        return {
            "marshal_enabled": False,
            "race_active": False,
            "has_current_run": False,
            "message": "Die digitale Streckenpostenfunktion ist für diese Veranstaltung nicht aktiviert. Fehlerpunkte werden durch die Zeitnahme erfasst.",
        }

    if type not in ["sector", "general"]:
        raise HTTPException(status_code=400, detail="Ungültiger Typ")

    if lane not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Ungültige Bahn")

    if type == "sector" and (sector is None or sector < 1 or sector > get_position_count(event)):
        raise HTTPException(status_code=400, detail="Ungültige Position")

    if type == "general":
        sector = None

    run = get_current_run_for_lane(event_id, lane, db)
    if not run:
        return {
            "marshal_enabled": True,
            "race_active": False,
            "has_current_run": False,
            "message": "Noch kein Rennbetrieb aktiv.",
            "start_approval_status": get_start_approval_status(event, db),
        }

    driver = get_lane_driver(run, lane, db)
    if not driver:
        raise HTTPException(status_code=404, detail="Fahrer nicht gefunden")

    class_name = f"Klasse {run.race_class}"

    penalty = get_penalty_record(
        event_id=event_id,
        run_id=run.id,
        lane=lane,
        penalty_type=type,
        sector=sector,
        db=db,
    )

    current_points = penalty.points if penalty else 0

    return {
        "marshal_enabled": True,
        "race_active": True,
        "has_current_run": True,
        "run_id": run.id,
        "run_number": run.run_number,
        "run_label": get_marshal_run_label(run),
        "class_name": class_name,
        "lane": lane,
        "start_number": driver.start_number,
        "driver_name": f"{driver.first_name} {driver.last_name}",
        "penalty_type": type,
        "sector": sector if type == 'sector' else None,
        "current_points": current_points,
        "has_submission": penalty is not None,
        "locked": run.confirmed,
        "confirmed": run.confirmed,
        "can_edit": not run.confirmed,
    }


@router.get("/ui/events/{event_id}/marshal/qr")
def ui_marshal_qr(
    event_id: int,
    request: Request,
    type: str = Query(...),
    lane: str = Query(...),
    sector: int = Query(None),
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    penalty_type = normalize_station_type(type)

    if not is_marshal_enabled(event):
        return templates.TemplateResponse(
            request=request,
            name="marshal_disabled.html",
            context={
                "request": request,
                "event": event,
            },
        )

    if penalty_type not in ["sector", "general"] or lane not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Ungültige Station")

    if penalty_type == "sector" and (sector is None or sector < 1 or sector > get_position_count(event)):
        raise HTTPException(status_code=400, detail="Ungültige Position")

    if penalty_type == "general":
        sector = None

    return templates.TemplateResponse(
        request=request,
        name="marshal_qr.html",
        context=build_marshal_qr_context(event, request, penalty_type, lane, sector, False),
    )


@router.get("/ui/events/{event_id}/marshal/qr/print")
def ui_marshal_qr_print(
    event_id: int,
    request: Request,
    type: str = Query(...),
    lane: str = Query(...),
    sector: int = Query(None),
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)
    penalty_type = normalize_station_type(type)

    if not is_marshal_enabled(event):
        return templates.TemplateResponse(
            request=request,
            name="marshal_disabled.html",
            context={
                "request": request,
                "event": event,
            },
        )

    if penalty_type not in ["sector", "general"] or lane not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Ungültige Station")

    if penalty_type == "sector" and (sector is None or sector < 1 or sector > get_position_count(event)):
        raise HTTPException(status_code=400, detail="Ungültige Position")

    if penalty_type == "general":
        sector = None

    return templates.TemplateResponse(
        request=request,
        name="marshal_qr.html",
        context=build_marshal_qr_context(event, request, penalty_type, lane, sector, True),
    )


@router.get("/ui/events/{event_id}/marshal/qr/print-all")
def ui_marshal_qr_print_all(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)

    if not is_marshal_enabled(event):
        return templates.TemplateResponse(
            request=request,
            name="marshal_disabled.html",
            context={
                "request": request,
                "event": event,
            },
        )

    stations = []
    for station in build_marshal_stations(event, request):
        stations.append({
            **station,
            "qr_data_uri": build_qr_data_uri(station["absolute_url"]),
        })

    return templates.TemplateResponse(
        request=request,
        name="marshal_qr_print_all.html",
        context={
            "request": request,
            "event": event,
            "stations": stations,
        },
    )


@router.post("/api/events/{event_id}/monitor/marshal-qr")
def api_show_marshal_qr_on_monitor(
    event_id: int,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)

    if not is_marshal_enabled(event):
        raise HTTPException(status_code=403, detail="Streckenpostenfunktion ist deaktiviert")

    qr_mode = payload.get("mode")

    if qr_mode == "landing":
        landing_url = f"{get_base_url(request)}/ui/events/{event.id}/marshal"
        set_monitor_qr_settings(
            db=db,
            event_id=event.id,
            values={
                f"marshal_qr_event_{event.id}_enabled": "true",
                f"marshal_qr_event_{event.id}_mode": "landing",
                f"marshal_qr_event_{event.id}_label": "Auswahl Station",
                f"marshal_qr_event_{event.id}_url": landing_url,
            },
        )
        db.commit()
        return {"status": "success"}

    if qr_mode == "all":
        set_monitor_qr_settings(
            db=db,
            event_id=event.id,
            values={
                f"marshal_qr_event_{event.id}_enabled": "true",
                f"marshal_qr_event_{event.id}_mode": "all",
                f"marshal_qr_event_{event.id}_label": "Alle Streckenposten",
                f"marshal_qr_event_{event.id}_url": "",
            },
        )
        db.commit()
        return {"status": "success"}

    penalty_type = normalize_station_type(payload.get("type"))
    lane = payload.get("lane")
    sector = payload.get("sector")

    if penalty_type not in ["sector", "general"] or lane not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Ungültige Station")

    if penalty_type == "sector":
        try:
            sector = int(sector)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Ungültige Position")

        if sector < 1 or sector > get_position_count(event):
            raise HTTPException(status_code=400, detail="Ungültige Position")
    else:
        sector = None

    absolute_url = get_station_url(event.id, penalty_type, lane, sector, get_base_url(request))
    station_label = get_station_label(penalty_type, lane, sector)
    set_monitor_qr_settings(
        db=db,
        event_id=event.id,
        values={
            f"marshal_qr_event_{event.id}_enabled": "true",
            f"marshal_qr_event_{event.id}_mode": "single",
            f"marshal_qr_event_{event.id}_label": station_label,
            f"marshal_qr_event_{event.id}_url": absolute_url,
        },
    )

    db.commit()
    return {"status": "success"}


@router.post("/api/events/{event_id}/monitor/reset")
def api_reset_marshal_qr_monitor(
    event_id: int,
    db: Session = Depends(get_db),
):
    event = require_event(event_id, db)

    setting = db.query(SystemSetting).filter(
        SystemSetting.key == f"marshal_qr_event_{event.id}_enabled"
    ).first()

    if setting:
        setting.value = "false"
        setting.updated_at = datetime.now().isoformat()

    expires_at_setting = db.query(SystemSetting).filter(
        SystemSetting.key == f"marshal_qr_event_{event.id}_expires_at"
    ).first()

    if expires_at_setting:
        expires_at_setting.value = ""
        expires_at_setting.updated_at = datetime.now().isoformat()

    db.commit()

    return {"status": "success"}
