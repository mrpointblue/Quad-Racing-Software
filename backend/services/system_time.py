import os
import subprocess
from datetime import datetime


def parse_system_time_input(value: str | None) -> datetime:
    cleaned_value = (value or "").strip()

    for date_format in [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]:
        try:
            return datetime.strptime(cleaned_value, date_format)
        except ValueError:
            continue

    raise ValueError("Ungültiges Zeitformat")


def format_system_command_error(
    command_label: str,
    result: subprocess.CompletedProcess,
) -> str:
    raw_message = (result.stderr or result.stdout or "").strip()
    normalized_message = raw_message.lower()

    if "password is required" in normalized_message or "a terminal is required" in normalized_message:
        return (
            f"{command_label} darf ohne Passwort nicht ausgeführt werden. "
            "Bitte die Installationsberechtigungen prüfen."
        )

    if "not in the sudoers" in normalized_message or "not allowed" in normalized_message:
        return (
            f"{command_label} ist für den Dienst nicht freigegeben. "
            "Bitte die Installationsberechtigungen prüfen."
        )

    if "no new privileges" in normalized_message:
        return f"{command_label} kann in dieser Laufzeitumgebung nicht mit sudo ausgeführt werden."

    if "operation not permitted" in normalized_message:
        return (
            f"{command_label} wurde vom Betriebssystem verweigert. "
            "Im Docker-Betrieb muss SYS_TIME freigegeben sein."
        )

    if raw_message:
        return f"{command_label} fehlgeschlagen: {raw_message}"

    return f"{command_label} fehlgeschlagen (Exit-Code {result.returncode})."


def run_system_time_command(command: list[str], command_label: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, f"{command_label} hat zu lange gedauert."
    except OSError as error:
        return False, f"{command_label} konnte nicht gestartet werden: {error}"

    if result.returncode != 0:
        return False, format_system_command_error(command_label, result)

    return True, ""


def _is_containerized() -> bool:
    return os.environ.get("CONTAINERIZED", "").strip().lower() == "true"


def set_system_time(target_time: datetime) -> tuple[bool, str]:
    if target_time.year < 2020 or target_time.year > 2099:
        return False, "Jahr außerhalb des erlaubten Bereichs."

    formatted_time = target_time.strftime("%Y-%m-%d %H:%M:%S")

    if _is_containerized():
        ok, message = run_system_time_command(
            ["date", "--set", formatted_time],
            "Systemzeit setzen (Docker)",
        )
        return (True, formatted_time) if ok else (False, message)

    run_system_time_command(
        ["sudo", "-n", "timedatectl", "set-ntp", "false"],
        "Automatische Zeitsynchronisation deaktivieren",
    )

    set_attempts = [
        (
            ["sudo", "-n", "timedatectl", "set-time", formatted_time],
            "Systemzeit setzen (timedatectl)",
        ),
        (
            ["sudo", "-n", "date", "--set", formatted_time],
            "Systemzeit setzen (date)",
        ),
    ]
    error_messages = []

    for command, command_label in set_attempts:
        ok, message = run_system_time_command(command, command_label)

        if ok:
            run_system_time_command(
                ["sudo", "-n", "hwclock", "--systohc"],
                "Hardware-Uhr speichern",
            )
            return True, formatted_time

        error_messages.append(message)

    return False, " / ".join(error_messages)
