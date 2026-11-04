from backend.database import DATA_DIR
from pathlib import Path
from backend.round_logic import ROUND_FINAL
from backend.round_logic import ROUND_PRELIMINARY
from backend.round_logic import ROUND_SEMIFINAL
from datetime import datetime


EVENT_YEAR = datetime.now().year

CLUB_LOGO_UPLOAD_DIR = Path("backend/static/uploads")

CLUB_LOGO_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}

CLUB_LOGO_MAX_SIZE_BYTES = 2 * 1024 * 1024

DATABASE_BACKUP_DIR = DATA_DIR / "backups"

DATABASE_IMPORT_DIR = DATA_DIR / "imports"

DATABASE_IMPORT_ALLOWED_EXTENSIONS = {".sqlite", ".db"}

DATABASE_IMPORT_MAX_SIZE_BYTES = 100 * 1024 * 1024

REQUIRED_DATABASE_TABLES = {
    "events",
    "participants",
    "pairings",
    "race_runs",
    "run_results",
    "system_settings",
}

REGISTRATION_GROUPS = [
    {
        "group_key": "class_1_2",
        "name": "Klasse 1 & 2",
        "classes": "1,2",
    },
    {
        "group_key": "class_3_4",
        "name": "Klasse 3 & 4",
        "classes": "3,4",
    },
    {
        "group_key": "class_5",
        "name": "Klasse 5",
        "classes": "5",
    },
]

ROUND_ORDER = {
    ROUND_PRELIMINARY: 1,
    "viertelfinale": 1,
    ROUND_SEMIFINAL: 2,
    ROUND_FINAL: 3,
}

CLASS_ROUND_CONFIGS = [
    {
        "key": "final",
        "round_names": [ROUND_FINAL],
        "label": "Finale",
        "number": 3,
    },
    {
        "key": "semifinal",
        "round_names": [ROUND_SEMIFINAL],
        "label": "Halbfinale",
        "number": 2,
    },
    {
        "key": "quarterfinal",
        "round_names": [ROUND_PRELIMINARY, "viertelfinale"],
        "label": "Viertelfinale",
        "number": 1,
    },
]

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

MONITOR_ROUND_OPTIONS = [
    {
        "key": ROUND_PRELIMINARY,
        "label": "Viertelfinale",
    },
    {
        "key": ROUND_SEMIFINAL,
        "label": "Halbfinale",
    },
    {
        "key": ROUND_FINAL,
        "label": "Finale",
    },
]

CHECKIN_QR_BASE_URL = "http://race.local:8010"

REGISTRATION_STATUS_LABELS = {
    "draft": "Entwurf",
    "submitted": "Eingereicht",
    "under_review": "In Prüfung",
    "approved": "Bestätigt",
    "rejected": "Abgelehnt",
    "correction_required": "Korrektur erforderlich",
    "cancelled": "Storniert",
}

REGISTRATION_STATUS_ORDER = {
    "under_review": 1,
    "submitted": 2,
    "correction_required": 3,
    "draft": 4,
    "rejected": 5,
    "cancelled": 6,
    "approved": 7,
}

LOGO_DEFAULT_SETTINGS = {
    "logo_enabled": "false",
    "logo_simulation_mode": "false",
    "logo_ip": "192.168.2.20",
    "logo_port": "502",
    "logo_timeout_ms": "1000",
    "logo_poll_interval_ms": "500",
    "logo_access_mode": "modbus_tcp",
    "logo_password": "",

    "logo_time_register_start": "0",
    "logo_output_coil_start": "8192",
    "logo_marker_coil_start": "8256",
    "logo_web_start_coil": "8262",
    "logo_web_reset_coil": "8264",
    "logo_pulse_duration": "0.2",
    "logo_pulse_duration_ms": "200",

    "logo_start_range": "132",
    "logo_start_address": "24",

    "logo_reset_range": "132",
    "logo_reset_address": "32",

    "logo_time_lane_1_range": "19",
    "logo_time_lane_1_address": "0",

    "logo_time_lane_2_range": "19",
    "logo_time_lane_2_address": "16",

    "logo_ready_range": "130",
    "logo_ready_address": "0",

    "logo_go_range": "130",
    "logo_go_address": "1",

    "logo_attention_range": "130",
    "logo_attention_address": "2",

    "logo_light_barrier_1_range": "131",
    "logo_light_barrier_1_address": "0",

    "logo_light_barrier_2_range": "131",
    "logo_light_barrier_2_address": "1",

    "logo_error_range": "131",
    "logo_error_address": "2",

    "logo_false_start_1_range": "131",
    "logo_false_start_1_address": "3",

    "logo_false_start_2_range": "131",
    "logo_false_start_2_address": "4",

    "logo_last_check_at": "",
    "logo_last_check_ok": "",
    "logo_last_error": "",
}

YOUTH_CARD_DEFAULT_SETTINGS = {
    "youth_card_scan_enabled": "false",
}

CLUB_LOGO_DEFAULT_SETTINGS = {
    "club_logo_path": "",
    "club_logo_original_filename": "",
    "club_logo_uploaded_at": "",
}

STARTDISPLAY_DEFAULT_SETTINGS = {
    "startdisplay_enabled": "false",
    "startdisplay_host": "",
    "startdisplay_port": "80",
    "startdisplay_endpoint": "/display",
    "startdisplay_last_send": "",
    "startdisplay_last_success": "",
    "startdisplay_last_error": "",
    "startdisplay_last_lane_a": "",
    "startdisplay_last_lane_b": "",
    "startdisplay_last_payload": "",
    "startdisplay_last_test_payload": "",
}
