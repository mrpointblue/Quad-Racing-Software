from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.services.logo_client import build_logo_client
import copy
from datetime import datetime
import threading
import time
from backend.constants import LOGO_DEFAULT_SETTINGS
from backend.application.settings_service import get_logo_settings


def parse_logo_poll_interval_ms(settings: dict | None) -> int:
    settings = settings or {}

    try:
        interval_ms = int(str(settings.get("logo_poll_interval_ms") or "200").strip())
    except (TypeError, ValueError):
        interval_ms = 200

    return 500

def build_empty_logo_status(enabled: bool, message: str, ok: bool = False) -> dict:
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
        "inputs": {
            "lane_a_light_barrier": False,
            "lane_b_light_barrier": False,
            "read_error": "",
        },
        "markers": {
            "lane_a_running": False,
            "lane_b_running": False,
            "reset_available": False,
            "false_start_a": False,
            "false_start_b": False,
            "light_barrier_fault": False,
            "light_barrier_fault_a": False,
            "light_barrier_fault_b": False,
        },
    }

class LogoStatusCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._client = None
        self._client_key = None
        self._settings: dict = dict(LOGO_DEFAULT_SETTINGS)
        self._poll_interval_ms = parse_logo_poll_interval_ms(self._settings)
        self._status = self._with_cache_metadata(
            build_empty_logo_status(False, "LOGO-Status wird geladen.", ok=True),
            source="cache-init",
        )

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="logo-status-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        with self._client_lock:
            self._drop_client_locked()

    def get_status(self) -> dict:
        with self._lock:
            status = copy.deepcopy(self._status)

        cached_at_monotonic = status.get("_cached_at_monotonic")
        if isinstance(cached_at_monotonic, (int, float)):
            status["cache_age_ms"] = round((time.monotonic() - cached_at_monotonic) * 1000)

        status.pop("_cached_at_monotonic", None)
        return status

    def _run(self):
        settings_refresh_after = 0.0

        while not self._stop_event.is_set():
            loop_started_at = time.monotonic()
            settings = self._settings

            if loop_started_at >= settings_refresh_after:
                settings = self._load_settings()
                settings_refresh_after = loop_started_at + 5.0

            poll_interval_ms = parse_logo_poll_interval_ms(settings)

            try:
                logo_client = build_logo_client(settings)

                if not logo_client.config.enabled:
                    with self._client_lock:
                        self._drop_client_locked()
                    status = build_empty_logo_status(False, "LOGO ist deaktiviert.", ok=True)
                else:
                    with self._client_lock:
                        client = self._get_or_connect_locked(logo_client, settings)
                        status = self._read_status_with_client(logo_client, client)
            except Exception as error:
                with self._client_lock:
                    self._drop_client_locked()

                status = self._error_status(settings, error)

            self._store_status(
                status=status,
                settings=settings,
                poll_interval_ms=poll_interval_ms,
            )
            self._stop_event.wait(poll_interval_ms / 1000)

    def pulse_web_start(self, settings: dict) -> dict:
        return self._pulse_web_coil(settings, "start", "WEB Start")

    def pulse_web_reset(self, settings: dict) -> dict:
        return self._pulse_web_coil(settings, "reset", "WEB Reset")

    def _pulse_web_coil(self, settings: dict, coil_name: str, label: str) -> dict:
        logo_client = build_logo_client(settings)

        if not logo_client.config.enabled:
            return build_empty_logo_status(False, "LOGO ist deaktiviert.", ok=True)

        coil_address = (
            logo_client.config.web_start_coil
            if coil_name == "start"
            else logo_client.config.web_reset_coil
        )

        if coil_address is None:
            return {
                "enabled": True,
                "ok": False,
                "online": False,
                "message": f"{label}-Schreibadresse ist nicht konfiguriert.",
            }

        try:
            with self._client_lock:
                client = self._get_or_connect_locked(logo_client, settings)
                self._write_coil_or_raise(client, coil_address, True)
                time.sleep(logo_client.config.pulse_duration)
                self._write_coil_or_raise(client, coil_address, False)

            return {
                "enabled": True,
                "ok": True,
                "online": True,
                "message": f"{label}-Impuls gesendet.",
                "coil": coil_address,
            }
        except Exception as error:
            with self._client_lock:
                self._drop_client_locked()

            return {
                "enabled": True,
                "ok": False,
                "online": False,
                "message": f"{label}-Impuls fehlgeschlagen: {error}",
                "coil": coil_address,
            }

    def _get_or_connect_locked(self, logo_client, settings: dict):
        next_client_key = self._connection_key(settings)

        if self._client is not None and self._client_key != next_client_key:
            self._drop_client_locked()

        if self._client is None:
            self._client = logo_client._connect()
            self._client_key = next_client_key

        return self._client

    @staticmethod
    def _write_coil_or_raise(client, coil_address: int, value: bool):
        response = client.write_coil(coil_address, value)

        if response is None or getattr(response, "isError", lambda: True)():
            raise RuntimeError("Modbus-Schreibantwort ist fehlerhaft.")

    def _drop_client_locked(self):
        if self._client is not None:
            self._close_client(self._client)
            self._client = None
            self._client_key = None

    @staticmethod
    def _connection_key(settings: dict) -> tuple:
        return (
            str(settings.get("logo_enabled", "")).strip().lower(),
            str(settings.get("logo_ip", "")).strip(),
            str(settings.get("logo_port", "")).strip(),
            str(settings.get("logo_timeout_ms", "")).strip(),
            str(settings.get("logo_time_register_start", "")).strip(),
            str(settings.get("logo_output_coil_start", "")).strip(),
            str(settings.get("logo_marker_coil_start", "")).strip(),
        )

    @staticmethod
    def _close_client(client):
        try:
            client.close()
        except Exception:
            pass

    @staticmethod
    def _read_status_with_client(logo_client, client) -> dict:
        inputs = logo_client.read_inputs_status(client)
        markers = logo_client.read_markers(client)
        markers["light_barrier_fault_a"] = (
            markers["light_barrier_fault"] and bool(inputs["lane_a_light_barrier"])
        )
        markers["light_barrier_fault_b"] = (
            markers["light_barrier_fault"] and bool(inputs["lane_b_light_barrier"])
        )

        return {
            "enabled": True,
            "ok": True,
            "online": True,
            "message": "LOGO-Status gelesen.",
            "times": logo_client.read_times(client),
            "outputs": logo_client.read_outputs(client),
            "inputs": inputs,
            "markers": markers,
        }

    @staticmethod
    def _error_status(settings: dict, error: Exception) -> dict:
        return build_empty_logo_status(
            enabled=str(settings.get("logo_enabled", "")).strip().lower() == "true",
            ok=False,
            message=f"LOGO-Status konnte nicht gelesen werden: {error}",
        )

    def _load_settings(self) -> dict:
        db = SessionLocal()
        try:
            return get_logo_settings(db)
        except Exception as error:
            self._store_status(
                status=build_empty_logo_status(
                    False,
                    f"LOGO-Einstellungen konnten nicht geladen werden: {error}",
                ),
                settings=dict(LOGO_DEFAULT_SETTINGS),
                poll_interval_ms=parse_logo_poll_interval_ms(LOGO_DEFAULT_SETTINGS),
            )
            return dict(LOGO_DEFAULT_SETTINGS)
        finally:
            db.close()

    def _store_status(self, status: dict, settings: dict, poll_interval_ms: int):
        with self._lock:
            self._settings = dict(settings)
            self._poll_interval_ms = poll_interval_ms
            self._status = self._with_cache_metadata(status, source="poller")

    def _with_cache_metadata(self, status: dict, source: str) -> dict:
        payload = copy.deepcopy(status)
        payload["cached"] = True
        payload["cache_source"] = source
        payload["cached_at"] = datetime.now().isoformat(timespec="seconds")
        payload["_cached_at_monotonic"] = time.monotonic()
        payload["poll_interval_ms"] = self._poll_interval_ms
        return payload

logo_status_cache = LogoStatusCache()

def get_logo_status_payload(db: Session | None = None) -> dict:
    return logo_status_cache.get_status()

def pulse_logo_web_start(db: Session) -> dict:
    return logo_status_cache.pulse_web_start(get_logo_settings(db))

def pulse_logo_web_reset(db: Session) -> dict:
    return logo_status_cache.pulse_web_reset(get_logo_settings(db))

def pulse_logo_web_start_background():
    db = SessionLocal()
    try:
        pulse_logo_web_start(db)
    finally:
        db.close()

def pulse_logo_web_reset_background():
    db = SessionLocal()
    try:
        pulse_logo_web_reset(db)
    finally:
        db.close()
