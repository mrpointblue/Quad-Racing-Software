from backend.models import Participant, Pairing, RaceRun, RunResult


def create_run_results_for_run(
    event_id: int,
    race_run: RaceRun,
    lane_a_driver_id: int | None,
    lane_b_driver_id: int | None,
) -> list[RunResult]:
    results: list[RunResult] = []

    if lane_a_driver_id is not None:
        results.append(
            RunResult(
                event_id=event_id,
                race_run=race_run,
                participant_id=lane_a_driver_id,
                lane="lane_a",
                raw_time_ms=None,
                sector_1_penalty=0,
                sector_2_penalty=0,
                sector_3_penalty=0,
                sector_4_penalty=0,
                sector_5_penalty=0,
                sector_6_penalty=0,
                sector_7_penalty=0,
                sector_8_penalty=0,
                sector_9_penalty=0,
                sector_10_penalty=0,
                general_penalty=0,
                confirmed=False,
            )
        )

    if lane_b_driver_id is not None:
        results.append(
            RunResult(
                event_id=event_id,
                race_run=race_run,
                participant_id=lane_b_driver_id,
                lane="lane_b",
                raw_time_ms=None,
                sector_1_penalty=0,
                sector_2_penalty=0,
                sector_3_penalty=0,
                sector_4_penalty=0,
                sector_5_penalty=0,
                sector_6_penalty=0,
                sector_7_penalty=0,
                sector_8_penalty=0,
                sector_9_penalty=0,
                sector_10_penalty=0,
                general_penalty=0,
                confirmed=False,
            )
        )

    return results


def create_two_runs_for_pairing(
    event_id: int,
    pairing: Pairing,
) -> list[RaceRun]:
    """
    Aus einer Paarung entstehen immer zwei Wertungsläufe.

    Anzeige:
    pairing_number=1, run_number=1 -> Lauf 1.1
    pairing_number=1, run_number=2 -> Lauf 1.2

    Normalfall:
    Lauf x.1: Fahrer 1 Bahn A, Fahrer 2 Bahn B
    Lauf x.2: Fahrer 2 Bahn A, Fahrer 1 Bahn B

    Einzelfahrer:
    Lauf x.1: Fahrer Bahn A
    Lauf x.2: Fahrer Bahn B
    """

    driver_1_id = pairing.driver_1_id
    driver_2_id = pairing.driver_2_id

    # Lauf x.1
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
    )

    run_1.results = create_run_results_for_run(
        event_id=event_id,
        race_run=run_1,
        lane_a_driver_id=driver_1_id,
        lane_b_driver_id=driver_2_id,
    )

    # Lauf x.2 mit Bahnwechsel
    if driver_2_id is not None:
        # normale Paarung: Fahrer tauschen die Bahnen
        run_2_lane_a_driver_id = driver_2_id
        run_2_lane_b_driver_id = driver_1_id
    else:
        # Einzelfahrer: zweiter Lauf auf Bahn B
        run_2_lane_a_driver_id = None
        run_2_lane_b_driver_id = driver_1_id

    run_2 = RaceRun(
        event_id=event_id,
        pairing=pairing,
        race_class=pairing.race_class,
        round_name=pairing.round_name,
        run_number=2,
        lane_a_driver_id=run_2_lane_a_driver_id if run_2_lane_a_driver_id else driver_1_id,
        lane_b_driver_id=run_2_lane_b_driver_id,
        lane_a_time_ms=None,
        lane_b_time_ms=None,
        status="created",
        confirmed=False,
    )

    # Achtung:
    # lane_a_driver_id in RaceRun ist aktuell im Datenmodell nullable=False.
    # Deshalb muss technisch ein Fahrer in lane_a_driver_id stehen.
    # Die echte Bahnbelegung für die Anzeige/Ergebniszeile kommt aus RunResult.lane.
    if driver_2_id is None:
        run_2.results = create_run_results_for_run(
            event_id=event_id,
            race_run=run_2,
            lane_a_driver_id=None,
            lane_b_driver_id=driver_1_id,
        )
    else:
        run_2.results = create_run_results_for_run(
            event_id=event_id,
            race_run=run_2,
            lane_a_driver_id=run_2_lane_a_driver_id,
            lane_b_driver_id=run_2_lane_b_driver_id,
        )

    return [run_1, run_2]


def create_outer_pairs_by_start_number(
    participants: list[Participant],
) -> list[tuple[Participant, Participant | None]]:
    """
    Sortiert numerisch nach Startnummer und paart von außen nach innen.

    Beispiele:
    8 Fahrer: 1-8, 2-7, 3-6, 4-5
    5 Fahrer: 1-5, 2-4, 3-Freilos
    """
    remaining = sorted(
        participants,
        key=lambda participant: participant.start_number,
    )

    pairs: list[tuple[Participant, Participant | None]] = []

    while remaining:
        driver_1 = remaining.pop(0)
        driver_2 = remaining.pop(-1) if remaining else None
        pairs.append((driver_1, driver_2))

    return pairs


def create_start_number_preliminary_pairings(
    participants: list[Participant],
    race_class: int,
    event_id: int,
) -> list[Pairing]:
    pairs = create_outer_pairs_by_start_number(participants)
    pairings: list[Pairing] = []

    for pairing_number, (driver_1, driver_2) in enumerate(pairs, start=1):
        pairing = Pairing(
            event_id=event_id,
            race_class=race_class,
            round_name="viertelfinale",
            pairing_number=pairing_number,
            driver_1_id=driver_1.id,
            driver_2_id=driver_2.id if driver_2 else None,
            is_single_driver=driver_2 is None,
            status="created",
        )

        pairing.runs = create_two_runs_for_pairing(
            event_id=event_id,
            pairing=pairing,
        )
        pairings.append(pairing)

    return pairings


def create_random_preliminary_pairings(
    participants: list[Participant],
    race_class: int,
    event_id: int,
) -> list[Pairing]:
    """
    Kompatibilitätsname für bestehende Aufrufer.

    Die Paarungen sind nicht mehr zufällig, sondern werden nach Startnummer
    von außen nach innen gebildet.
    """
    return create_start_number_preliminary_pairings(
        participants=participants,
        race_class=race_class,
        event_id=event_id,
    )
