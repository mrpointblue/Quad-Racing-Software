from io import BytesIO
from datetime import datetime

import xlsxwriter

from backend.models import Event, Pairing, RunResult


ROUND_LABELS = {
    "quarterfinal": "Viertelfinale",
    "semifinal": "Halbfinale",
    "final": "Finale",
}

ROUND_NUMBERS = {
    "quarterfinal": 1,
    "semifinal": 2,
    "final": 3,
}

ROUND_ORDER = {
    "quarterfinal": 1,
    "semifinal": 2,
    "final": 3,
}


def normalize_round_key(round_name: str) -> str:
    if round_name in ["vorlauf", "viertelfinale"]:
        return "quarterfinal"

    if round_name == "halbfinale":
        return "semifinal"

    if round_name == "finale":
        return "final"

    return round_name


def now_export_string() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def format_time_ms(time_ms: int | None) -> str:
    if time_ms is None:
        return ""

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


def is_participant_withdrawn(result: RunResult | None) -> bool:
    if result is None:
        return False

    if result.participant is None:
        return False

    return result.participant.withdrawn is True


def get_valued_time_ms(result: RunResult | None) -> int | None:
    if result is None:
        return None

    if is_participant_withdrawn(result):
        return None

    if result.raw_time_ms is None:
        return None

    return result.raw_time_ms + (get_penalty_sum(result) * 1000)


def get_result_for_participant(
    pairing: Pairing,
    run_number: int,
    participant_id: int,
) -> RunResult | None:
    for race_run in pairing.runs:
        if race_run.run_number != run_number:
            continue

        for result in race_run.results:
            if result.participant_id == participant_id:
                return result

    return None


def get_pairing_participants(pairing: Pairing) -> list:
    participants = []

    if pairing.driver_1 is not None:
        participants.append(pairing.driver_1)

    if pairing.driver_2 is not None:
        participants.append(pairing.driver_2)

    return participants


def get_participant_status(participant) -> str:
    if participant.withdrawn:
        if participant.withdrawn_reason:
            return f"Ausgeschieden – {participant.withdrawn_reason}"

        return "Ausgeschieden"

    return "Gewertet"


def sort_and_place_round_rows(rows: list[dict]) -> list[dict]:
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            row["withdrawn"],
            row["round_total_ms"] is None,
            row["round_total_ms"] if row["round_total_ms"] is not None else 999999999999,
            row["start_number"],
        ),
    )

    place = 1

    for row in sorted_rows:
        if row["withdrawn"]:
            row["place"] = ""
        elif row["round_total_ms"] is None:
            row["place"] = ""
        else:
            row["place"] = place
            place += 1

    return sorted_rows


def build_class_export_rows(
    pairings: list[Pairing],
) -> dict[str, list[dict]]:
    rows_by_round = {
        "quarterfinal": [],
        "semifinal": [],
        "final": [],
    }

    sorted_pairings = sorted(
        pairings,
        key=lambda pairing: (
            ROUND_ORDER.get(normalize_round_key(pairing.round_name), 99),
            pairing.pairing_number,
        ),
    )

    for pairing in sorted_pairings:
        round_key = normalize_round_key(pairing.round_name)

        if round_key not in rows_by_round:
            rows_by_round[round_key] = []

        round_number = ROUND_NUMBERS.get(round_key, 0)

        for participant in get_pairing_participants(pairing):
            result_1 = get_result_for_participant(
                pairing=pairing,
                run_number=1,
                participant_id=participant.id,
            )

            result_2 = get_result_for_participant(
                pairing=pairing,
                run_number=2,
                participant_id=participant.id,
            )

            withdrawn = participant.withdrawn is True

            result_1_ms = get_valued_time_ms(result_1)
            result_2_ms = get_valued_time_ms(result_2)

            if withdrawn:
                round_total_ms = None
            elif result_1_ms is not None and result_2_ms is not None:
                round_total_ms = result_1_ms + result_2_ms
            else:
                round_total_ms = None

            if withdrawn:
                round_total_display = ""
                run_1_time = ""
                run_1_result = ""
                run_2_time = ""
                run_2_result = ""
            else:
                round_total_display = format_time_ms(round_total_ms)
                run_1_time = format_time_ms(result_1.raw_time_ms if result_1 else None)
                run_1_result = format_time_ms(result_1_ms)
                run_2_time = format_time_ms(result_2.raw_time_ms if result_2 else None)
                run_2_result = format_time_ms(result_2_ms)

            rows_by_round[round_key].append(
                {
                    "place": "",
                    "round_key": round_key,
                    "round_label": ROUND_LABELS.get(round_key, pairing.round_name),
                    "pairing_number": pairing.pairing_number,
                    "start_number": participant.start_number,
                    "driver_name": f"{participant.first_name} {participant.last_name}",
                    "club": participant.club or "",
                    "adac_number": participant.adac_number or "",
                    "youth_license_number": participant.youth_license_number or "",
                    "status": get_participant_status(participant),
                    "withdrawn": withdrawn,
                    "run_1_display": f"{round_number}.1",
                    "run_1_time": run_1_time,
                    "run_1_penalty": get_penalty_sum(result_1) if not withdrawn else "",
                    "run_1_result": run_1_result,
                    "run_1_result_ms": result_1_ms,
                    "run_2_display": f"{round_number}.2",
                    "run_2_time": run_2_time,
                    "run_2_penalty": get_penalty_sum(result_2) if not withdrawn else "",
                    "run_2_result": run_2_result,
                    "run_2_result_ms": result_2_ms,
                    "round_total_ms": round_total_ms,
                    "round_total": round_total_display,
                    "round_penalty": (
                        get_penalty_sum(result_1) + get_penalty_sum(result_2)
                        if not withdrawn
                        else ""
                    ),
                }
            )

    for round_key in rows_by_round:
        rows_by_round[round_key] = sort_and_place_round_rows(
            rows_by_round[round_key]
        )

    return rows_by_round


def build_overall_rows(rows_by_round: dict[str, list[dict]]) -> list[dict]:
    participants = {}

    for round_key in ["quarterfinal", "semifinal", "final"]:
        for row in rows_by_round.get(round_key, []):
            start_number = row["start_number"]

            if start_number not in participants:
                participants[start_number] = {
                    "place": "",
                    "start_number": row["start_number"],
                    "driver_name": row["driver_name"],
                    "club": row["club"],
                    "adac_number": row["adac_number"],
                    "youth_license_number": row["youth_license_number"],
                    "status": row["status"],
                    "withdrawn": row["withdrawn"],

                    "quarterfinal_ms": None,
                    "quarterfinal_display": "",
                    "quarterfinal_penalty": "",

                    "semifinal_ms": None,
                    "semifinal_display": "",
                    "semifinal_penalty": "",

                    "final_ms": None,
                    "final_display": "",
                    "final_penalty": "",

                    "total_ms": None,
                    "total_display": "",
                    "total_penalty": "",
                }

            if row["withdrawn"]:
                participants[start_number]["withdrawn"] = True
                participants[start_number]["status"] = row["status"]

            if round_key == "quarterfinal":
                participants[start_number]["quarterfinal_ms"] = row["round_total_ms"]
                participants[start_number]["quarterfinal_display"] = row["round_total"]
                participants[start_number]["quarterfinal_penalty"] = row["round_penalty"]

            if round_key == "semifinal":
                participants[start_number]["semifinal_ms"] = row["round_total_ms"]
                participants[start_number]["semifinal_display"] = row["round_total"]
                participants[start_number]["semifinal_penalty"] = row["round_penalty"]

            if round_key == "final":
                participants[start_number]["final_ms"] = row["round_total_ms"]
                participants[start_number]["final_display"] = row["round_total"]
                participants[start_number]["final_penalty"] = row["round_penalty"]

    overall_rows = []

    for item in participants.values():
        if item["withdrawn"]:
            item["total_ms"] = None
            item["total_display"] = ""
            item["total_penalty"] = ""
            overall_rows.append(item)
            continue

        total_ms = 0
        has_total = False

        for key in ["quarterfinal_ms", "semifinal_ms", "final_ms"]:
            if item[key] is not None:
                total_ms += item[key]
                has_total = True

        item["total_ms"] = total_ms if has_total else None
        item["total_display"] = format_time_ms(item["total_ms"])

        total_penalty = 0

        for key in ["quarterfinal_penalty", "semifinal_penalty", "final_penalty"]:
            if isinstance(item[key], int):
                total_penalty += item[key]

        item["total_penalty"] = total_penalty

        overall_rows.append(item)

    overall_rows = sorted(
        overall_rows,
        key=lambda row: (
            row["withdrawn"],
            row["total_ms"] is None,
            row["total_ms"] if row["total_ms"] is not None else 999999999999,
            row["start_number"],
        ),
    )

    place = 1

    for row in overall_rows:
        if row["withdrawn"]:
            row["place"] = ""
        elif row["total_ms"] is None:
            row["place"] = ""
        else:
            row["place"] = place
            place += 1

    return overall_rows


def write_event_header(
    worksheet,
    title: str,
    event: Event,
    export_timestamp: str,
    title_format,
    meta_format,
):
    worksheet.write(0, 0, title, title_format)
    worksheet.write(1, 0, "Veranstaltung", meta_format)
    worksheet.write(1, 1, f"{event.event_number} – {event.name}")
    worksheet.write(2, 0, "Ortsclub / Veranstalter", meta_format)
    worksheet.write(2, 1, event.organizer_club or "")
    worksheet.write(3, 0, "Datum", meta_format)
    worksheet.write(3, 1, event.event_date or "")
    worksheet.write(4, 0, "Ort", meta_format)
    worksheet.write(4, 1, event.location or "")
    worksheet.write(5, 0, "Exportiert am", meta_format)
    worksheet.write(5, 1, export_timestamp)


def write_round_sheet(
    workbook,
    sheet_name: str,
    event: Event,
    race_class: int,
    rows: list[dict],
    export_timestamp: str,
):
    worksheet = workbook.add_worksheet(sheet_name)

    title_format = workbook.add_format({"bold": True, "font_size": 16})
    meta_format = workbook.add_format({"bold": True, "font_size": 11})
    header_format = workbook.add_format({"bold": True, "bg_color": "#E5E7EB", "border": 1})
    cell_format = workbook.add_format({"border": 1})
    time_format = workbook.add_format({"border": 1, "align": "right"})
    number_format = workbook.add_format({"border": 1, "align": "right"})
    place_format = workbook.add_format({"border": 1, "bold": True, "align": "center"})

    write_event_header(
        worksheet=worksheet,
        title=f"{sheet_name} – Klasse {race_class}",
        event=event,
        export_timestamp=export_timestamp,
        title_format=title_format,
        meta_format=meta_format,
    )

    headers = [
        "Platz",
        "Runde",
        "Paarung",
        "Startnr.",
        "Fahrer",
        "Verein",
        "ADAC Nummer",
        "Jugendausweisnummer",
        "Status",
        "Lauf 1",
        "Zeit 1",
        "Fehlerpunkte 1",
        "Ergebnis 1",
        "Lauf 2",
        "Zeit 2",
        "Fehlerpunkte 2",
        "Ergebnis 2",
        "Gesamt Runde",
    ]

    header_row = 7

    for column, header in enumerate(headers):
        worksheet.write(header_row, column, header, header_format)

    for row_index, row_data in enumerate(rows, start=header_row + 1):
        worksheet.write(row_index, 0, row_data["place"], place_format)
        worksheet.write(row_index, 1, row_data["round_label"], cell_format)
        worksheet.write(row_index, 2, row_data["pairing_number"], number_format)
        worksheet.write(row_index, 3, row_data["start_number"], number_format)
        worksheet.write(row_index, 4, row_data["driver_name"], cell_format)
        worksheet.write(row_index, 5, row_data["club"], cell_format)
        worksheet.write(row_index, 6, row_data["adac_number"], cell_format)
        worksheet.write(row_index, 7, row_data["youth_license_number"], cell_format)
        worksheet.write(row_index, 8, row_data["status"], cell_format)
        worksheet.write(row_index, 9, row_data["run_1_display"], cell_format)
        worksheet.write(row_index, 10, row_data["run_1_time"], time_format)
        worksheet.write(row_index, 11, row_data["run_1_penalty"], number_format)
        worksheet.write(row_index, 12, row_data["run_1_result"], time_format)
        worksheet.write(row_index, 13, row_data["run_2_display"], cell_format)
        worksheet.write(row_index, 14, row_data["run_2_time"], time_format)
        worksheet.write(row_index, 15, row_data["run_2_penalty"], number_format)
        worksheet.write(row_index, 16, row_data["run_2_result"], time_format)
        worksheet.write(row_index, 17, row_data["round_total"], time_format)

    worksheet.set_column(0, 0, 8)
    worksheet.set_column(1, 1, 14)
    worksheet.set_column(2, 2, 10)
    worksheet.set_column(3, 3, 10)
    worksheet.set_column(4, 4, 24)
    worksheet.set_column(5, 5, 32)
    worksheet.set_column(6, 6, 18)
    worksheet.set_column(7, 7, 22)
    worksheet.set_column(8, 8, 24)
    worksheet.set_column(9, 9, 8)
    worksheet.set_column(10, 10, 12)
    worksheet.set_column(11, 11, 16)
    worksheet.set_column(12, 12, 12)
    worksheet.set_column(13, 13, 8)
    worksheet.set_column(14, 14, 12)
    worksheet.set_column(15, 15, 16)
    worksheet.set_column(16, 16, 12)
    worksheet.set_column(17, 17, 14)

    worksheet.freeze_panes(header_row + 1, 0)
    worksheet.autofilter(
        header_row,
        0,
        max(header_row + len(rows), header_row),
        len(headers) - 1,
    )


def write_overall_sheet(
    workbook,
    event: Event,
    race_class: int,
    rows: list[dict],
    export_timestamp: str,
    sheet_name: str = "Gesamtwertung",
    title: str | None = None,
):
    worksheet = workbook.add_worksheet(sheet_name)

    title_format = workbook.add_format({"bold": True, "font_size": 16})
    meta_format = workbook.add_format({"bold": True, "font_size": 11})
    header_format = workbook.add_format({"bold": True, "bg_color": "#E5E7EB", "border": 1})
    cell_format = workbook.add_format({"border": 1})
    time_format = workbook.add_format({"border": 1, "align": "right"})
    number_format = workbook.add_format({"border": 1, "align": "right"})
    place_format = workbook.add_format({"border": 1, "bold": True, "align": "center"})

    write_event_header(
        worksheet=worksheet,
        title=title or f"Gesamtwertung – Klasse {race_class}",
        event=event,
        export_timestamp=export_timestamp,
        title_format=title_format,
        meta_format=meta_format,
    )

    headers = [
        "Platz",
        "Startnr.",
        "Fahrer",
        "Verein",
        "ADAC Nummer",
        "Jugendausweisnummer",
        "Status",
        "Viertelfinale",
        "FP Viertelfinale",
        "Halbfinale",
        "FP Halbfinale",
        "Finale",
        "FP Finale",
        "Gesamt FP",
        "Gesamtzeit",
    ]

    header_row = 7

    for column, header in enumerate(headers):
        worksheet.write(header_row, column, header, header_format)

    for row_index, row_data in enumerate(rows, start=header_row + 1):
        worksheet.write(row_index, 0, row_data["place"], place_format)
        worksheet.write(row_index, 1, row_data["start_number"], number_format)
        worksheet.write(row_index, 2, row_data["driver_name"], cell_format)
        worksheet.write(row_index, 3, row_data["club"], cell_format)
        worksheet.write(row_index, 4, row_data["adac_number"], cell_format)
        worksheet.write(row_index, 5, row_data["youth_license_number"], cell_format)
        worksheet.write(row_index, 6, row_data["status"], cell_format)
        worksheet.write(row_index, 7, row_data["quarterfinal_display"], time_format)
        worksheet.write(row_index, 8, row_data["quarterfinal_penalty"], number_format)
        worksheet.write(row_index, 9, row_data["semifinal_display"], time_format)
        worksheet.write(row_index, 10, row_data["semifinal_penalty"], number_format)
        worksheet.write(row_index, 11, row_data["final_display"], time_format)
        worksheet.write(row_index, 12, row_data["final_penalty"], number_format)
        worksheet.write(row_index, 13, row_data["total_penalty"], number_format)
        worksheet.write(row_index, 14, row_data["total_display"], time_format)

    worksheet.set_column(0, 0, 8)
    worksheet.set_column(1, 1, 10)
    worksheet.set_column(2, 2, 24)
    worksheet.set_column(3, 3, 32)
    worksheet.set_column(4, 4, 18)
    worksheet.set_column(5, 5, 22)
    worksheet.set_column(6, 6, 24)
    worksheet.set_column(7, 7, 14)
    worksheet.set_column(8, 8, 18)
    worksheet.set_column(9, 9, 14)
    worksheet.set_column(10, 10, 16)
    worksheet.set_column(11, 11, 14)
    worksheet.set_column(12, 12, 10)
    worksheet.set_column(13, 13, 12)
    worksheet.set_column(14, 14, 14)

    worksheet.freeze_panes(header_row + 1, 0)
    worksheet.autofilter(
        header_row,
        0,
        max(header_row + len(rows), header_row),
        len(headers) - 1,
    )


def write_all_classes_overall_sheet(
    workbook,
    event: Event,
    rows: list[dict],
    export_timestamp: str,
):
    worksheet = workbook.add_worksheet("Alle Klassen")

    title_format = workbook.add_format({"bold": True, "font_size": 16})
    meta_format = workbook.add_format({"bold": True, "font_size": 11})
    header_format = workbook.add_format({"bold": True, "bg_color": "#E5E7EB", "border": 1})
    cell_format = workbook.add_format({"border": 1})
    time_format = workbook.add_format({"border": 1, "align": "right"})
    number_format = workbook.add_format({"border": 1, "align": "right"})
    place_format = workbook.add_format({"border": 1, "bold": True, "align": "center"})

    write_event_header(
        worksheet=worksheet,
        title="Gesamtwertung – alle Klassen",
        event=event,
        export_timestamp=export_timestamp,
        title_format=title_format,
        meta_format=meta_format,
    )

    headers = [
        "Klasse",
        "Platz",
        "Startnr.",
        "Fahrer",
        "Verein",
        "ADAC Nummer",
        "Jugendausweisnummer",
        "Status",
        "Viertelfinale",
        "FP Viertelfinale",
        "Halbfinale",
        "FP Halbfinale",
        "Finale",
        "FP Finale",
        "Gesamt FP",
        "Gesamtzeit",
    ]

    header_row = 7

    for column, header in enumerate(headers):
        worksheet.write(header_row, column, header, header_format)

    for row_index, row_data in enumerate(rows, start=header_row + 1):
        worksheet.write(row_index, 0, row_data["race_class"], number_format)
        worksheet.write(row_index, 1, row_data["place"], place_format)
        worksheet.write(row_index, 2, row_data["start_number"], number_format)
        worksheet.write(row_index, 3, row_data["driver_name"], cell_format)
        worksheet.write(row_index, 4, row_data["club"], cell_format)
        worksheet.write(row_index, 5, row_data["adac_number"], cell_format)
        worksheet.write(row_index, 6, row_data["youth_license_number"], cell_format)
        worksheet.write(row_index, 7, row_data["status"], cell_format)
        worksheet.write(row_index, 8, row_data["quarterfinal_display"], time_format)
        worksheet.write(row_index, 9, row_data["quarterfinal_penalty"], number_format)
        worksheet.write(row_index, 10, row_data["semifinal_display"], time_format)
        worksheet.write(row_index, 11, row_data["semifinal_penalty"], number_format)
        worksheet.write(row_index, 12, row_data["final_display"], time_format)
        worksheet.write(row_index, 13, row_data["final_penalty"], number_format)
        worksheet.write(row_index, 14, row_data["total_penalty"], number_format)
        worksheet.write(row_index, 15, row_data["total_display"], time_format)

    worksheet.set_column(0, 0, 8)
    worksheet.set_column(1, 1, 8)
    worksheet.set_column(2, 2, 10)
    worksheet.set_column(3, 3, 24)
    worksheet.set_column(4, 4, 32)
    worksheet.set_column(5, 5, 18)
    worksheet.set_column(6, 6, 22)
    worksheet.set_column(7, 7, 24)
    worksheet.set_column(8, 8, 14)
    worksheet.set_column(9, 9, 18)
    worksheet.set_column(10, 10, 14)
    worksheet.set_column(11, 11, 16)
    worksheet.set_column(12, 12, 14)
    worksheet.set_column(13, 13, 10)
    worksheet.set_column(14, 14, 12)
    worksheet.set_column(15, 15, 14)

    worksheet.freeze_panes(header_row + 1, 0)
    worksheet.autofilter(
        header_row,
        0,
        max(header_row + len(rows), header_row),
        len(headers) - 1,
    )


def write_team_standings_sheet(
    workbook,
    event: Event,
    rows: list[dict],
    export_timestamp: str,
):
    worksheet = workbook.add_worksheet("Mannschaftswertung")

    title_format = workbook.add_format({"bold": True, "font_size": 16})
    meta_format = workbook.add_format({"bold": True, "font_size": 11})
    header_format = workbook.add_format({"bold": True, "bg_color": "#E5E7EB", "border": 1})
    cell_format = workbook.add_format({"border": 1})
    number_format = workbook.add_format({"border": 1, "align": "right"})
    place_format = workbook.add_format({"border": 1, "bold": True, "align": "center"})

    write_event_header(
        worksheet=worksheet,
        title="Mannschaftswertung",
        event=event,
        export_timestamp=export_timestamp,
        title_format=title_format,
        meta_format=meta_format,
    )

    headers = [
        "Platz",
        "Team",
        "Punkte",
        "Gewertete Fahrer",
        "Streichergebnis",
    ]

    header_row = 7

    for column, header in enumerate(headers):
        worksheet.write(header_row, column, header, header_format)

    for row_index, row_data in enumerate(rows, start=header_row + 1):
        counted_members = []
        dropped_members = []

        for member in row_data["counted_members"]:
            counted_members.append(
                f"#{member['start_number']} {member['driver_name']} "
                f"(Kl. {member['race_class']}, Platz {member['place'] or '-'}, "
                f"{member['points']} Punkte)"
            )

        for member in row_data["dropped_members"]:
            dropped_members.append(
                f"#{member['start_number']} {member['driver_name']} "
                f"(Kl. {member['race_class']}, Platz {member['place'] or '-'}, "
                f"{member['points']} Punkte)"
            )

        worksheet.write(row_index, 0, row_data["place"], place_format)
        worksheet.write(row_index, 1, row_data["team"].name, cell_format)
        worksheet.write(row_index, 2, row_data["points"], number_format)
        worksheet.write(row_index, 3, "\n".join(counted_members), cell_format)
        worksheet.write(row_index, 4, "\n".join(dropped_members), cell_format)

    worksheet.set_column(0, 0, 8)
    worksheet.set_column(1, 1, 28)
    worksheet.set_column(2, 2, 12)
    worksheet.set_column(3, 3, 56)
    worksheet.set_column(4, 4, 56)

    worksheet.freeze_panes(header_row + 1, 0)
    worksheet.autofilter(
        header_row,
        0,
        max(header_row + len(rows), header_row),
        len(headers) - 1,
    )


def build_class_xlsx(
    event: Event,
    race_class: int,
    pairings: list[Pairing],
) -> bytes:
    output = BytesIO()
    export_timestamp = now_export_string()

    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    rows_by_round = build_class_export_rows(pairings)
    overall_rows = build_overall_rows(rows_by_round)

    write_overall_sheet(
        workbook=workbook,
        event=event,
        race_class=race_class,
        rows=overall_rows,
        export_timestamp=export_timestamp,
    )

    write_round_sheet(
        workbook=workbook,
        sheet_name="Viertelfinale",
        event=event,
        race_class=race_class,
        rows=rows_by_round.get("quarterfinal", []),
        export_timestamp=export_timestamp,
    )

    write_round_sheet(
        workbook=workbook,
        sheet_name="Halbfinale",
        event=event,
        race_class=race_class,
        rows=rows_by_round.get("semifinal", []),
        export_timestamp=export_timestamp,
    )

    write_round_sheet(
        workbook=workbook,
        sheet_name="Finale",
        event=event,
        race_class=race_class,
        rows=rows_by_round.get("final", []),
        export_timestamp=export_timestamp,
    )

    workbook.close()

    output.seek(0)
    return output.getvalue()


def build_overall_xlsx(
    event: Event,
    race_class: int,
    pairings: list[Pairing],
) -> bytes:
    output = BytesIO()
    export_timestamp = now_export_string()

    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    rows_by_round = build_class_export_rows(pairings)
    overall_rows = build_overall_rows(rows_by_round)

    write_overall_sheet(
        workbook=workbook,
        event=event,
        race_class=race_class,
        rows=overall_rows,
        export_timestamp=export_timestamp,
    )

    workbook.close()

    output.seek(0)
    return output.getvalue()


def build_all_overall_xlsx(
    event: Event,
    pairings_by_class: dict[int, list[Pairing]],
    team_standings: list[dict] | None = None,
) -> bytes:
    output = BytesIO()
    export_timestamp = now_export_string()

    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    all_rows = []
    overall_rows_by_class = {}

    for race_class in [1, 2, 3, 4, 5]:
        rows_by_round = build_class_export_rows(
            pairings_by_class.get(race_class, [])
        )
        overall_rows = build_overall_rows(rows_by_round)
        overall_rows_by_class[race_class] = overall_rows

        for row in overall_rows:
            all_rows.append(
                {
                    **row,
                    "race_class": race_class,
                }
            )

    write_all_classes_overall_sheet(
        workbook=workbook,
        event=event,
        rows=all_rows,
        export_timestamp=export_timestamp,
    )

    for race_class in [1, 2, 3, 4, 5]:
        write_overall_sheet(
            workbook=workbook,
            event=event,
            race_class=race_class,
            rows=overall_rows_by_class.get(race_class, []),
            export_timestamp=export_timestamp,
            sheet_name=f"Klasse {race_class}",
            title=f"Gesamtwertung – Klasse {race_class}",
        )

    write_team_standings_sheet(
        workbook=workbook,
        event=event,
        rows=team_standings or [],
        export_timestamp=export_timestamp,
    )

    workbook.close()

    output.seek(0)
    return output.getvalue()


def build_team_standings_xlsx(
    event: Event,
    team_standings: list[dict],
) -> bytes:
    output = BytesIO()
    export_timestamp = now_export_string()

    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    write_team_standings_sheet(
        workbook=workbook,
        event=event,
        rows=team_standings,
        export_timestamp=export_timestamp,
    )

    workbook.close()

    output.seek(0)
    return output.getvalue()


def build_round_xlsx(
    event: Event,
    race_class: int,
    pairings: list[Pairing],
    round_key: str,
) -> bytes:
    output = BytesIO()
    export_timestamp = now_export_string()

    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    rows_by_round = build_class_export_rows(pairings)

    sheet_names = {
        "quarterfinal": "Viertelfinale",
        "semifinal": "Halbfinale",
        "final": "Finale",
    }

    sheet_name = sheet_names.get(round_key, round_key)

    write_round_sheet(
        workbook=workbook,
        sheet_name=sheet_name,
        event=event,
        race_class=race_class,
        rows=rows_by_round.get(round_key, []),
        export_timestamp=export_timestamp,
    )

    workbook.close()

    output.seek(0)
    return output.getvalue()
