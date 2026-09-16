from backend.models import Participant, Pairing, RaceRun, RunResult
from backend.time_utils import normalize_time_to_centiseconds_ms


ROUND_PRELIMINARY = "vorlauf"
ROUND_SEMIFINAL = "halbfinale"
ROUND_FINAL = "finale"


def is_participant_available(participant: Participant) -> bool:
    return participant.active is True and participant.withdrawn is not True


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


def get_valued_time_ms(result: RunResult) -> int | None:
    if result.raw_time_ms is None:
        return None

    if result.participant and result.participant.withdrawn:
        return None

    scoring_raw_time_ms = normalize_time_to_centiseconds_ms(result.raw_time_ms)

    if scoring_raw_time_ms is None:
        return None

    return scoring_raw_time_ms + (get_penalty_sum(result) * 1000)


def normalize_round_name(round_name: str) -> str:
    if round_name in ["vorlauf", "viertelfinale"]:
        return ROUND_PRELIMINARY

    return round_name


def is_result_complete(result: RunResult) -> bool:
    """
    Ein Ergebnis ist vollständig, wenn:
    - der Fahrer ausgeschieden ist
      oder
    - eine Zeit vorhanden ist
    """
    if result.participant and result.participant.withdrawn:
        return True

    return result.raw_time_ms is not None


def is_round_complete(
    pairings: list[Pairing],
    round_name: str,
) -> bool:
    normalized_target = normalize_round_name(round_name)

    round_pairings = [
        pairing for pairing in pairings
        if normalize_round_name(pairing.round_name) == normalized_target
    ]

    if not round_pairings:
        return False

    for pairing in round_pairings:
        if not pairing.runs:
            return False

        for race_run in pairing.runs:
            if not race_run.confirmed:
                return False

            for result in race_run.results:
                if not is_result_complete(result):
                    return False

    return True


def round_exists(
    pairings: list[Pairing],
    round_name: str,
) -> bool:
    normalized_target = normalize_round_name(round_name)

    return any(
        normalize_round_name(pairing.round_name) == normalized_target
        for pairing in pairings
    )


def get_participant_total_until_round(
    participant: Participant,
    pairings: list[Pairing],
    included_rounds: list[str],
) -> tuple[int, int] | None:
    if not is_participant_available(participant):
        return None

    normalized_included_rounds = [
        normalize_round_name(round_name)
        for round_name in included_rounds
    ]

    total_ms = 0
    total_penalty = 0
    has_result = False

    for pairing in pairings:
        if normalize_round_name(pairing.round_name) not in normalized_included_rounds:
            continue

        for race_run in pairing.runs:
            if not race_run.confirmed:
                continue

            for result in race_run.results:
                if result.participant_id != participant.id:
                    continue

                valued_time_ms = get_valued_time_ms(result)

                if valued_time_ms is None:
                    return None

                total_ms += valued_time_ms
                total_penalty += get_penalty_sum(result)
                has_result = True

    if not has_result:
        return None

    return total_ms, total_penalty


def get_ranked_participants_until_round(
    participants: list[Participant],
    pairings: list[Pairing],
    included_rounds: list[str],
) -> list[tuple[Participant, int, int]]:
    ranking: list[tuple[Participant, int, int]] = []

    for participant in participants:
        if not is_participant_available(participant):
            continue

        total = get_participant_total_until_round(
            participant=participant,
            pairings=pairings,
            included_rounds=included_rounds,
        )

        if total is not None:
            total_ms, total_penalty = total
            ranking.append((participant, total_ms, total_penalty))

    ranking.sort(
        key=lambda item: (
            item[1],
            item[2],
            item[0].start_number,
        )
    )

    return ranking


def has_unresolved_ranking_ties(
    participants: list[Participant],
    pairings: list[Pairing],
    included_rounds: list[str],
) -> bool:
    seen_keys: set[tuple[int, int]] = set()

    for participant in participants:
        if not is_participant_available(participant):
            continue

        total = get_participant_total_until_round(
            participant=participant,
            pairings=pairings,
            included_rounds=included_rounds,
        )

        if total is None:
            continue

        if total in seen_keys:
            return True

        seen_keys.add(total)

    return False


def create_runs_for_pairing(
    event_id: int,
    pairing: Pairing,
) -> list[RaceRun]:
    driver_1_id = pairing.driver_1_id
    driver_2_id = pairing.driver_2_id

    run_1 = RaceRun(
        event_id=event_id,
        pairing=pairing,
        race_class=pairing.race_class,
        round_name=pairing.round_name,
        run_number=1,
        lane_a_driver_id=driver_1_id,
        lane_b_driver_id=driver_2_id,
        lane_a_time_ms=None,
        lane_b_time_ms=None,
        status="created",
        confirmed=False,
        traffic_light_status="off",
        false_start_lane="none",
    )

    run_1.results = [
        RunResult(
            event_id=event_id,
            race_run=run_1,
            participant_id=driver_1_id,
            lane="lane_a",
            raw_time_ms=None,
            general_penalty=0,
            confirmed=False,
        )
    ]

    if driver_2_id is not None:
        run_1.results.append(
            RunResult(
                event_id=event_id,
                race_run=run_1,
                participant_id=driver_2_id,
                lane="lane_b",
                raw_time_ms=None,
                general_penalty=0,
                confirmed=False,
            )
        )

    if driver_2_id is not None:
        run_2_lane_a_driver_id = driver_2_id
        run_2_lane_b_driver_id = driver_1_id
    else:
        run_2_lane_a_driver_id = driver_1_id
        run_2_lane_b_driver_id = None

    run_2 = RaceRun(
        event_id=event_id,
        pairing=pairing,
        race_class=pairing.race_class,
        round_name=pairing.round_name,
        run_number=2,
        lane_a_driver_id=run_2_lane_a_driver_id,
        lane_b_driver_id=run_2_lane_b_driver_id,
        lane_a_time_ms=None,
        lane_b_time_ms=None,
        status="created",
        confirmed=False,
        traffic_light_status="off",
        false_start_lane="none",
    )

    run_2.results = []

    if driver_2_id is not None:
        run_2.results.append(
            RunResult(
                event_id=event_id,
                race_run=run_2,
                participant_id=driver_2_id,
                lane="lane_a",
                raw_time_ms=None,
                general_penalty=0,
                confirmed=False,
            )
        )

        run_2.results.append(
            RunResult(
                event_id=event_id,
                race_run=run_2,
                participant_id=driver_1_id,
                lane="lane_b",
                raw_time_ms=None,
                general_penalty=0,
                confirmed=False,
            )
        )
    else:
        run_2.results.append(
            RunResult(
                event_id=event_id,
                race_run=run_2,
                participant_id=driver_1_id,
                lane="lane_b",
                raw_time_ms=None,
                general_penalty=0,
                confirmed=False,
            )
        )

    return [run_1, run_2]


def create_pairings_from_pairs(
    event_id: int,
    race_class: int,
    round_name: str,
    pairs: list[tuple[Participant, Participant | None]],
) -> list[Pairing]:
    pairings: list[Pairing] = []
    pairing_number = 1

    for driver_1, driver_2 in pairs:
        if not is_participant_available(driver_1):
            continue

        if driver_2 is not None and not is_participant_available(driver_2):
            driver_2 = None

        pairing = Pairing(
            event_id=event_id,
            race_class=race_class,
            round_name=round_name,
            pairing_number=pairing_number,
            driver_1_id=driver_1.id,
            driver_2_id=driver_2.id if driver_2 else None,
            is_single_driver=driver_2 is None,
            status="created",
        )

        pairing.runs = create_runs_for_pairing(
            event_id=event_id,
            pairing=pairing,
        )

        pairings.append(pairing)
        pairing_number += 1

    return pairings


def create_pairings_from_order(
    event_id: int,
    race_class: int,
    round_name: str,
    ordered_participants: list[Participant],
) -> list[Pairing]:
    """
    Legacy-Helfer:
    Paart eine bereits vorbereitete Reihenfolge immer 1-2, 3-4, ...
    """
    pairs: list[tuple[Participant, Participant | None]] = []

    participants = [
        participant for participant in ordered_participants
        if is_participant_available(participant)
    ]

    while participants:
        driver_1 = participants.pop(0)

        if participants:
            driver_2 = participants.pop(0)
        else:
            driver_2 = None

        pairs.append((driver_1, driver_2))

    return create_pairings_from_pairs(
        event_id=event_id,
        race_class=race_class,
        round_name=round_name,
        pairs=pairs,
    )


def create_middle_out_pairs(
    ordered_participants: list[Participant],
) -> list[tuple[Participant, Participant | None]]:
    """
    Paart nach Reglement aus der sortierten Wertung von der Mitte heraus.

    Bei einer ungeraden Teilnehmerzahl bleibt zuerst der Letztplatzierte
    als Einzelfahrer übrig. Die verbleibende gerade Teilnehmerzahl wird
    anschließend von der Mitte nach außen gepaart.

    Beispiele:
    4 Fahrer: 2-3, 1-4
    5 Fahrer: 2-3, 1-4, 5-Freilos
    6 Fahrer: 3-4, 2-5, 1-6
    9 Fahrer: 4-5, 3-6, 2-7, 1-8, 9-Freilos

    Die Liste ordered_participants muss bereits nach Wertung sortiert sein:
    Platz 1, Platz 2, Platz 3, ...
    """
    participants = [
        participant for participant in ordered_participants
        if is_participant_available(participant)
    ]

    pairs: list[tuple[Participant, Participant | None]] = []

    if not participants:
        return pairs

    remaining = list(participants)
    bye_participant = remaining.pop() if len(remaining) % 2 == 1 else None

    while remaining:
        left_index = (len(remaining) // 2) - 1
        right_index = len(remaining) // 2

        driver_1 = remaining.pop(left_index)
        right_index -= 1
        driver_2 = remaining.pop(right_index)

        pairs.append((driver_1, driver_2))

    if bye_participant is not None:
        pairs.append((bye_participant, None))

    return pairs

def create_semifinal_pairings(
    event_id: int,
    race_class: int,
    participants: list[Participant],
    pairings: list[Pairing],
) -> list[Pairing]:
    ranking = get_ranked_participants_until_round(
        participants=participants,
        pairings=pairings,
        included_rounds=[ROUND_PRELIMINARY],
    )

    ordered = [
        participant for participant, _total_ms, _total_penalty in ranking
    ]

    middle_out_pairs = create_middle_out_pairs(ordered)

    return create_pairings_from_pairs(
        event_id=event_id,
        race_class=race_class,
        round_name=ROUND_SEMIFINAL,
        pairs=middle_out_pairs,
    )


def create_final_pairings(
    event_id: int,
    race_class: int,
    participants: list[Participant],
    pairings: list[Pairing],
) -> list[Pairing]:
    ranking = get_ranked_participants_until_round(
        participants=participants,
        pairings=pairings,
        included_rounds=[ROUND_PRELIMINARY, ROUND_SEMIFINAL],
    )

    ordered = [
        participant for participant, _total_ms, _total_penalty in ranking
    ]

    middle_out_pairs = create_middle_out_pairs(ordered)

    return create_pairings_from_pairs(
        event_id=event_id,
        race_class=race_class,
        round_name=ROUND_FINAL,
        pairs=middle_out_pairs,
    )
