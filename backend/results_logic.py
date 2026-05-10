from backend.models import Participant, Pairing, RunResult
from backend.time_utils import (
    format_time_ms as format_centiseconds_time_ms,
    normalize_time_to_centiseconds_ms,
)


ROUND_PRELIMINARY = "viertelfinale"
ROUND_SEMIFINAL = "halbfinale"
ROUND_FINAL = "finale"


ROUND_LABELS = {
    ROUND_PRELIMINARY: "Viertelfinale",
    ROUND_SEMIFINAL: "Halbfinale",
    ROUND_FINAL: "Finale",
}


ROUND_ORDER = [
    ROUND_PRELIMINARY,
    ROUND_SEMIFINAL,
    ROUND_FINAL,
]

ROUND_KEY_CONFIG = {
    "quarterfinal": {
        "field": "preliminary",
        "label": "Viertelfinale",
    },
    "semifinal": {
        "field": "semifinal",
        "label": "Halbfinale",
    },
    "final": {
        "field": "final",
        "label": "Finale",
    },
}


UNRESOLVED_TIE_MESSAGE = (
    "Nicht aufgelöster Gleichstand erkannt: Gesamtzeit und Fehlerpunkte sind identisch. "
    "Es erfolgt keine automatische Entscheidung durch die Software. Die Rennleitung muss "
    "entscheiden; ggf. ist ein Stechen bzw. eine Wiederholung des Finallaufs erforderlich."
)


def normalize_round_name(round_name: str) -> str:
    if round_name in ["vorlauf", "viertelfinale"]:
        return ROUND_PRELIMINARY

    return round_name


def format_time_ms(time_ms: int | None) -> str:
    return format_centiseconds_time_ms(time_ms)


def get_penalty_sum(result: RunResult | None) -> int:
    if result is None:
        return 0

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


def get_valued_time_ms(result: RunResult | None) -> int | None:
    if result is None:
        return None

    if result.participant and result.participant.withdrawn:
        return None

    if result.raw_time_ms is None:
        return None

    scoring_raw_time_ms = normalize_time_to_centiseconds_ms(result.raw_time_ms)

    if scoring_raw_time_ms is None:
        return None

    return scoring_raw_time_ms + (get_penalty_sum(result) * 1000)


def get_participant_status(participant: Participant) -> str:
    if participant.withdrawn:
        if participant.withdrawn_reason:
            return f"Ausgeschieden – {participant.withdrawn_reason}"

        return "Ausgeschieden"

    return "Gewertet"


def build_empty_round_detail() -> dict:
    return {
        "run_1_raw_ms": None,
        "run_1_penalty": "",
        "run_1_result_ms": None,
        "run_1_raw_display": "",
        "run_1_result_display": "",

        "run_2_raw_ms": None,
        "run_2_penalty": "",
        "run_2_result_ms": None,
        "run_2_raw_display": "",
        "run_2_result_display": "",

        "round_total_ms": None,
        "round_total_display": "",
        "round_penalty": "",
    }


def get_result_for_participant(
    pairings: list[Pairing],
    participant_id: int,
    round_name: str,
    run_number: int,
) -> RunResult | None:
    normalized_target = normalize_round_name(round_name)

    for pairing in pairings:
        if normalize_round_name(pairing.round_name) != normalized_target:
            continue

        for race_run in pairing.runs:
            if race_run.run_number != run_number:
                continue

            for result in race_run.results:
                if result.participant_id == participant_id:
                    return result

    return None


def build_round_detail(
    result_index: dict[tuple[int, str, int], RunResult],
    participant_id: int,
    round_name: str,
) -> dict:
    normalized_round = normalize_round_name(round_name)
    run_1 = result_index.get((participant_id, normalized_round, 1))
    run_2 = result_index.get((participant_id, normalized_round, 2))

    run_1_penalty = get_penalty_sum(run_1)
    run_2_penalty = get_penalty_sum(run_2)

    run_1_result_ms = get_valued_time_ms(run_1)
    run_2_result_ms = get_valued_time_ms(run_2)

    if run_1_result_ms is not None and run_2_result_ms is not None:
        total_ms = run_1_result_ms + run_2_result_ms
    else:
        total_ms = None

    return {
        "run_1_raw_ms": normalize_time_to_centiseconds_ms(run_1.raw_time_ms) if run_1 else None,
        "run_1_penalty": run_1_penalty,
        "run_1_result_ms": run_1_result_ms,
        "run_1_raw_display": format_time_ms(run_1.raw_time_ms if run_1 else None),
        "run_1_result_display": format_time_ms(run_1_result_ms),

        "run_2_raw_ms": normalize_time_to_centiseconds_ms(run_2.raw_time_ms) if run_2 else None,
        "run_2_penalty": run_2_penalty,
        "run_2_result_ms": run_2_result_ms,
        "run_2_raw_display": format_time_ms(run_2.raw_time_ms if run_2 else None),
        "run_2_result_display": format_time_ms(run_2_result_ms),

        "round_total_ms": total_ms,
        "round_total_display": format_time_ms(total_ms),
        "round_penalty": run_1_penalty + run_2_penalty,
    }


def build_class_results(
    participants: list[Participant],
    pairings: list[Pairing],
) -> list[dict]:
    result_rows = []
    result_index: dict[tuple[int, str, int], RunResult] = {}

    for pairing in pairings:
        normalized_round = normalize_round_name(pairing.round_name)

        for race_run in pairing.runs:
            for result in race_run.results:
                result_index[
                    (result.participant_id, normalized_round, race_run.run_number)
                ] = result

    for participant in participants:
        if participant.withdrawn:
            preliminary = build_empty_round_detail()
            semifinal = build_empty_round_detail()
            final = build_empty_round_detail()

            result_rows.append(
                {
                    "place": "",
                    "participant": participant,
                    "start_number": participant.start_number,
                    "driver_name": f"{participant.first_name} {participant.last_name}",
                    "club": participant.club or "",
                    "race_class": participant.race_class,
                    "status": get_participant_status(participant),
                    "withdrawn": True,
                    "withdrawn_reason": participant.withdrawn_reason or "",

                    "preliminary": preliminary,
                    "semifinal": semifinal,
                    "final": final,

                    "preliminary_display": "",
                    "semifinal_display": "",
                    "final_display": "",

                    "total_ms": None,
                    "total_display": "",
                    "total_penalty": "",
                    "unresolved_tie": False,
                }
            )
            continue

        preliminary = build_round_detail(
            result_index=result_index,
            participant_id=participant.id,
            round_name=ROUND_PRELIMINARY,
        )

        semifinal = build_round_detail(
            result_index=result_index,
            participant_id=participant.id,
            round_name=ROUND_SEMIFINAL,
        )

        final = build_round_detail(
            result_index=result_index,
            participant_id=participant.id,
            round_name=ROUND_FINAL,
        )

        total_ms = 0
        has_any_result = False

        for round_detail in [preliminary, semifinal, final]:
            if round_detail["round_total_ms"] is not None:
                total_ms += round_detail["round_total_ms"]
                has_any_result = True

        if not has_any_result:
            continue

        total_penalty = (
            preliminary["round_penalty"]
            + semifinal["round_penalty"]
            + final["round_penalty"]
        )

        result_rows.append(
            {
                "place": None,
                "participant": participant,
                "start_number": participant.start_number,
                "driver_name": f"{participant.first_name} {participant.last_name}",
                "club": participant.club or "",
                "race_class": participant.race_class,
                "status": get_participant_status(participant),
                "withdrawn": False,
                "withdrawn_reason": "",

                "preliminary": preliminary,
                "semifinal": semifinal,
                "final": final,

                "preliminary_display": preliminary["round_total_display"],
                "semifinal_display": semifinal["round_total_display"],
                "final_display": final["round_total_display"],

                "total_ms": total_ms,
                "total_display": format_time_ms(total_ms),
                "total_penalty": total_penalty,
            }
        )

    result_rows.sort(
        key=lambda row: (
            row["withdrawn"],
            row["total_ms"] is None,
            row["total_ms"],
            row["total_penalty"] if isinstance(row["total_penalty"], int) else 999999,
            row["start_number"],
        )
    )

    previous_key = None
    current_place = 0

    for index, row in enumerate(result_rows, start=1):
        if row["withdrawn"] or row["total_ms"] is None:
            row["place"] = ""
            row["unresolved_tie"] = False
            continue

        key = (row["total_ms"], row["total_penalty"])

        if key != previous_key:
            current_place = index
            previous_key = key

        row["place"] = current_place
        row["unresolved_tie"] = False

    return result_rows


def build_tie_warnings(
    result_rows: list[dict],
    race_class: int,
) -> list[dict]:
    grouped_rows: dict[tuple[int, int], list[dict]] = {}

    for row in result_rows:
        total_ms = row.get("total_ms")
        total_penalty = row.get("total_penalty")

        if total_ms is None or not isinstance(total_penalty, int):
            continue

        grouped_rows.setdefault((total_ms, total_penalty), []).append(row)

    warnings = []

    for (_total_ms, total_penalty), rows in grouped_rows.items():
        if len(rows) < 2:
            continue

        for row in rows:
            row["unresolved_tie"] = True

        warnings.append(
            {
                "race_class": race_class,
                "place": rows[0].get("place"),
                "total_display": rows[0].get("total_display", ""),
                "total_penalty": total_penalty,
                "drivers": [
                    f"#{row.get('start_number')} {row.get('driver_name')}"
                    for row in rows
                ],
            }
        )

    return warnings


def build_round_tie_warnings(
    result_rows: list[dict],
    race_class: int,
    round_label: str,
) -> list[dict]:
    grouped_rows: dict[tuple[int, int], list[dict]] = {}

    for row in result_rows:
        round_detail = row.get("round_detail") or {}
        total_ms = round_detail.get("round_total_ms")
        total_penalty = round_detail.get("round_penalty")

        if total_ms is None or not isinstance(total_penalty, int):
            continue

        grouped_rows.setdefault((total_ms, total_penalty), []).append(row)

    warnings = []

    for (_total_ms, total_penalty), rows in grouped_rows.items():
        if len(rows) < 2:
            continue

        for row in rows:
            row["unresolved_tie"] = True

        warnings.append(
            {
                "race_class": race_class,
                "round_label": round_label,
                "place": rows[0].get("place"),
                "total_display": (rows[0].get("round_detail") or {}).get("round_total_display", ""),
                "total_penalty": total_penalty,
                "drivers": [
                    f"#{row.get('start_number')} {row.get('driver_name')}"
                    for row in rows
                ],
            }
        )

    return warnings


def get_round_key_config(round_key: str) -> dict | None:
    return ROUND_KEY_CONFIG.get(round_key)


def build_compact_round_results(
    participants: list[Participant],
    pairings: list[Pairing],
    round_key: str,
) -> list[dict]:
    round_config = get_round_key_config(round_key)

    if not round_config:
        return []

    result_rows = []

    for row in build_class_results(
        participants=participants,
        pairings=pairings,
    ):
        round_detail = row[round_config["field"]]

        if row.get("withdrawn"):
            result_rows.append(
                {
                    "place": "",
                    "start_number": row["start_number"],
                    "driver_name": row["driver_name"],
                    "club": row["club"],
                    "status": row.get("status", "Ausgeschieden"),
                    "withdrawn": True,
                    "round_detail": round_detail,
                    "unresolved_tie": False,
                }
            )
            continue

        if round_detail["round_total_ms"] is None:
            continue

        result_rows.append(
            {
                "place": None,
                "start_number": row["start_number"],
                "driver_name": row["driver_name"],
                "club": row["club"],
                "status": row.get("status", "Gewertet"),
                "withdrawn": False,
                "round_detail": round_detail,
            }
        )

    result_rows.sort(
        key=lambda row: (
            row.get("withdrawn", False),
            row["round_detail"]["round_total_ms"] is None,
            row["round_detail"]["round_total_ms"],
            row["round_detail"]["round_penalty"],
            row["start_number"],
        )
    )

    previous_key = None
    current_place = 0

    for index, row in enumerate(result_rows, start=1):
        if row.get("withdrawn") or row["round_detail"]["round_total_ms"] is None:
            row["place"] = ""
            row["unresolved_tie"] = False
            continue

        round_detail = row["round_detail"]
        key = (
            round_detail["round_total_ms"],
            round_detail["round_penalty"],
        )

        if key != previous_key:
            current_place = index
            previous_key = key

        row["place"] = current_place
        row["unresolved_tie"] = False

    return result_rows
