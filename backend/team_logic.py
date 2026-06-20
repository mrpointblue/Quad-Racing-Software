from sqlalchemy.orm import Session

from backend.models import Event, Pairing, Participant, Team
from backend.results_logic import build_class_results, get_participant_status


POINTS_TABLE_C = {
    1: 25,
    2: 22,
    3: 20,
    4: 18,
    5: 16,
    6: 15,
    7: 14,
    8: 13,
    9: 12,
    10: 11,
    11: 10,
    12: 9,
    13: 8,
    14: 7,
    15: 6,
    16: 5,
    17: 4,
    18: 3,
    19: 2,
    20: 1,
}


def sort_place_or_last(place) -> int:
    if isinstance(place, int):
        return place

    return 999


def get_points_table_c(place: int | None, has_result: bool) -> int:
    if not has_result or place is None:
        return 0

    return POINTS_TABLE_C.get(place, 1)


def build_participant_points(
    event: Event,
    db: Session,
) -> dict[int, dict]:
    points_by_participant = {}

    for race_class in [1, 2, 3, 4, 5]:
        participants = db.query(Participant).filter(
            Participant.event_id == event.id,
            Participant.race_class == race_class,
            Participant.active == True,
        ).all()

        pairings = db.query(Pairing).filter(
            Pairing.event_id == event.id,
            Pairing.race_class == race_class,
        ).all()

        result_rows = build_class_results(
            participants=participants,
            pairings=pairings,
        )

        for row in result_rows:
            participant = row["participant"]
            points_by_participant[participant.id] = {
                "place": row["place"],
                "points": get_points_table_c(
                    place=row["place"],
                    has_result=not row.get("withdrawn", False),
                ),
                "total_display": row["total_display"],
                "status": row.get("status") or get_participant_status(participant),
                "withdrawn": row.get("withdrawn", False),
            }

    return points_by_participant


def build_team_standings(
    event: Event,
    teams: list[Team],
    db: Session,
) -> list[dict]:
    points_by_participant = build_participant_points(event, db)
    standings = []

    for team in teams:
        member_rows = []

        for member in team.members:
            participant = member.participant

            if not participant:
                continue

            participant_points = points_by_participant.get(
                participant.id,
                {
                    "place": None,
                    "points": 0,
                    "total_display": "-",
                    "status": get_participant_status(participant),
                    "withdrawn": participant.withdrawn is True,
                },
            )

            member_rows.append(
                {
                    "participant": participant,
                    "driver_name": f"{participant.first_name} {participant.last_name}",
                    "start_number": participant.start_number,
                    "race_class": participant.race_class,
                    "place": participant_points["place"],
                    "points": participant_points["points"],
                    "total_display": participant_points["total_display"],
                    "status": participant_points["status"],
                    "withdrawn": participant_points["withdrawn"],
                    "counted": False,
                }
            )

        member_rows.sort(
            key=lambda row: (
                -row["points"],
                sort_place_or_last(row["place"]),
                row["start_number"],
            )
        )

        counted_points = 0

        for index, member_row in enumerate(member_rows):
            if index < 3:
                member_row["counted"] = True
                counted_points += member_row["points"]

        standings.append(
            {
                "place": None,
                "team": team,
                "members": member_rows,
                "counted_members": [row for row in member_rows if row["counted"]],
                "dropped_members": [row for row in member_rows if not row["counted"]],
                "points": counted_points,
            }
        )

    standings.sort(
        key=lambda row: (
            -row["points"],
            row["team"].name.lower(),
        )
    )

    for index, row in enumerate(standings, start=1):
        row["place"] = index

    return standings
