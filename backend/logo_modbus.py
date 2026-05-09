import socket
import time


def build_result(ok: bool, message: str, details: dict | None = None) -> dict:
    return {
        "ok": ok,
        "message": message,
        "details": details or {},
    }


def test_tcp_connection(ip: str, port: int, timeout_seconds: float) -> dict:
    started_at = time.monotonic()

    try:
        with socket.create_connection(
            (ip, port),
            timeout=timeout_seconds,
        ):
            elapsed_ms = round((time.monotonic() - started_at) * 1000)

            return build_result(
                ok=True,
                message="TCP-Verbindung erfolgreich.",
                details={
                    "ip": ip,
                    "port": port,
                    "timeout_seconds": timeout_seconds,
                    "response_time_ms": elapsed_ms,
                },
            )
    except Exception as error:
        elapsed_ms = round((time.monotonic() - started_at) * 1000)

        return build_result(
            ok=False,
            message=f"TCP-Verbindung fehlgeschlagen: {error}",
            details={
                "ip": ip,
                "port": port,
                "timeout_seconds": timeout_seconds,
                "response_time_ms": elapsed_ms,
            },
        )


def test_modbus_connection(ip: str, port: int, timeout_seconds: float) -> dict:
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        return build_result(
            ok=False,
            message="pymodbus ist nicht installiert.",
            details={},
        )

    client = None
    started_at = time.monotonic()

    try:
        client = ModbusTcpClient(
            host=ip,
            port=port,
            timeout=timeout_seconds,
        )
        connected = client.connect()
        elapsed_ms = round((time.monotonic() - started_at) * 1000)

        if not connected:
            return build_result(
                ok=False,
                message="Modbus-TCP-Verbindung fehlgeschlagen.",
                details={
                    "ip": ip,
                    "port": port,
                    "timeout_seconds": timeout_seconds,
                    "response_time_ms": elapsed_ms,
                },
            )

        return build_result(
            ok=True,
            message="Modbus-TCP-Verbindung erfolgreich.",
            details={
                "ip": ip,
                "port": port,
                "timeout_seconds": timeout_seconds,
                "response_time_ms": elapsed_ms,
            },
        )
    except Exception as error:
        elapsed_ms = round((time.monotonic() - started_at) * 1000)

        return build_result(
            ok=False,
            message=f"Modbus-TCP-Verbindung fehlgeschlagen: {error}",
            details={
                "ip": ip,
                "port": port,
                "timeout_seconds": timeout_seconds,
                "response_time_ms": elapsed_ms,
            },
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def get_bool_setting(settings: dict, key: str) -> bool:
    return str(settings.get(key, "")).strip().lower() == "true"


def parse_connection_settings(settings: dict) -> dict:
    ip = str(settings.get("logo_ip", "")).strip()
    access_mode = str(settings.get("logo_access_mode", "")).strip()

    try:
        port = int(str(settings.get("logo_port", "")).strip())
    except (TypeError, ValueError):
        return build_result(
            ok=False,
            message="LOGO-Port ist ungültig.",
            details={"mode": "invalid_config"},
        )

    try:
        timeout_ms = int(str(settings.get("logo_timeout_ms", "")).strip())
    except (TypeError, ValueError):
        return build_result(
            ok=False,
            message="LOGO-Timeout ist ungültig.",
            details={"mode": "invalid_config"},
        )

    if not ip:
        return build_result(
            ok=False,
            message="LOGO-IP-Adresse fehlt.",
            details={"mode": "invalid_config"},
        )

    if port < 1 or port > 65535:
        return build_result(
            ok=False,
            message="LOGO-Port ist außerhalb des gültigen Bereichs.",
            details={"mode": "invalid_config", "port": port},
        )

    if timeout_ms < 100:
        return build_result(
            ok=False,
            message="LOGO-Timeout ist zu niedrig.",
            details={"mode": "invalid_config", "timeout_ms": timeout_ms},
        )

    return {
        "ok": True,
        "ip": ip,
        "port": port,
        "timeout_seconds": timeout_ms / 1000,
        "access_mode": access_mode,
    }


def read_logo_status(settings: dict) -> dict:
    try:
        if not get_bool_setting(settings, "logo_enabled"):
            return build_result(
                ok=False,
                message="LOGO ist deaktiviert.",
                details={"mode": "disabled"},
            )

        if get_bool_setting(settings, "logo_simulation_mode"):
            return build_result(
                ok=True,
                message="Simulationsmodus aktiv. Keine echte LOGO-Verbindung erforderlich.",
                details={"mode": "simulation"},
            )

        if str(settings.get("logo_access_mode", "")).strip() != "modbus_tcp":
            return build_result(
                ok=False,
                message="Modbus TCP ist nicht als Zugriffsart ausgewählt.",
                details={"mode": "not_modbus"},
            )

        parsed_settings = parse_connection_settings(settings)

        if not parsed_settings.get("ok"):
            return parsed_settings

        tcp_result = test_tcp_connection(
            ip=parsed_settings["ip"],
            port=parsed_settings["port"],
            timeout_seconds=parsed_settings["timeout_seconds"],
        )

        if not tcp_result["ok"]:
            return tcp_result

        return test_modbus_connection(
            ip=parsed_settings["ip"],
            port=parsed_settings["port"],
            timeout_seconds=parsed_settings["timeout_seconds"],
        )
    except Exception as error:
        return build_result(
            ok=False,
            message=f"LOGO-Statusprüfung fehlgeschlagen: {error}",
            details={},
        )
