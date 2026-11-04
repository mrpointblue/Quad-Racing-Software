from backend.models import Event
from backend.models import MonitorDisplaySetting
from backend.models import Pairing
from backend.models import Participant
from backend.round_logic import ROUND_FINAL
from backend.round_logic import ROUND_PRELIMINARY
from backend.round_logic import ROUND_SEMIFINAL
from backend.models import RaceRun
from backend.models import RunResult
from sqlalchemy.orm import Session
from backend.time_utils import normalize_time_to_centiseconds_ms
from backend.common import now_string
from backend.application.logo_runtime import get_logo_status_payload
from backend.application.logo_sync import sync_logo_times_for_run
from backend.application.monitor_settings import get_or_create_monitor_display_setting
from backend.application.runs import format_time_ms, get_lane_results_for_run, get_penalty_for_sector, get_round_display, get_run_repeat_scope, is_lane_editable_for_repeat_scope
from backend.application.settings_service import get_logo_settings, get_setting


def get_monitor_penalty_sum(result: RunResult | None, db: Session) -> int:
    if result is None:
        return 0

    penalty = result.general_penalty or 0

    for sector in range(1, 11):
        penalty += get_penalty_for_sector(result, sector)

    return penalty

def get_monitor_valued_time_ms_for_raw(
    result: RunResult | None,
    db: Session,
    raw_time_ms: int | None,
) -> int | None:
    if result is None or raw_time_ms is None:
        return None

    if result.participant and result.participant.withdrawn:
        return None

    scoring_raw_time_ms = normalize_time_to_centiseconds_ms(raw_time_ms)

    if scoring_raw_time_ms is None:
        return None

    return scoring_raw_time_ms + (get_monitor_penalty_sum(result, db) * 1000)

def get_monitor_valued_time_ms(result: RunResult | None, db: Session) -> int | None:
    return get_monitor_valued_time_ms_for_raw(
        result=result,
        db=db,
        raw_time_ms=result.raw_time_ms if result else None,
    )

def monitor_driver_name(participant: Participant | None) -> str:
    if participant is None:
        return "-"
    
    # Don't show eliminated drivers in monitor
    if participant.withdrawn:
        return "-"

    return f"#{participant.start_number} {participant.first_name} {participant.last_name}"

def monitor_driver_display_name(participant: Participant | None) -> str:
    if participant is None or participant.withdrawn:
        return "-"

    first_name = (participant.first_name or "").strip()
    last_name = (participant.last_name or "").strip()

    if first_name and last_name:
        return f"{first_name} {last_name}"

    return first_name or last_name or "-"

def monitor_result_payload(
    result: RunResult | None,
    db: Session,
    raw_time_ms_override: int | None = None,
) -> dict:
    if result is None:
        return {
            "driver": "-",
            "start_number": None,
            "last_name": "-",
            "display_name": "-",
            "raw_time": "-",
            "penalty": "-",
            "valued_time": "-",
        }

    participant = result.participant
    if participant is None or participant.withdrawn:
        start_number = None
        last_name = "-"
        display_name = "-"
    else:
        start_number = participant.start_number
        last_name = participant.last_name
        display_name = monitor_driver_display_name(participant)

    raw_time_ms = (
        raw_time_ms_override
        if isinstance(raw_time_ms_override, int) and raw_time_ms_override > 0
        else result.raw_time_ms
    )

    return {
        "driver": monitor_driver_name(participant),
        "start_number": start_number,
        "last_name": last_name,
        "display_name": display_name,
        "raw_time": format_time_ms(raw_time_ms),
        "penalty": get_monitor_penalty_sum(result, db),
        "valued_time": format_time_ms(
            get_monitor_valued_time_ms_for_raw(result, db, raw_time_ms)
        ),
    }

def monitor_run_payload(
    race_run: RaceRun,
    pairing: Pairing,
    db: Session,
    live_status: dict | None = None,
) -> dict:
    lane_a_result, lane_b_result = get_lane_results_for_run(race_run)
    round_number, round_label = get_round_display(race_run.round_name)
    live_times = live_status.get("times", {}) if live_status else {}
    repeat_scope = get_run_repeat_scope(db, race_run.id)
    use_live_times = bool(
        live_status
        and live_status.get("ok")
        and not race_run.confirmed
        and race_run.status in ["running", "started"]
    )
    lane_a_live_ms = (
        live_times.get("lane_a_ms")
        if use_live_times and is_lane_editable_for_repeat_scope("lane_a", repeat_scope)
        else None
    )
    lane_b_live_ms = (
        live_times.get("lane_b_ms")
        if use_live_times and is_lane_editable_for_repeat_scope("lane_b", repeat_scope)
        else None
    )

    return {
        "id": race_run.id,
        "race_class": race_run.race_class,
        "round_name": race_run.round_name,
        "round_label": round_label,
        "round_number": round_number,
        "pairing_number": pairing.pairing_number,
        "run_number": race_run.run_number,
        "status": race_run.status,
        "traffic_light_status": race_run.traffic_light_status or "off",
        "confirmed": race_run.confirmed,
        "lane_a": monitor_result_payload(lane_a_result, db, lane_a_live_ms),
        "lane_b": monitor_result_payload(lane_b_result, db, lane_b_live_ms),
    }

def get_monitor_order_key(race_run: RaceRun, pairing: Pairing) -> tuple:
    round_number, _round_label = get_round_display(race_run.round_name)

    return (
        round_number,
        race_run.race_class,
        pairing.pairing_number,
        race_run.run_number,
    )

def get_monitor_runs(
    event_id: int,
    db: Session,
) -> list[tuple[RaceRun, Pairing]]:
    pairings = db.query(Pairing).filter(
        Pairing.event_id == event_id,
    ).all()

    items: list[tuple[RaceRun, Pairing]] = []

    for pairing in pairings:
        for race_run in pairing.runs:
            items.append((race_run, pairing))

    items.sort(
        key=lambda item: get_monitor_order_key(
            race_run=item[0],
            pairing=item[1],
        )
    )

    return items

def pick_monitor_current_run(
    run_items: list[tuple[RaceRun, Pairing]],
    active_run_id: int | None = None,
) -> tuple[RaceRun, Pairing] | None:
    """
    Aktuellen Monitor-Lauf bestimmen.

    Die aktiv gesetzte Lauf-ID hat Vorrang. Wenn keine aktive ID gesetzt ist,
    zeigt der Monitor den ersten unbestätigten Lauf in fachlicher Reihenfolge.
    Dadurch bleibt die Monitoranzeige auch vor dem Drücken von START nutzbar.
    """
    if active_run_id:
        for race_run, pairing in run_items:
            if race_run.id == active_run_id and not race_run.confirmed:
                return race_run, pairing

    for race_run, pairing in run_items:
        if not race_run.confirmed:
            return race_run, pairing

    return None

def pick_monitor_next_runs(
    run_items: list[tuple[RaceRun, Pairing]],
    db: Session,
    current_run: RaceRun | None,
) -> list[dict]:
    """
    Nächste Starter klassenübergreifend in Startreihenfolge anzeigen.

    Bahnwechsel/Rückläufe derselben Paarung werden für die Teilnehmeranzeige
    ausgeblendet. Die Rennläufe selbst bleiben unverändert.
    """
    if current_run is None:
        return []

    next_runs = []
    current_found = False
    displayed_pairing_ids: set[int] = set()

    for race_run, pairing in run_items:
        if race_run.id == current_run.id:
            current_found = True
            displayed_pairing_ids.add(pairing.id)
            continue

        if not current_found:
            continue

        if race_run.confirmed:
            continue

        if pairing.id in displayed_pairing_ids:
            continue

        displayed_pairing_ids.add(pairing.id)
        next_runs.append(monitor_run_payload(race_run, pairing, db))

    return next_runs

def get_monitor_result_classes(race_class: int | None) -> set[int] | None:
    if race_class in [1, 2]:
        return {1, 2}

    if race_class in [3, 4]:
        return {3, 4}

    if race_class == 5:
        return {5}

    return None

def get_monitor_live_result_runs(
    run_items: list[tuple[RaceRun, Pairing]],
    db: Session,
    focus_race_classes: set[int] | None = None,
    preferred_round_number: int | None = None,
    limit: int = 20,
) -> list[dict]:
    participant_rows = {}
    available_rounds_with_results = set()

    for race_run, _pairing in run_items:
        normalized_round = "viertelfinale" if race_run.round_name == ROUND_PRELIMINARY else race_run.round_name

        if normalized_round not in ["viertelfinale", ROUND_SEMIFINAL]:
            continue

        if focus_race_classes is not None and race_run.race_class not in focus_race_classes:
            continue

        if race_run.results:
            round_number, _round_label = get_round_display(race_run.round_name)

            if any(result.raw_time_ms is not None for result in race_run.results):
                available_rounds_with_results.add(round_number)

    if not available_rounds_with_results:
        return []

    if preferred_round_number in available_rounds_with_results:
        display_round_number = preferred_round_number
    else:
        available_round_numbers = [
            round_number
            for round_number in available_rounds_with_results
            if preferred_round_number is None or round_number <= preferred_round_number
        ]

        if not available_round_numbers:
            available_round_numbers = list(available_rounds_with_results)

        display_round_number = max(available_round_numbers)

    for race_run, _pairing in run_items:
        normalized_round = "viertelfinale" if race_run.round_name == ROUND_PRELIMINARY else race_run.round_name

        if normalized_round not in ["viertelfinale", ROUND_SEMIFINAL]:
            continue

        round_number, round_label = get_round_display(race_run.round_name)

        if focus_race_classes is not None and race_run.race_class not in focus_race_classes:
            continue

        if display_round_number != round_number:
            continue

        for result in race_run.results:
            if result.raw_time_ms is None:
                continue

            valued_time_ms = get_monitor_valued_time_ms(result, db)

            if valued_time_ms is None:
                continue

            row_key = (
                result.participant_id,
                race_run.race_class,
                round_number,
            )
            existing = participant_rows.get(row_key)

            if existing is None:
                existing = {
                    "driver": monitor_driver_name(result.participant),
                    "race_class": race_run.race_class,
                    "round_label": round_label,
                    "round_number": round_number,
                    "raw_time_ms": 0,
                    "penalty": 0,
                    "valued_time_ms": 0,
                    "runs_count": 0,
                }
                participant_rows[row_key] = existing

            existing["raw_time_ms"] += normalize_time_to_centiseconds_ms(result.raw_time_ms) or 0
            existing["penalty"] += get_monitor_penalty_sum(result, db)
            existing["valued_time_ms"] += valued_time_ms
            existing["runs_count"] += 1

    rows = sorted(
        [
            {
                **row,
                "raw_time": format_time_ms(row["raw_time_ms"]),
                "valued_time": format_time_ms(row["valued_time_ms"]),
            }
            for row in participant_rows.values()
        ],
        key=lambda row: (
            row["race_class"],
            row["valued_time_ms"],
            row["penalty"],
            row["driver"],
        )
    )

    current_class = None
    previous_key = None
    current_place = 0
    class_position = 0

    for row in rows:
        if row["race_class"] != current_class:
            current_class = row["race_class"]
            class_position = 1
            current_place = 1
            previous_key = (row["race_class"], row["valued_time_ms"], row["penalty"])
        else:
            class_position += 1
            key = (row["race_class"], row["valued_time_ms"], row["penalty"])

            if key != previous_key:
                current_place = class_position
                previous_key = key

        row["place"] = current_place

    return rows[:limit]

def build_monitor_logo_signal_payload(
    settings: dict,
    setting: MonitorDisplaySetting,
    current_run: RaceRun | None,
) -> dict:
    visible = bool(setting.show_logo_signal)
    status = "off"

    logo_status = None

    if visible and settings.get("logo_enabled") == "true":
        logo_status = get_logo_status_payload()
        outputs = logo_status.get("outputs", {})

        if outputs.get("red"):
            status = "red"
        elif outputs.get("yellow"):
            status = "orange"
        elif outputs.get("green"):
            status = "green"

    if status == "off" and visible and current_run is not None:
        status = current_run.traffic_light_status or "off"

    if status == "yellow":
        status = "orange"

    if status not in ["red", "orange", "green"]:
        status = "off"

    return {
        "visible": visible,
        "status": status,
        "label": {
            "red": "ROT",
            "orange": "ORANGE",
            "green": "GRÜN",
            "off": "AUS",
        }.get(status, "AUS"),
        "mode": settings.get("logo_access_mode") or "inactive",
        "hardware": logo_status,
    }

def build_monitor_payload(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting | None = None,
) -> dict:
    if setting is None:
        setting = get_or_create_monitor_display_setting(event, db)

    active_run_id_str = get_setting(db, "startdisplay_active_run_id")
    active_run_id = int(active_run_id_str) if active_run_id_str and active_run_id_str.isdigit() else None

    run_items = get_monitor_runs(
        event_id=event.id,
        db=db,
    )

    current_item = pick_monitor_current_run(run_items, active_run_id=active_run_id)

    if current_item is not None:
        current_run, current_pairing = current_item
        live_status = sync_logo_times_for_run(current_run, db)
        current_payload = monitor_run_payload(
            current_run,
            current_pairing,
            db,
            live_status=live_status,
        )
        current_round_number, _current_round_label = get_round_display(current_run.round_name)

        if current_run.round_name == ROUND_FINAL:
            current_round_number = get_round_display(ROUND_SEMIFINAL)[0]
    else:
        current_payload = None
        current_run = None
        current_round_number = None

    logo_settings = get_logo_settings(db)

    return {
        "event": {
            "id": event.id,
            "event_number": event.event_number,
            "name": event.name,
            "date": event.event_date or "",
            "organizer_club": event.organizer_club or "",
            "active": bool(event.active),
        },
        "server_time": now_string(),
        "current_run": current_payload,
        "logo_signal": build_monitor_logo_signal_payload(
            settings=logo_settings,
            setting=setting,
            current_run=current_run,
        ),
        "next_runs": pick_monitor_next_runs(
            run_items=run_items,
            db=db,
            current_run=current_run,
        ),
        "live_results": get_monitor_live_result_runs(
            run_items=run_items,
            db=db,
            focus_race_classes=get_monitor_result_classes(
                current_run.race_class if current_run is not None else None
            ),
            preferred_round_number=current_round_number,
            limit=20,
        ),
        "final_note": "Finale wird nicht live angezeigt.",
    }
