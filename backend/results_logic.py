from backend.models import Participant, Pairing, RunResult


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


def normalize_round_name(round_name: str) -> str:
    if round_name in ["vorlauf", "viertelfinale"]:
        return ROUND_PRELIMINARY

    return round_name


def format_time_ms(time_ms: int | None) -> str:
    if time_ms is None:
        return "—"

    total_centiseconds = round(time_ms / 10)
    minutes = total_centiseconds // 6000
    seconds = (total_centiseconds % 6000) // 100
    hundredths = total_centiseconds % 100

    return f"{minutes:02d}:{seconds:02d},{hundredths:02d}"


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

    return result.raw_time_ms + (get_penalty_sum(result) * 1000)


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
        "run_1_raw_ms": run_1.raw_time_ms if run_1 else None,
        "run_1_penalty": run_1_penalty,
        "run_1_result_ms": run_1_result_ms,
        "run_1_raw_display": format_time_ms(run_1.raw_time_ms if run_1 else None),
        "run_1_result_display": format_time_ms(run_1_result_ms),

        "run_2_raw_ms": run_2.raw_time_ms if run_2 else None,
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
            row["total_ms"],
            row["total_penalty"],
            row["start_number"],
        )
    )

    current_place = 1

    for row in result_rows:
        row["place"] = current_place
        current_place += 1

    return result_rows


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

        if round_detail["round_total_ms"] is None:
            continue

        result_rows.append(
            {
                "place": None,
                "start_number": row["start_number"],
                "driver_name": row["driver_name"],
                "club": row["club"],
                "round_detail": round_detail,
            }
        )

    result_rows.sort(
        key=lambda row: (
            row["round_detail"]["round_total_ms"],
            row["round_detail"]["round_penalty"],
            row["start_number"],
        )
    )

    for index, row in enumerate(result_rows, start=1):
        row["place"] = index

    return result_rows
