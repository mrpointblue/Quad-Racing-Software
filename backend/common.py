from backend.models import Event
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import date
from datetime import datetime
from backend.constants import EVENT_YEAR


def now_string() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")

def parse_event_date(event_date: str | None) -> date | None:
    if not event_date:
        return None

    event_date = event_date.strip()

    for date_format in ["%d.%m.%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(event_date, date_format).date()
        except ValueError:
            pass

    return None

def get_event_year(event: Event | None) -> int:
    if event and event.event_date:
        parsed_date = parse_event_date(event.event_date)

        if parsed_date:
            return parsed_date.year

    return datetime.now().year

def is_event_date_reached(event: Event) -> bool:
    parsed_date = parse_event_date(event.event_date)

    if parsed_date is None:
        return True

    return date.today() >= parsed_date

def get_event_date_block_message(event: Event) -> str:
    return (
        "Rennbetrieb erst ab Veranstaltungsdatum möglich. "
        f"Eingetragenes Datum: {event.event_date or '-'}"
    )

def determine_race_class(birth_year: int, event_year: int = EVENT_YEAR) -> int:
    age = event_year - birth_year

    if age in [7, 8]:
        return 1

    if age in [9, 10, 11]:
        return 2

    if age in [12, 13, 14]:
        return 3

    if age in [15, 16, 17, 18]:
        return 4

    if age >= 19:
        return 5

    raise ValueError(
        f"Geburtsjahr {birth_year} passt im Veranstaltungsjahr {event_year} zu keiner gültigen Klasse"
    )

def get_active_event(db: Session) -> Event | None:
    return db.query(Event).filter(Event.active == True).first()

def require_active_event(db: Session) -> Event:
    event = get_active_event(db)

    if not event:
        raise HTTPException(
            status_code=400,
            detail="Keine aktive Veranstaltung vorhanden",
        )

    return event

def require_event(
    event_id: int,
    db: Session,
) -> Event:
    event = db.query(Event).filter(
        Event.id == event_id
    ).first()

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Veranstaltung nicht gefunden",
        )

    return event

def ensure_event_is_open(event: Event):
    if event.closed:
        raise HTTPException(
            status_code=403,
            detail="Die Veranstaltung ist geschlossen. Änderungen sind nicht mehr möglich.",
        )

def ensure_event_is_active(event: Event):
    if event.active is not True:
        raise HTTPException(
            status_code=409,
            detail=(
                "Diese Veranstaltung ist nicht aktiv. "
                "Unter Veranstaltung zuerst Aktiv setzen."
            ),
        )

def redirect_to_active_event_path(
    db: Session,
    path_suffix: str,
):
    active_event = get_active_event(db)

    if not active_event:
        return RedirectResponse(
            url="/ui/events",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ui/events/{active_event.id}{path_suffix}",
        status_code=303,
    )

def clean_optional_form_value(value: str | None) -> str | None:
    if value is None:
        return None

    return value.strip() or None

def parse_form_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in ["1", "true", "yes", "on"]

def get_youth_card_checked_at(
    checked: bool,
    existing_checked_at: str | None = None,
) -> str | None:
    if not checked:
        return None

    return existing_checked_at or now_string()
