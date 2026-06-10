import base64
from datetime import datetime, timedelta
from io import BytesIO
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from backend.database import get_db
from backend.marshal_urls import MARSHAL_QR_BASE_URL, build_marshal_station_path, build_marshal_station_url
from backend.models import Event, Pairing, RaceRun, RunPenalty, RunResult, Participant, RegistrationGroup, SystemSetting
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
        return "Start-/Zielposten"

    return f"Bahn {lane} / Position {sector}"


def get_base_url(request: Request) -> str:
    return MARSHAL_QR_BASE_URL


def get_station_url(event_id: int, penalty_type: str, lane: str, sector: int | None, base_url: str = "") -> str:
    if base_url:
        return build_marshal_station_url(event_id, penalty_type, lane, sector, base_url)

    return build_marshal_station_path(event_id, penalty_type, lane, sector)


def build_marshal_stations(event: Event, request: Request | None = None) -> list[dict]:
    base_url = get_base_url(request) if request else ""
    stations = []

    url = get_station_url(event.id, "general", "", None, base_url)
    stations.append({
        "name": get_station_label("general", ""),
        "url": get_station_url(event.id, "general", "", None),
        "absolute_url": url,
        "type": "general",
        "lane": "",
        "sector": None,
    })

    for lane in ["A", "B"]:
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
    lane: str | None,
    sector: int | None,
    print_mode: bool,
) -> dict:
    normalized_lane = lane or ""
    absolute_url = get_station_url(event.id, penalty_type, normalized_lane, sector, get_base_url(request))
    station_label = get_station_label(penalty_type, normalized_lane, sector)

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


def get_lane_driver(run: RaceRun, lane: str, db: Session) -> Participant | None:
    result_lane = "lane_a" if lane == "A" else "lane_b"
    result = next((r for r in run.results if r.lane == result_lane), None)

    if not result or result.participant_id is None:
        return None

    return db.query(Participant).filter(Participant.id == result.participant_id).first()


def run_has_lane_driver(run: RaceRun, lane: str) -> bool:
    result_lane = "lane_a" if lane == "A" else "lane_b"
    return any(
        result.lane == result_lane and result.participant_id is not None
        for result in run.results
    )


def get_marshal_round_number(round_name: str | None) -> int:
    if round_name == "halbfinale":
        return 2

    if round_name == "finale":
        return 3

    return 1


def get_marshal_run_label(run: RaceRun) -> str:
    return f"{get_marshal_round_number(run.round_name)}.{run.run_number}"


def get_marshal_run_order_key(race_run: RaceRun, pairing: Pairing) -> tuple:
    return (
        get_marshal_round_number(race_run.round_name),
        race_run.race_class,
        pairing.pairing_number,
        race_run.run_number,
    )


def get_ordered_marshal_runs(event_id: int, db: Session) -> list[tuple[RaceRun, Pairing]]:
    pairings = db.query(Pairing).filter(
        Pairing.event_id == event_id,
    ).all()

    items: list[tuple[RaceRun, Pairing]] = []

    for pairing in pairings:
        for race_run in pairing.runs:
            items.append((race_run, pairing))

    items.sort(
        key=lambda item: get_marshal_run_order_key(
            race_run=item[0],
            pairing=item[1],
        )
    )

    return items


def get_current_marshal_run(event_id: int, db: Session) -> RaceRun | None:
    active_run_id_str = db.query(SystemSetting).filter(
        SystemSetting.key == "startdisplay_active_run_id",
    ).first()

    if active_run_id_str and active_run_id_str.value:
        try:
            active_run_id = int(active_run_id_str.value)
            active_run = db.query(RaceRun).filter(
                RaceRun.id == active_run_id,
                RaceRun.event_id == event_id,
                RaceRun.confirmed == False,
            ).first()
            if active_run:
                return active_run
        except (ValueError, TypeError):
            pass

    for race_run, _pairing in get_ordered_marshal_runs(event_id, db):
        if not race_run.confirmed:
            return race_run

    return None


def get_current_run_for_lane(event_id: int, lane: str, db: Session):
    current_run = get_current_marshal_run(event_id, db)

    if not current_run or not run_has_lane_driver(current_run, lane):
        return None

    return current_run


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
    current_run = get_current_marshal_run(event.id, db)

    for lane in ["A", "B"]:
        run = current_run

        if not run or not run_has_lane_driver(run, lane):
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

    if type == "general":
        lane_entries = []

        for general_lane in ["A", "B"]:
            lane_run = get_current_run_for_lane(event_id, general_lane, db)
            lane_driver = get_lane_driver(lane_run, general_lane, db) if lane_run else None
            penalty = None

            if lane_run and lane_driver:
                penalty = get_penalty_record(
                    event_id=event_id,
                    run_id=lane_run.id,
                    lane=general_lane,
                    penalty_type="general",
                    sector=None,
                    db=db,
                )

            lane_entries.append(
                {
                    "lane": general_lane,
                    "run": lane_run,
                    "run_label": get_marshal_run_label(lane_run) if lane_run else "",
                    "driver": lane_driver,
                    "current_points": penalty.points if penalty else 0,
                    "has_submission": penalty is not None,
                    "can_edit": bool(lane_run and not lane_run.confirmed),
                }
            )

        if not any(entry["run"] and entry["driver"] for entry in lane_entries):
            return templates.TemplateResponse(
                request=request,
                name="marshal_no_run.html",
                context={
                    "request": request,
                    "event": event,
                    "lane": "",
                    "type": type,
                    "sector": None,
                    "station_label": get_station_label(type, "", None),
                    "start_approval_status": get_start_approval_status(event, db),
                },
            )

        return templates.TemplateResponse(
            request=request,
            name="marshal_input.html",
            context={
                "request": request,
                "event": event,
                "type": type,
                "sector": None,
                "lane_entries": lane_entries,
            },
        )

    if lane not in ["A", "B"]:
        raise HTTPException(status_code=400, detail="Ungültige Bahn")

    if type == "sector" and (sector is None or sector < 1 or sector > get_position_count(event)):
        raise HTTPException(status_code=400, detail="Ungültige Position")

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
    has_submission = penalty is not None
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
            "has_submission": has_submission,
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

    current_station_run = get_current_run_for_lane(event_id, lane, db)
    if not current_station_run or current_station_run.id != run.id:
        raise HTTPException(
            status_code=400,
            detail="Dieser Lauf ist für diese Station aktuell nicht aktiv.",
        )

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
            "message": "Für diese Station ist aktuell kein Lauf aktiv.",
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
    lane: str = Query(None),
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

    if penalty_type not in ["sector", "general"]:
        raise HTTPException(status_code=400, detail="Ungültige Station")

    if penalty_type == "sector":
        if lane not in ["A", "B"]:
            raise HTTPException(status_code=400, detail="Ungültige Bahn")

        if sector is None or sector < 1 or sector > get_position_count(event):
            raise HTTPException(status_code=400, detail="Ungültige Position")
    else:
        lane = ""
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
    lane: str = Query(None),
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

    if penalty_type not in ["sector", "general"]:
        raise HTTPException(status_code=400, detail="Ungültige Station")

    if penalty_type == "sector":
        if lane not in ["A", "B"]:
            raise HTTPException(status_code=400, detail="Ungültige Bahn")

        if sector is None or sector < 1 or sector > get_position_count(event):
            raise HTTPException(status_code=400, detail="Ungültige Position")
    else:
        lane = ""
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

    if penalty_type not in ["sector", "general"]:
        raise HTTPException(status_code=400, detail="Ungültige Station")

    if penalty_type == "sector":
        if lane not in ["A", "B"]:
            raise HTTPException(status_code=400, detail="Ungültige Bahn")

        try:
            sector = int(sector)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Ungültige Position")

        if sector < 1 or sector > get_position_count(event):
            raise HTTPException(status_code=400, detail="Ungültige Position")
    else:
        lane = ""
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
