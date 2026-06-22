from sqlalchemy.orm import Session

from backend.export_logic import build_class_export_rows, build_overall_rows
from backend.models import Event, Pairing, RaceRun, RunResult
from backend.results_logic import format_time_ms
from backend.round_logic import ROUND_FINAL, ROUND_PRELIMINARY, ROUND_SEMIFINAL
from backend.time_utils import normalize_time_to_centiseconds_ms


def sort_int_or_last(value, fallback: int = 999999999) -> int:
    if isinstance(value, int):
        return value

    return fallback


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
    lane_label = "Bahn A" if result.lane == "lane_a" else "Bahn B"
    pairing_number = race_run.pairing.pairing_number if race_run.pairing else 0

    return (
        f"{round_labels.get(race_run.round_name, race_run.round_name)} "
        f"· Kl. {race_run.race_class} "
        f"· Lauf {pairing_number}.{race_run.run_number} "
        f"· {lane_label}"
    )


def build_adac_overall_rows(
    event: Event,
    db: Session,
) -> list[dict]:
    rows = []

    for race_class in [1, 2, 3, 4, 5]:
        pairings = db.query(Pairing).filter(
            Pairing.event_id == event.id,
            Pairing.race_class == race_class,
        ).all()
        rows_by_round = build_class_export_rows(pairings)

        for row in build_overall_rows(rows_by_round):
            rows.append(
                {
                    **row,
                    "race_class": race_class,
                }
            )

    return rows


def build_run_count_statistics(
    event: Event,
    db: Session,
) -> dict:
    race_runs = db.query(RaceRun).filter(
        RaceRun.event_id == event.id,
    ).all()

    started_runs_count = len(
        [
            race_run
            for race_run in race_runs
            if race_run.started_at or race_run.status != "created"
        ]
    )
    finished_runs_count = len(
        [
            race_run
            for race_run in race_runs
            if race_run.status in ["finished", "confirmed"] or race_run.confirmed
        ]
    )
    confirmed_runs_count = len(
        [
            race_run
            for race_run in race_runs
            if race_run.confirmed
        ]
    )

    return {
        "started_runs_count": started_runs_count,
        "finished_runs_count": finished_runs_count,
        "confirmed_runs_count": confirmed_runs_count,
    }


def build_result_statistics(
    event: Event,
    db: Session,
    adac_overall_rows: list[dict] | None = None,
) -> dict:
    if adac_overall_rows is None:
        adac_overall_rows = build_adac_overall_rows(event, db)

    results = db.query(RunResult).filter(
        RunResult.event_id == event.id,
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

    scored_overall_rows = [
        row
        for row in adac_overall_rows
        if row.get("total_ms") is not None
        and isinstance(row.get("total_penalty"), int)
    ]

    if scored_overall_rows:
        best_row = sorted(
            scored_overall_rows,
            key=lambda row: (
                sort_int_or_last(row.get("total_ms")),
                sort_int_or_last(row.get("total_penalty")),
                sort_int_or_last(row.get("start_number")),
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
                normalize_time_to_centiseconds_ms(item.raw_time_ms),
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
        **build_run_count_statistics(event, db),
    }
