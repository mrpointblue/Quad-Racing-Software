from backend.models import Pairing
from backend.models import Participant
from backend.models import RaceRun
from backend.models import RunPenalty
from backend.models import RunResult
from sqlalchemy.orm import Session
from backend.time_utils import format_time_ms as format_centiseconds_time_ms
from backend.time_utils import normalize_time_to_centiseconds_ms
from backend.common import now_string
from backend.constants import CLASS_ROUND_CONFIGS
from backend.application.settings_service import get_setting


def format_time_ms(time_ms: int | None) -> str:
    return format_centiseconds_time_ms(time_ms)

def get_penalty_for_sector(result: RunResult, sector: int) -> int:
    return getattr(result, f"sector_{sector}_penalty", 0) or 0

def set_penalty_for_sector(result: RunResult, sector: int, value: int):
    setattr(result, f"sector_{sector}_penalty", value)

def prepare_result_for_display(result: RunResult, sectors: list[int]) -> RunResult:
    sector_penalties = {}

    for sector in sectors:
        sector_penalties[sector] = get_penalty_for_sector(result, sector)

    total_penalty_seconds = sum(sector_penalties.values()) + (result.general_penalty or 0)

    if result.participant and result.participant.withdrawn:
        valued_time_ms = None
    elif result.raw_time_ms is not None:
        scoring_raw_time_ms = normalize_time_to_centiseconds_ms(result.raw_time_ms)
        valued_time_ms = scoring_raw_time_ms + (total_penalty_seconds * 1000)
    else:
        valued_time_ms = None

    result.sector_penalties = sector_penalties
    result.total_penalty_seconds = total_penalty_seconds
    result.valued_time_ms = valued_time_ms
    result.valued_time_display = format_time_ms(valued_time_ms)

    return result

def result_has_repeatable_values(result: RunResult) -> bool:
    if result.raw_time_ms is not None:
        return True

    if result.general_penalty:
        return True

    for sector in range(1, 11):
        if get_penalty_for_sector(result, sector):
            return True

    return False

def get_digital_penalty_status(
    run_penalties: list[RunPenalty],
    penalty_type: str,
    lane: str,
    sector: int | None = None,
) -> dict:
    matching_penalty = None

    for penalty in run_penalties:
        if penalty.penalty_type != penalty_type or penalty.lane != lane:
            continue

        if penalty_type == "sector" and penalty.sector != sector:
            continue

        if penalty_type == "general" and penalty.sector not in [None, 0]:
            continue

        matching_penalty = penalty
        break

    if not matching_penalty:
        return {
            "label": "Allgemein" if penalty_type == "general" else f"Position {sector}",
            "points": None,
            "has_submission": False,
            "status": "keine Meldung",
            "changed": False,
        }

    points = matching_penalty.points or 0
    changed = bool(matching_penalty.updated_at)

    return {
        "label": "Allgemein" if penalty_type == "general" else f"Position {sector}",
        "points": points,
        "has_submission": True,
        "status": f"{points} FP übermittelt",
        "changed": changed,
    }

def build_digital_penalty_statuses(
    race_run: RaceRun,
    result: RunResult,
    sectors: list[int],
    run_penalties: list[RunPenalty],
) -> list[dict]:
    lane = "A" if result.lane == "lane_a" else "B"
    statuses = [
        get_digital_penalty_status(
            run_penalties=run_penalties,
            penalty_type="general",
            lane=lane,
        )
    ]

    for sector in sectors:
        statuses.append(
            get_digital_penalty_status(
                run_penalties=run_penalties,
                penalty_type="sector",
                lane=lane,
                sector=sector,
            )
        )

    return statuses

def get_lane_results_for_run(run: RaceRun) -> tuple[RunResult | None, RunResult | None]:
    lane_a = None
    lane_b = None

    for result in run.results:
        if result.lane == "lane_a":
            lane_a = result

        if result.lane == "lane_b":
            lane_b = result

    return lane_a, lane_b

def get_round_display(round_name: str) -> tuple[int, str]:
    if round_name in ["vorlauf", "viertelfinale"]:
        return 1, "Viertelfinale"

    if round_name == "halbfinale":
        return 2, "Halbfinale"

    if round_name == "finale":
        return 3, "Finale"

    return 0, round_name

def reset_run_values_for_repeat(
    race_run: RaceRun,
    results: list[RunResult],
    repeat_scope: str = "both",
):
    if repeat_scope not in ["lane_a", "lane_b", "both"]:
        repeat_scope = "both"

    reset_lanes = ["lane_a", "lane_b"] if repeat_scope == "both" else [repeat_scope]

    race_run.status = "created"
    race_run.confirmed = False
    race_run.traffic_light_status = "off"
    race_run.false_start_lane = "none"
    race_run.started_at = None
    race_run.finished_at = None

    if "lane_a" in reset_lanes:
        race_run.lane_a_time_ms = None

    if "lane_b" in reset_lanes:
        race_run.lane_b_time_ms = None

    race_run.reset_count = (race_run.reset_count or 0) + 1
    race_run.last_reset_at = now_string()
    race_run.last_reset_by = "Wiederholung"

    for result in results:
        if result.lane not in reset_lanes:
            continue

        result.raw_time_ms = None
        result.general_penalty = 0
        result.confirmed = False

        for sector in range(1, 11):
            set_penalty_for_sector(result, sector, 0)

def repeat_scope_setting_key(run_id: int) -> str:
    return f"repeat_scope_run_{run_id}"

def normalize_repeat_scope(value: str | None) -> str:
    value = (value or "both").strip().lower()

    if value in ["lane_a", "lane_b", "both"]:
        return value

    return "both"

def get_run_repeat_scope(db: Session, run_id: int) -> str:
    return normalize_repeat_scope(
        get_setting(db, repeat_scope_setting_key(run_id), "both")
    )

def is_lane_editable_for_repeat_scope(lane: str, repeat_scope: str) -> bool:
    return repeat_scope == "both" or lane == repeat_scope

def normalize_penalty_input(value: str | None) -> int:
    if value is None or str(value).strip() == "":
        return 0

    penalty = max(0, int(value))
    return ((penalty + 2) // 5) * 5

def get_run_start_order_key(race_run: RaceRun, pairing: Pairing) -> tuple:
    round_number, _round_label = get_round_display(race_run.round_name)

    return (
        round_number,
        pairing.pairing_number,
        race_run.run_number,
    )

def get_start_ordered_class_runs(
    event_id: int,
    race_class: int,
    db: Session,
) -> list[tuple[RaceRun, Pairing]]:
    pairings = db.query(Pairing).filter(
        Pairing.event_id == event_id,
        Pairing.race_class == race_class,
    ).all()

    items: list[tuple[RaceRun, Pairing]] = []

    for pairing in pairings:
        for race_run in pairing.runs:
            items.append((race_run, pairing))

    items.sort(
        key=lambda item: get_run_start_order_key(
            race_run=item[0],
            pairing=item[1],
        )
    )

    return items

def pick_next_unconfirmed_run(
    run_items: list[tuple[RaceRun, Pairing]],
) -> tuple[RaceRun, Pairing] | None:
    for race_run, pairing in run_items:
        if not race_run.confirmed:
            return race_run, pairing
    return None

def build_run_navigation_payload(
    race_run: RaceRun,
    pairing: Pairing,
) -> dict:
    round_number, round_label = get_round_display(race_run.round_name)

    return {
        "id": race_run.id,
        "race_class": race_run.race_class,
        "round_number": round_number,
        "round_label": round_label,
        "pairing_number": pairing.pairing_number,
        "run_number": race_run.run_number,
        "status": race_run.status,
    }

def get_class_round_config(round_key: str) -> dict | None:
    for config in CLASS_ROUND_CONFIGS:
        if config["key"] == round_key:
            return config

    return None

def participant_start_label(participant: Participant | None) -> str:
    if participant is None or participant.withdrawn:
        return "-"

    return f"#{participant.start_number} {participant.first_name} {participant.last_name}"

def reset_active_run_hardware(event_id: int, db: Session) -> None:
    active_run_id = None
    active_run_id_str = get_setting(db, "startdisplay_active_run_id")

    if active_run_id_str and active_run_id_str.isdigit():
        active_run_id = int(active_run_id_str)

    if active_run_id is None:
        return

    race_run = db.query(RaceRun).filter(
        RaceRun.event_id == event_id,
        RaceRun.id == active_run_id,
    ).first()

    if not race_run:
        return

    race_run.traffic_light_status = "off"
    race_run.false_start_lane = "none"
    race_run.last_reset_at = now_string()
    race_run.last_reset_by = "Zeitmessanlage"
    race_run.reset_count = (race_run.reset_count or 0) + 1

    if race_run.status in ["running", "started"]:
        race_run.status = "ready"

    db.commit()
