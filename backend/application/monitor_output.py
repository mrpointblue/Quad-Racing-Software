from backend.models import Event
from backend.models import MonitorDisplaySetting
from backend.models import Pairing
from backend.models import Participant
from backend.round_logic import ROUND_FINAL
from backend.round_logic import ROUND_PRELIMINARY
from backend.round_logic import ROUND_SEMIFINAL
from sqlalchemy.orm import Session
from backend.models import Team
from backend.export_logic import build_class_export_rows
from backend.results_logic import build_compact_round_results
from backend.export_logic import build_overall_rows
from backend.statistics_logic import build_result_statistics
from backend.application.monitor_live import build_monitor_payload
from backend.application.monitor_qr import get_monitor_checkin_qr_payload, get_monitor_marshal_qr_payload
from backend.application.monitor_settings import get_or_create_monitor_display_setting, monitor_display_setting_payload, parse_monitor_csv
from backend.application.runs import get_round_display
from backend.application.teams import build_team_standings


def build_monitor_result_rows_for_round(
    event: Event,
    db: Session,
    race_class: int,
    round_name: str,
) -> list[dict]:
    round_key_by_name = {
        ROUND_PRELIMINARY: "quarterfinal",
        ROUND_SEMIFINAL: "semifinal",
        ROUND_FINAL: "final",
    }
    round_key = round_key_by_name.get(round_name)

    if round_key is None:
        return []

    participants = db.query(Participant).filter(
        Participant.event_id == event.id,
        Participant.race_class == race_class,
        Participant.active == True,
    ).all()

    pairings = db.query(Pairing).filter(
        Pairing.event_id == event.id,
        Pairing.race_class == race_class,
    ).all()

    compact_rows = build_compact_round_results(
        participants=participants,
        pairings=pairings,
        round_key=round_key,
    )

    return [
        {
            "place": row["place"],
            "start_number": row["start_number"],
            "driver": row["driver_name"],
            "run_1_lane_label": row["round_detail"]["run_1_lane_label"],
            "run_1_raw_display": row["round_detail"]["run_1_raw_display"],
            "run_1_penalty": row["round_detail"]["run_1_penalty"],
            "run_2_lane_label": row["round_detail"]["run_2_lane_label"],
            "run_2_raw_display": row["round_detail"]["run_2_raw_display"],
            "run_2_penalty": row["round_detail"]["run_2_penalty"],
            "round_total_display": row["round_detail"]["round_total_display"],
        }
        for row in compact_rows
    ]

def build_monitor_result_sections(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting,
) -> list[dict]:
    selected_classes = [
        int(item)
        for item in parse_monitor_csv(setting.display_classes)
        if item.isdigit() and int(item) in [1, 2, 3, 4, 5]
    ]
    selected_rounds = [
        item
        for item in parse_monitor_csv(setting.display_rounds)
        if item in [ROUND_PRELIMINARY, ROUND_SEMIFINAL, ROUND_FINAL]
    ]
    sections = []

    for race_class in selected_classes:
        for round_name in selected_rounds:
            rows = build_monitor_result_rows_for_round(
                event=event,
                db=db,
                race_class=race_class,
                round_name=round_name,
            )

            if not rows:
                continue

            _round_number, round_label = get_round_display(round_name)
            sections.append(
                {
                    "title": f"Ergebnisse Klasse {race_class} · {round_label}",
                    "rows": rows,
                }
            )

    return sections

def build_monitor_team_rows(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting,
) -> list[dict]:
    if not setting.show_team_results:
        return []

    teams = db.query(Team).filter(
        Team.event_id == event.id,
    ).order_by(
        Team.name,
    ).all()
    standings = build_team_standings(
        event=event,
        teams=teams,
        db=db,
    )

    return [
        {
            "place": row["place"],
            "team_name": row["team"].name,
            "points": row["points"],
            "members": [
                {
                    "driver": member["driver_name"],
                    "start_number": member["start_number"],
                    "race_class": member["race_class"],
                    "place": member["place"],
                    "points": member["points"],
                    "status": member["status"],
                    "withdrawn": member["withdrawn"],
                    "counted": member["counted"],
                }
                for member in row["members"]
            ],
        }
        for row in standings
    ]

def build_monitor_overall_sections(
    event: Event,
    db: Session,
    setting: MonitorDisplaySetting,
) -> list[dict]:
    if not setting.show_overall_results:
        return []

    selected_classes = [
        int(item)
        for item in parse_monitor_csv(setting.display_classes)
        if item.isdigit() and int(item) in [1, 2, 3, 4, 5]
    ]
    sections = []

    for race_class in selected_classes:
        pairings = db.query(Pairing).filter(
            Pairing.event_id == event.id,
            Pairing.race_class == race_class,
        ).all()
        rows_by_round = build_class_export_rows(pairings)
        overall_rows = [
            row
            for row in build_overall_rows(rows_by_round)
            if row.get("place") not in ["", None]
        ]

        if not overall_rows:
            continue

        sections.append(
            {
                "title": f"Gesamtwertung Klasse {race_class}",
                "rows": [
                    {
                        "place": row["place"],
                        "race_class": race_class,
                        "start_number": row["start_number"],
                        "driver": row["driver_name"],
                        "quarterfinal": row["quarterfinal_display"] or "-",
                        "semifinal": row["semifinal_display"] or "-",
                        "final": row["final_display"] or "-",
                        "total_penalty": row["total_penalty"],
                        "total": row["total_display"] or "-",
                    }
                    for row in overall_rows
                ],
            }
        )

    return sections

def build_monitor_output_payload(
    event: Event,
    db: Session,
) -> dict:
    setting = get_or_create_monitor_display_setting(event, db)
    race_payload = build_monitor_payload(event, db, setting=setting)

    if not setting.show_race_control:
        race_payload["current_run"] = None
        race_payload["next_runs"] = []

    race_payload["display_setting"] = monitor_display_setting_payload(setting)
    race_payload["result_sections"] = build_monitor_result_sections(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["overall_sections"] = build_monitor_overall_sections(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["team_results"] = build_monitor_team_rows(
        event=event,
        db=db,
        setting=setting,
    )
    race_payload["statistics"] = (
        build_result_statistics(
            event=event,
            db=db,
        )
        if setting.show_statistics
        else None
    )
    race_payload["marshal_qr"] = get_monitor_marshal_qr_payload(event, db)
    race_payload["checkin_qr"] = get_monitor_checkin_qr_payload(event, setting)
    race_payload["live_results"] = []

    return race_payload
