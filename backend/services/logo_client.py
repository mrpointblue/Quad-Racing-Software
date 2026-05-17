import time
from dataclasses import dataclass
from typing import Any


def _bool_setting(settings: dict[str, str], key: str) -> bool:
    return str(settings.get(key, "")).strip().lower() == "true"


def _int_setting(settings: dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(settings.get(key, "")).strip())
    except (TypeError, ValueError):
        return default


def _float_setting(settings: dict[str, str], key: str, default: float) -> float:
    try:
        return float(str(settings.get(key, "")).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def _optional_int_setting(settings: dict[str, str], key: str) -> int | None:
    raw_value = str(settings.get(key, "")).strip()

    if raw_value == "":
        return None

    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _format_centiseconds_time(time_ms: int | None) -> str | None:
    if time_ms is None:
        return None

    total_centiseconds = int(time_ms) // 10
    minutes = total_centiseconds // 6000
    seconds = (total_centiseconds % 6000) // 100
    hundredths = total_centiseconds % 100

    return f"{minutes:02d}:{seconds:02d},{hundredths:02d}"


def _empty_status(enabled: bool, message: str, ok: bool = False) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "ok": ok,
        "online": False,
        "message": message,
        "times": {
            "lane_a_raw": None,
            "lane_b_raw": None,
            "lane_a_centiseconds": None,
            "lane_b_centiseconds": None,
            "lane_a_ms": None,
            "lane_b_ms": None,
            "lane_a_seconds": None,
            "lane_b_seconds": None,
            "lane_a_display": None,
            "lane_b_display": None,
        },
        "outputs": {
            "yellow": False,
            "green": False,
            "red": False,
        },
        "markers": {
            "lane_a_running": False,
            "lane_b_running": False,
            "reset_available": False,
            "false_start_a": False,
            "false_start_b": False,
            "light_barrier_fault": False,
        },
    }


@dataclass
class LogoConfig:
    enabled: bool
    host: str
    port: int
    timeout: float
    time_register_start: int
    output_coil_start: int
    marker_coil_start: int
    web_start_coil: int | None
    web_reset_coil: int | None
    pulse_duration: float


class LogoClient:
    def __init__(self, settings: dict[str, str]):
        self.settings = settings
        self.config = self._build_config(settings)

    @staticmethod
    def _build_config(settings: dict[str, str]) -> LogoConfig:
        timeout_ms = _int_setting(settings, "logo_timeout_ms", 1000)
        pulse_ms = _int_setting(settings, "logo_pulse_duration_ms", 200)

        return LogoConfig(
            enabled=_bool_setting(settings, "logo_enabled"),
            host=str(settings.get("logo_ip", "192.168.2.20")).strip(),
            port=_int_setting(settings, "logo_port", 502),
            timeout=max(0.1, timeout_ms / 1000),
            time_register_start=_int_setting(settings, "logo_time_register_start", 0),
            output_coil_start=_int_setting(settings, "logo_output_coil_start", 8192),
            marker_coil_start=_int_setting(settings, "logo_marker_coil_start", 8256),
            web_start_coil=_optional_int_setting(settings, "logo_web_start_coil") or 8262,
            web_reset_coil=_optional_int_setting(settings, "logo_web_reset_coil") or 8264,
            pulse_duration=max(0.05, _float_setting(settings, "logo_pulse_duration", pulse_ms / 1000)),
        )

    def _disabled_result(self) -> dict[str, Any]:
        return _empty_status(False, "LOGO ist deaktiviert.", ok=True)

    def _get_modbus_client_class(self):
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError as error:
            raise RuntimeError("pymodbus ist nicht installiert.") from error

        return ModbusTcpClient

    def _connect(self):
        client_class = self._get_modbus_client_class()
        client = client_class(
            host=self.config.host,
            port=self.config.port,
            timeout=self.config.timeout,
        )

        if not client.connect():
            try:
                client.close()
            except Exception:
                pass
            raise ConnectionError("Modbus-TCP-Verbindung fehlgeschlagen.")

        return client

    @staticmethod
    def _bits(response, count: int) -> list[bool]:
        if response is None or getattr(response, "isError", lambda: True)():
            raise RuntimeError("Modbus-Leseantwort ist fehlerhaft.")

        bits = list(getattr(response, "bits", []) or [])
        return [bool(bits[index]) if index < len(bits) else False for index in range(count)]

    @staticmethod
    def _registers(response, count: int) -> list[int]:
        if response is None or getattr(response, "isError", lambda: True)():
            raise RuntimeError("Modbus-Leseantwort ist fehlerhaft.")

        registers = list(getattr(response, "registers", []) or [])

        if len(registers) < count:
            raise RuntimeError("Modbus-Leseantwort enthält zu wenige Register.")

        return [int(registers[index]) & 0xFFFF for index in range(count)]

    def test_connection(self) -> dict[str, Any]:
        if not self.config.enabled:
            return self._disabled_result()

        client = None
        started_at = time.monotonic()

        try:
            client = self._connect()
            elapsed_ms = round((time.monotonic() - started_at) * 1000)
            return {
                "enabled": True,
                "ok": True,
                "online": True,
                "message": "Modbus-TCP-Verbindung erfolgreich.",
                "response_time_ms": elapsed_ms,
            }
        except Exception as error:
            elapsed_ms = round((time.monotonic() - started_at) * 1000)
            return {
                "enabled": True,
                "ok": False,
                "online": False,
                "message": str(error),
                "response_time_ms": elapsed_ms,
            }
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def read_times(self, client=None) -> dict[str, Any]:
        if not self.config.enabled:
            return self._disabled_result()["times"]

        close_client = False

        try:
            if client is None:
                client = self._connect()
                close_client = True

            response = client.read_holding_registers(
                address=self.config.time_register_start,
                count=4,
            )
            registers = self._registers(response, 4)
            lane_a_raw = (registers[0] << 16) | registers[1]
            lane_b_raw = (registers[2] << 16) | registers[3]

            # Die LOGO liefert die Zeitwerte als Hundertstelsekunden.
            # Intern speichert die Anwendung Zeiten weiterhin in Millisekunden.
            lane_a_ms = lane_a_raw * 10
            lane_b_ms = lane_b_raw * 10

            return {
                "lane_a_raw": lane_a_raw,
                "lane_b_raw": lane_b_raw,
                "lane_a_centiseconds": lane_a_raw,
                "lane_b_centiseconds": lane_b_raw,
                "lane_a_ms": lane_a_ms,
                "lane_b_ms": lane_b_ms,
                "lane_a_seconds": lane_a_raw / 100,
                "lane_b_seconds": lane_b_raw / 100,
                "lane_a_display": _format_centiseconds_time(lane_a_ms),
                "lane_b_display": _format_centiseconds_time(lane_b_ms),
            }
        finally:
            if close_client and client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def read_outputs(self, client=None) -> dict[str, bool]:
        if not self.config.enabled:
            return self._disabled_result()["outputs"]

        close_client = False

        try:
            if client is None:
                client = self._connect()
                close_client = True

            response = client.read_coils(
                address=self.config.output_coil_start,
                count=3,
            )
            bits = self._bits(response, 3)

            return {
                "yellow": bits[0],
                "green": bits[1],
                "red": bits[2],
            }
        finally:
            if close_client and client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def read_markers(self, client=None) -> dict[str, bool]:
        if not self.config.enabled:
            return self._disabled_result()["markers"]

        close_client = False

        try:
            if client is None:
                client = self._connect()
                close_client = True

            response = client.read_coils(
                address=self.config.marker_coil_start,
                count=6,
            )
            bits = self._bits(response, 6)

            return {
                "lane_a_running": bits[0],
                "lane_b_running": bits[1],
                "reset_available": bits[2],
                "false_start_a": bits[3],
                "false_start_b": bits[4],
                "light_barrier_fault": bits[5],
            }
        finally:
            if close_client and client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def read_status(self) -> dict[str, Any]:
        if not self.config.enabled:
            return self._disabled_result()

        client = None

        try:
            client = self._connect()
            times = self.read_times(client)
            outputs = self.read_outputs(client)
            markers = self.read_markers(client)

            return {
                "enabled": True,
                "ok": True,
                "online": True,
                "message": "LOGO-Status gelesen.",
                "times": times,
                "outputs": outputs,
                "markers": markers,
            }
        except Exception as error:
            status = _empty_status(True, f"LOGO-Status konnte nicht gelesen werden: {error}")
            status["ok"] = False
            return status
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def _pulse_coil(self, coil_address: int | None, label: str) -> dict[str, Any]:
        if not self.config.enabled:
            return self._disabled_result()

        if coil_address is None:
            return {
                "enabled": True,
                "ok": False,
                "online": False,
                "message": f"{label}-Schreibadresse ist nicht konfiguriert.",
            }

        client = None

        try:
            client = self._connect()
            client.write_coil(coil_address, True)
            time.sleep(self.config.pulse_duration)
            client.write_coil(coil_address, False)

            return {
                "enabled": True,
                "ok": True,
                "online": True,
                "message": f"{label}-Impuls gesendet.",
                "coil": coil_address,
            }
        except Exception as error:
            return {
                "enabled": True,
                "ok": False,
                "online": False,
                "message": f"{label}-Impuls fehlgeschlagen: {error}",
                "coil": coil_address,
            }
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def pulse_web_start(self) -> dict[str, Any]:
        return self._pulse_coil(self.config.web_start_coil, "WEB Start")

    def pulse_web_reset(self) -> dict[str, Any]:
        return self._pulse_coil(self.config.web_reset_coil, "WEB Reset")


def build_logo_client(settings: dict[str, str]) -> LogoClient:
    return LogoClient(settings)
