from urllib.parse import urlencode, urlsplit


MARSHAL_QR_BASE_URL = "http://race.local:8000"


def build_marshal_station_path(
    event_id: int,
    penalty_type: str,
    lane: str = "",
    sector: int | str | None = None,
) -> str:
    params = {
        "type": penalty_type,
    }

    if penalty_type != "general":
        params["lane"] = lane

    if penalty_type == "sector":
        params["sector"] = str(sector)

    return f"/ui/events/{event_id}/marshal?{urlencode(params)}"


def build_marshal_station_url(
    event_id: int,
    penalty_type: str,
    lane: str = "",
    sector: int | str | None = None,
    base_url: str = MARSHAL_QR_BASE_URL,
) -> str:
    return f"{base_url.rstrip('/')}{build_marshal_station_path(event_id, penalty_type, lane, sector)}"


def normalize_marshal_url(url: str, event_id: int, base_url: str = MARSHAL_QR_BASE_URL) -> str:
    if not url:
        return ""

    parsed = urlsplit(url)
    event_marshal_path = f"/ui/events/{event_id}/marshal"

    if parsed.path != event_marshal_path:
        return url

    path = parsed.path
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return f"{base_url.rstrip('/')}{path}"
