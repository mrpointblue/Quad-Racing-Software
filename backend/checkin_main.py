"""
Multi-step check-in service for Quad Parallel Race.
Each registration step is on a separate page to avoid scrolling.
"""
import os
import base64
import uuid
from datetime import datetime, date
from pathlib import Path

from fastapi import FastAPI, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import Base, DATA_DIR, engine, get_db
from backend.models import Event, DriverProfile, Participant, Registration, RegistrationSignature
from backend.template_helpers import register_template_helpers
from backend.version import APP_VERSION

app = FastAPI(
    title="Quad Parallel Race Check-in",
    version=APP_VERSION,
)

Base.metadata.create_all(bind=engine)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SIGNATURE_UPLOAD_DIR = Path("backend/static/uploads/signatures")
SIGNATURE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def ensure_schema_column(table_name: str, column_name: str, column_definition: str):
    with engine.connect() as connection:
        columns = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
        column_names = [column[1] for column in columns]

        if column_name not in column_names:
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )
            connection.commit()


ensure_schema_column(
    table_name="registrations",
    column_name="legal_texts_accepted",
    column_definition="BOOLEAN DEFAULT 0",
)

ensure_schema_column(
    table_name="registrations",
    column_name="legal_texts_accepted_at",
    column_definition="VARCHAR",
)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")
templates = Jinja2Templates(directory="backend/templates")
register_template_helpers(templates)

CLUB_OPTIONS = [
    "MSC Lägerdorf v. 1925 e.V.",
    "AC Kellinghusen e.V.",
    "Wühlmäuse Schleswig-Holstein e.V.",
    "Gast",
]

BLOCKING_REGISTRATION_STATUSES = [
    "draft",
    "submitted",
    "under_review",
    "approved",
    "correction_required",
    "confirmed",
]


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def now_string() -> str:
    """Get current datetime as formatted string."""
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def parse_event_date(event_date: str | None) -> date | None:
    """Parse event date from various formats."""
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
    """Get year from event_date."""
    if event and event.event_date:
        parsed_date = parse_event_date(event.event_date)
        if parsed_date:
            return parsed_date.year
    return datetime.now().year


def determine_race_class(birth_year: int, event_year: int) -> int:
    """
    Bestimmt die Rennklasse basierend auf dem Geburtsjahr im Verhältnis zum Veranstaltungsjahr.
    Fix v.1.7.2: Sicherstellung, dass Grenzfälle (z.B. exakt 18/19 Jahre) korrekt in 
    Klasse 4 oder 5 einsortiert werden (Stichtagsregelung Jahrgang).
    """
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
    """Get the currently active event."""
    return db.query(Event).filter(Event.active == True).first()


def get_open_events(db: Session) -> list[Event]:
    """Get all open/future events."""
    events = db.query(Event).filter(Event.closed == False).order_by(Event.event_date, Event.id).all()

    now_date = date.today()
    available = []

    for event in events:
        if event.active:
            available.append(event)
            continue

        parsed_date = parse_event_date(event.event_date)

        if parsed_date is None or parsed_date >= now_date:
            available.append(event)

    return available


def require_event(event_id: int, db: Session) -> Event:
    """Get event by ID or raise 404."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Veranstaltung nicht gefunden")
    return event


def get_occupied_start_numbers(event_id: int, db: Session) -> set[int]:
    """Get all occupied start numbers for an event."""
    participant_numbers = {
        item[0]
        for item in db.query(Participant.start_number).filter(
            Participant.event_id == event_id,
        ).all()
        if item[0] is not None
    }

    registration_numbers = {
        item[0]
        for item in db.query(Registration.start_number).filter(
            Registration.event_id == event_id,
            Registration.registration_status.in_(BLOCKING_REGISTRATION_STATUSES)
        ).all()
        if item[0] is not None
    }

    return participant_numbers | registration_numbers


def is_start_number_occupied(event_id: int, start_number: int, db: Session) -> bool:
    """Check if a start number is already occupied."""
    return start_number in get_occupied_start_numbers(event_id, db)


def get_class_name(class_id: int) -> str:
    """Get the name of a driver class."""
    class_names = {
        1: "Klasse 1 (7-8 Jahre)",
        2: "Klasse 2 (9-11 Jahre)",
        3: "Klasse 3 (12-14 Jahre)",
        4: "Klasse 4 (15-18 Jahre)",
        5: "Klasse 5 (19+ Jahre)",
    }
    return class_names.get(class_id, f"Klasse {class_id}")


def normalize_boolean(value: str | None) -> bool:
    """Convert form value to boolean."""
    return str(value or "").strip().lower() in ["1", "true", "yes", "on"]


def validate_email(email: str | None) -> bool:
    """Validate email address."""
    if not email:
        return False
    return "@" in email and "." in email


def calculate_age_on_date(birth_date: date, reference_date: date) -> int:
    """Calculate exact age at a reference date."""
    age = reference_date.year - birth_date.year
    if (reference_date.month, reference_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def is_minor_today(birth_date_value: str | None) -> bool:
    """
    Prüft die Minderjährigkeit basierend auf dem heutigen Tag. 
    Dies ist entscheidend für die Haftungsausschlüsse am Terminal: 
    Wer heute noch 17 ist, benötigt zwingend die Unterschrift der Eltern, 
    auch wenn er am Renntag bereits 18 sein sollte (Rechtssicherheit bei Nennung).
    """
    if not birth_date_value:
        return False

    try:
        parsed_birth_date = datetime.strptime(birth_date_value, "%Y-%m-%d").date()
    except ValueError:
        return False

    return calculate_age_on_date(parsed_birth_date, date.today()) < 18


def save_signature_image(signature_data: str | None, prefix: str) -> str | None:
    """Save a canvas data URL as PNG and return its static path."""
    clean_data = (signature_data or "").strip()
    expected_prefix = "data:image/png;base64,"

    if not clean_data or not clean_data.startswith(expected_prefix):
        return None

    try:
        image_bytes = base64.b64decode(clean_data[len(expected_prefix):], validate=True)
    except Exception:
        return None

    if len(image_bytes) < 100:
        return None

    filename = f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}.png"
    image_path = SIGNATURE_UPLOAD_DIR / filename
    image_path.write_bytes(image_bytes)

    return f"/static/uploads/signatures/{filename}"


def validate_club(club: str | None) -> str:
    """Validate and normalize a club selected from the server-provided list."""
    clean_club = (club or "").strip()
    if clean_club not in CLUB_OPTIONS:
        raise HTTPException(status_code=400, detail="Bitte einen gültigen Verein auswählen.")
    return clean_club


def find_driver_profile_by_identifier(
    db: Session,
    adac_member_number: str | None = None,
    youth_pass_number: str | None = None,
) -> DriverProfile | None:
    """Find an active driver profile by ADAC number or youth pass number."""
    clean_adac = (adac_member_number or "").strip()
    clean_youth = (youth_pass_number or "").strip()

    if clean_adac:
        driver = db.query(DriverProfile).filter(
            DriverProfile.active == True,
            DriverProfile.adac_number == clean_adac,
        ).first()
        if driver:
            return driver

    if clean_youth:
        return db.query(DriverProfile).filter(
            DriverProfile.active == True,
            DriverProfile.youth_license_number == clean_youth,
        ).first()

    return None


def has_existing_event_entry(
    db: Session,
    event_id: int,
    driver_profile_id: int | None = None,
    adac_member_number: str | None = None,
    youth_pass_number: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    birth_year: int | None = None,
) -> bool:
    """Return True if this driver is already entered for the event."""
    clean_adac = (adac_member_number or "").strip()
    clean_youth = (youth_pass_number or "").strip()
    clean_first_name = (first_name or "").strip()
    clean_last_name = (last_name or "").strip()

    if driver_profile_id:
        participant = db.query(Participant).filter(
            Participant.event_id == event_id,
            Participant.driver_profile_id == driver_profile_id,
        ).first()
        if participant:
            return True

        registration = db.query(Registration).filter(
            Registration.event_id == event_id,
            Registration.driver_profile_id == driver_profile_id,
            Registration.registration_status.in_(BLOCKING_REGISTRATION_STATUSES),
        ).first()
        if registration:
            return True

    if clean_adac:
        participant = db.query(Participant).filter(
            Participant.event_id == event_id,
            Participant.adac_number == clean_adac,
        ).first()
        if participant:
            return True

        registration = db.query(Registration).filter(
            Registration.event_id == event_id,
            Registration.adac_member_number == clean_adac,
            Registration.registration_status.in_(BLOCKING_REGISTRATION_STATUSES),
        ).first()
        if registration:
            return True

    if clean_youth:
        participant = db.query(Participant).filter(
            Participant.event_id == event_id,
            Participant.youth_license_number == clean_youth,
        ).first()
        if participant:
            return True

        registration = db.query(Registration).filter(
            Registration.event_id == event_id,
            Registration.youth_pass_number == clean_youth,
            Registration.registration_status.in_(BLOCKING_REGISTRATION_STATUSES),
        ).first()
        if registration:
            return True

    if clean_first_name and clean_last_name and birth_year:
        participant = db.query(Participant).filter(
            Participant.event_id == event_id,
            Participant.first_name == clean_first_name,
            Participant.last_name == clean_last_name,
            Participant.birth_year == birth_year,
        ).first()
        if participant:
            return True

        registration = db.query(Registration).filter(
            Registration.event_id == event_id,
            Registration.first_name == clean_first_name,
            Registration.last_name == clean_last_name,
            Registration.birth_year == birth_year,
            Registration.registration_status.in_(BLOCKING_REGISTRATION_STATUSES),
        ).first()
        if registration:
            return True

    return False


# ============================================================================
# ROOT ROUTES
# ============================================================================

@app.get("/")
def root():
    """Redirect to check-in start page."""
    return RedirectResponse(url="/checkin", status_code=303)


@app.get("/checkin")
def checkin_root(request: Request, db: Session = Depends(get_db)):
    """Check-in start page - select event."""
    active_event = get_active_event(db)
    open_events = get_open_events(db)
    other_events = [
        event
        for event in open_events
        if not active_event or event.id != active_event.id
    ]

    return templates.TemplateResponse(
        request,
        "checkin_start.html",
        {
            "request": request,
            "active_event": active_event,
            "open_events": open_events,
            "other_events": other_events,
        },
    )


@app.get("/checkin/events")
def checkin_events_list(request: Request, db: Session = Depends(get_db)):
    """List all open events."""
    open_events = get_open_events(db)

    return templates.TemplateResponse(
        request,
        "checkin_events.html",
        {
            "request": request,
            "open_events": open_events,
        },
    )


@app.post("/checkin/events/{event_id}/select")
def checkin_event_select(event_id: int, db: Session = Depends(get_db)):
    """Select an event and redirect to step 2."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")
    return RedirectResponse(url=f"/checkin/events/{event_id}/step2", status_code=303)


# ============================================================================
# STEP 2: DIGITAL YOUTH CARD / DRIVER LOOKUP
# ============================================================================

@app.get("/checkin/events/{event_id}/step2")
def checkin_step2(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Step 2: Ask for digital youth card and optional driver lookup."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    return templates.TemplateResponse(
        request,
        "checkin_step2_start_number.html",
        {
            "request": request,
            "event": event,
            "form_values": {},
            "lookup_message": None,
        },
    )


@app.post("/checkin/events/{event_id}/step2")
def checkin_step2_post(
    event_id: int,
    request: Request,
    has_digital_youth_card: str = Form("0"),
    adac_member_number: str | None = Form(None),
    youth_pass_number: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Step 2: Lookup driver profile or proceed to manual data entry."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    digital = normalize_boolean(has_digital_youth_card)
    clean_adac = (adac_member_number or "").strip()
    clean_youth = (youth_pass_number or "").strip()

    from urllib.parse import quote
    if not clean_adac and not clean_youth:
        if digital:
            return templates.TemplateResponse(
                request,
                "checkin_step2_start_number.html",
                {
                    "request": request,
                    "event": event,
                    "form_values": {
                        "has_digital_youth_card": "1",
                        "adac_member_number": "",
                        "youth_pass_number": "",
                    },
                    "lookup_message": "Scanner ist vorbereitet. Bitte aktuell die gescannte oder manuell abgelesene ADAC-Nummer oder Jugendausweisnummer eingeben.",
                },
            )

        return RedirectResponse(
            url=f"/checkin/events/{event_id}/step3?digital=False",
            status_code=303,
        )

    driver = find_driver_profile_by_identifier(
        db=db,
        adac_member_number=clean_adac,
        youth_pass_number=clean_youth,
    )

    if not driver:
        return RedirectResponse(
            url=(
                f"/checkin/events/{event_id}/step3?"
                f"digital={digital}&lookup=not_found&"
                f"adac={quote(clean_adac)}&"
                f"youth={quote(clean_youth)}"
            ),
            status_code=303,
        )

    club = driver.club if driver.club in CLUB_OPTIONS else ""
    return RedirectResponse(
        url=(
            f"/checkin/events/{event_id}/step3?"
            f"digital={digital}&lookup=found&driver_id={driver.id}&"
            f"fn={quote(driver.first_name)}&"
            f"ln={quote(driver.last_name)}&"
            f"club={quote(club or '')}&"
            f"adac={quote(driver.adac_number or clean_adac)}&"
            f"youth={quote(driver.youth_license_number or clean_youth)}&"
            f"birth_year={driver.birth_year}"
        ),
        status_code=303,
    )


# ============================================================================
# STEP 3: PERSONAL DATA
# ============================================================================

@app.get("/checkin/events/{event_id}/step3")
def checkin_step3(
    event_id: int,
    sn: int = None,
    digital: str | None = None,
    lookup: str | None = None,
    fn: str | None = None,
    ln: str | None = None,
    bd: str | None = None,
    club: str | None = None,
    adac: str | None = None,
    youth: str | None = None,
    birth_year: int | None = None,
    driver_id: int | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Step 3: Enter personal data."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if sn is not None and (sn < 1 or sn > 99):
        return RedirectResponse(url=f"/checkin/events/{event_id}/step2", status_code=303)

    lookup_messages = {
        "found": "Fahrer wurde in der Datenbank gefunden. Bitte Geburtsdatum prüfen oder ergänzen.",
        "not_found": "Keine passenden Daten gefunden. Bitte die Angaben manuell erfassen.",
        "missing": "Bitte ADAC-Mitgliedsnummer oder Jugendausweisnummer eingeben. Alternativ ohne digitale Jugendkarte fortfahren.",
    }

    form_values = {
        "first_name": fn or "",
        "last_name": ln or "",
        "birth_date": bd or "",
        "club": club if club in CLUB_OPTIONS else "",
        "adac_member_number": adac or "",
        "youth_pass_number": youth or "",
        "digital_youth_pass_available": normalize_boolean(digital),
        "driver_id": driver_id or "",
        "birth_year": birth_year or "",
    }
    already_registered = has_existing_event_entry(
        db=db,
        event_id=event.id,
        driver_profile_id=driver_id,
        adac_member_number=adac,
        youth_pass_number=youth,
        first_name=fn,
        last_name=ln,
        birth_year=birth_year,
    )

    return templates.TemplateResponse(
        request,
        "checkin_step3_personal.html",
        {
            "request": request,
            "event": event,
            "start_number": sn,
            "form_values": form_values,
            "club_options": CLUB_OPTIONS,
            "lookup_message": lookup_messages.get(lookup),
            "already_registered": already_registered,
        },
    )


@app.post("/checkin/events/{event_id}/step3")
def checkin_step3_post(
    event_id: int,
    start_number: str | None = Form(None),
    driver_profile_id: str | None = Form(None),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_date: str = Form(...),
    club: str = Form(...),
    adac_member_number: str | None = Form(None),
    youth_pass_number: str | None = Form(None),
    digital_youth_pass_available: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Step 3: Validate personal data, determine class and proceed to step 5."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if not first_name.strip():
        raise HTTPException(status_code=400, detail="Vorname erforderlich.")
    if not last_name.strip():
        raise HTTPException(status_code=400, detail="Nachname erforderlich.")
    if not birth_date:
        raise HTTPException(status_code=400, detail="Geburtsdatum erforderlich.")
    if not club.strip():
        raise HTTPException(status_code=400, detail="Verein erforderlich.")
    clean_club = validate_club(club)
    clean_driver_profile_id = int(driver_profile_id) if str(driver_profile_id or "").strip().isdigit() else None

    # Validate birth date format
    try:
        parsed_birth_date = datetime.strptime(birth_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Geburtsdatum.")

    try:
        event_year = get_event_year(event)
        class_id = determine_race_class(parsed_birth_date.year, event_year)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from urllib.parse import quote
    if has_existing_event_entry(
        db=db,
        event_id=event.id,
        driver_profile_id=clean_driver_profile_id,
        adac_member_number=adac_member_number,
        youth_pass_number=youth_pass_number,
        first_name=first_name,
        last_name=last_name,
        birth_year=parsed_birth_date.year,
    ):
        return RedirectResponse(
            url=(
                f"/checkin/events/{event_id}/step3?"
                f"lookup=found&"
                f"driver_id={clean_driver_profile_id or ''}&"
                f"fn={quote(first_name.strip())}&"
                f"ln={quote(last_name.strip())}&"
                f"adac={quote(adac_member_number or '')}&"
                f"youth={quote(youth_pass_number or '')}&"
                f"birth_year={parsed_birth_date.year}"
            ),
            status_code=303,
        )

    class_name = get_class_name(class_id)
    clean_start_number = (start_number or "").strip()
    sn_part = f"sn={clean_start_number}&" if clean_start_number else ""
    driver_part = f"driver_id={clean_driver_profile_id}&" if clean_driver_profile_id else ""

    return RedirectResponse(
        url=(
            f"/checkin/events/{event_id}/step5?"
            f"{sn_part}"
            f"{driver_part}"
            f"fn={quote(first_name.strip())}&"
            f"ln={quote(last_name.strip())}&"
            f"bd={birth_date}&"
            f"club={quote(clean_club)}&"
            f"cid={class_id}&"
            f"cn={quote(class_name)}&"
            f"adac={quote(adac_member_number or '')}&"
            f"youth={quote(youth_pass_number or '')}&"
            f"digital={normalize_boolean(digital_youth_pass_available)}"
        ),
        status_code=303
    )


# ============================================================================
# STEP 4: AUTO-DETERMINE CLASS
# ============================================================================

@app.get("/checkin/events/{event_id}/step4")
def checkin_step4(
    event_id: int,
    sn: int = None,
    fn: str = None,
    ln: str = None,
    bd: str = None,
    club: str = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Legacy step: determine driver class automatically and continue."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if not all([sn, fn, ln, bd, club]):
        return RedirectResponse(url=f"/checkin/events/{event_id}/step2", status_code=303)

    try:
        parsed_birth_date = datetime.strptime(bd, "%Y-%m-%d").date()
        class_id = determine_race_class(parsed_birth_date.year, get_event_year(event))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    class_name = get_class_name(class_id)

    from urllib.parse import quote
    return RedirectResponse(
        url=(
            f"/checkin/events/{event_id}/step5?"
            f"sn={sn}&"
            f"fn={quote(fn)}&"
            f"ln={quote(ln)}&"
            f"bd={bd}&"
            f"club={quote(club)}&"
            f"cid={class_id}&"
            f"cn={quote(class_name)}"
        ),
        status_code=303,
    )


@app.post("/checkin/events/{event_id}/step4")
def checkin_step4_post(
    event_id: int,
    start_number: int = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_date: str = Form(...),
    club: str = Form(...),
    class_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    """Legacy step: ignore manual class input and determine class automatically."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    try:
        parsed_birth_date = datetime.strptime(birth_date, "%Y-%m-%d").date()
        class_id = determine_race_class(parsed_birth_date.year, get_event_year(event))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    class_name = get_class_name(class_id)

    from urllib.parse import quote
    return RedirectResponse(
        url=(
            f"/checkin/events/{event_id}/step5?"
            f"sn={start_number}&"
            f"fn={quote(first_name)}&"
            f"ln={quote(last_name)}&"
            f"bd={birth_date}&"
            f"club={quote(club)}&"
            f"cid={class_id}&"
            f"cn={quote(class_name)}"
        ),
        status_code=303
    )


# ============================================================================
# STEP 5: EXTRA INFORMATION
# ============================================================================

@app.get("/checkin/events/{event_id}/step5")
def checkin_step5(
    event_id: int,
    sn: int = None,
    driver_id: int | None = None,
    fn: str = None,
    ln: str = None,
    bd: str = None,
    club: str = None,
    cid: int = None,
    cn: str = None,
    adac: str | None = None,
    youth: str | None = None,
    digital: str | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Step 5: Select start number."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if not all([fn, ln, bd, club, cid]):
        return RedirectResponse(url=f"/checkin/events/{event_id}/step2", status_code=303)

    occupied_numbers = {
        number
        for number in range(1, 100)
        if is_start_number_occupied(event.id, number, db)
    }

    return templates.TemplateResponse(
        request,
        "checkin_step5_extra.html",
        {
            "request": request,
            "event": event,
            "start_number": sn,
            "driver_profile_id": driver_id,
            "first_name": fn,
            "last_name": ln,
            "birth_date": bd,
            "club": club,
            "class_id": cid,
            "class_name": cn,
            "adac_member_number": adac,
            "youth_pass_number": youth,
            "digital_youth_pass_available": normalize_boolean(digital),
            "occupied_numbers": occupied_numbers,
        },
    )


@app.post("/checkin/events/{event_id}/step5")
def checkin_step5_post(
    event_id: int,
    start_number: int = Form(...),
    driver_profile_id: str | None = Form(None),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_date: str = Form(...),
    club: str = Form(...),
    class_id: int = Form(...),
    adac_member_number: str | None = Form(None),
    youth_pass_number: str | None = Form(None),
    digital_youth_pass_available: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Step 5: Proceed to step 6."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if start_number < 1 or start_number > 99:
        raise HTTPException(status_code=400, detail="Startnummer ungültig.")

    if is_start_number_occupied(event_id, start_number, db):
        raise HTTPException(status_code=400, detail="Startnummer bereits belegt.")

    clean_driver_profile_id = int(driver_profile_id) if str(driver_profile_id or "").strip().isdigit() else None
    driver_part = f"driver_id={clean_driver_profile_id}&" if clean_driver_profile_id else ""

    from urllib.parse import quote
    return RedirectResponse(
        url=(
            f"/checkin/events/{event_id}/step6?"
            f"sn={start_number}&"
            f"{driver_part}"
            f"fn={quote(first_name)}&"
            f"ln={quote(last_name)}&"
            f"bd={birth_date}&"
            f"club={quote(club)}&"
            f"cid={class_id}&"
            f"adac={quote(adac_member_number or '')}&"
            f"youth={quote(youth_pass_number or '')}&"
            f"digital={normalize_boolean(digital_youth_pass_available)}"
        ),
        status_code=303
    )


# ============================================================================
# STEP 6: EMAIL & SIGNATURE
# ============================================================================

@app.get("/checkin/events/{event_id}/step6")
def checkin_step6(
    event_id: int,
    sn: int = None,
    driver_id: int | None = None,
    fn: str = None,
    ln: str = None,
    bd: str = None,
    club: str = None,
    cid: int = None,
    adac: str = None,
    youth: str = None,
    digital: str = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Step 6: Enter email and signature."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if not all([sn, fn, ln, bd, club, cid]):
        return RedirectResponse(url=f"/checkin/events/{event_id}/step2", status_code=303)

    minor = is_minor_today(bd)

    return templates.TemplateResponse(
        request,
        "checkin_step6_contact.html",
        {
            "request": request,
            "event": event,
            "start_number": sn,
            "driver_profile_id": driver_id,
            "first_name": fn,
            "last_name": ln,
            "birth_date": bd,
            "club": club,
            "class_id": cid,
            "adac_member_number": adac,
            "youth_pass_number": youth,
            "digital_youth_pass_available": normalize_boolean(digital),
            "form_values": {},
            "is_minor": minor,
        },
    )


@app.post("/checkin/events/{event_id}/step6")
def checkin_step6_post(
    event_id: int,
    start_number: int = Form(...),
    driver_profile_id: str | None = Form(None),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_date: str = Form(...),
    club: str = Form(...),
    class_id: int = Form(...),
    adac_member_number: str | None = Form(None),
    youth_pass_number: str | None = Form(None),
    digital_youth_pass_available: str | None = Form(None),
    send_protocol_email: str | None = Form(None),
    email: str | None = Form(None),
    signature_name: str = Form(...),
    signature_image_data: str | None = Form(None),
    guardian_signature_name: str | None = Form(None),
    guardian_signature_image_data: str | None = Form(None),
    guardian_address: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Step 6: Validate email/signature and proceed to summary."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if not signature_name.strip():
        raise HTTPException(status_code=400, detail="Unterschrift erforderlich.")
    signature_image_path = save_signature_image(signature_image_data, "driver")
    if not signature_image_path:
        raise HTTPException(status_code=400, detail="Bitte im Unterschriftenfeld Fahrer/in unterschreiben.")

    minor = is_minor_today(birth_date)
    if minor and not (guardian_signature_name or "").strip():
        raise HTTPException(status_code=400, detail="Unterschrift Erziehungsberechtigten erforderlich.")
    guardian_signature_image_path = None
    if minor:
        guardian_signature_image_path = save_signature_image(guardian_signature_image_data, "guardian")
        if not guardian_signature_image_path:
            raise HTTPException(status_code=400, detail="Bitte Unterschrift Erziehungsberechtigte/r erfassen.")
    if minor and not (guardian_address or "").strip():
        raise HTTPException(status_code=400, detail="Anschrift Erziehungsberechtigten erforderlich.")

    if normalize_boolean(send_protocol_email):
        if not validate_email(email):
            raise HTTPException(status_code=400, detail="Gültige Email erforderlich.")

    clean_driver_profile_id = int(driver_profile_id) if str(driver_profile_id or "").strip().isdigit() else None
    driver_part = f"driver_id={clean_driver_profile_id}&" if clean_driver_profile_id else ""

    from urllib.parse import quote
    return RedirectResponse(
        url=(
            f"/checkin/events/{event_id}/step7?"
            f"sn={start_number}&"
            f"{driver_part}"
            f"fn={quote(first_name)}&"
            f"ln={quote(last_name)}&"
            f"bd={birth_date}&"
            f"club={quote(club)}&"
            f"cid={class_id}&"
            f"adac={quote(adac_member_number or '')}&"
            f"youth={quote(youth_pass_number or '')}&"
            f"digital={normalize_boolean(digital_youth_pass_available)}&"
            f"email_send={normalize_boolean(send_protocol_email)}&"
            f"email={quote(email or '')}&"
            f"sig={quote(signature_name)}&"
            f"sig_path={quote(signature_image_path)}&"
            f"guardian_sig={quote(guardian_signature_name or '')}&"
            f"guardian_sig_path={quote(guardian_signature_image_path or '')}&"
            f"guardian_address={quote(guardian_address or '')}"
        ),
        status_code=303
    )


# ============================================================================
# STEP 7: SUMMARY & SUBMIT
# ============================================================================

@app.get("/checkin/events/{event_id}/step7")
def checkin_step7(
    event_id: int,
    sn: int = None,
    driver_id: int | None = None,
    fn: str = None,
    ln: str = None,
    bd: str = None,
    club: str = None,
    cid: int = None,
    adac: str = None,
    youth: str = None,
    digital: str = None,
    email_send: str = None,
    email: str = None,
    sig: str = None,
    sig_path: str | None = None,
    guardian_sig: str | None = None,
    guardian_sig_path: str | None = None,
    guardian_address: str | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Step 7: Review and submit registration."""
    event = require_event(event_id, db)
    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    if not all([sn, fn, ln, bd, club, cid, sig]):
        return RedirectResponse(url=f"/checkin/events/{event_id}/step2", status_code=303)

    class_name = get_class_name(cid)
    minor = is_minor_today(bd)

    return templates.TemplateResponse(
        request,
        "checkin_step7_summary.html",
        {
            "request": request,
            "event": event,
            "start_number": sn,
            "driver_profile_id": driver_id,
            "first_name": fn,
            "last_name": ln,
            "birth_date": bd,
            "club": club,
            "class_id": cid,
            "class_name": class_name,
            "adac_member_number": adac,
            "youth_pass_number": youth,
            "digital_youth_pass_available": normalize_boolean(digital),
            "send_protocol_email": normalize_boolean(email_send),
            "email": email,
            "signature_name": sig,
            "signature_image_path": sig_path,
            "guardian_signature_name": guardian_sig,
            "guardian_signature_image_path": guardian_sig_path,
            "guardian_address": guardian_address,
            "is_minor": minor,
        },
    )


@app.post("/checkin/events/{event_id}/submit")
def checkin_submit(
    event_id: int,
    request: Request,
    start_number: int = Form(...),
    driver_profile_id: str | None = Form(None),
    first_name: str = Form(...),
    last_name: str = Form(...),
    birth_date: str = Form(...),
    club: str = Form(...),
    class_id: int = Form(...),
    adac_member_number: str | None = Form(None),
    youth_pass_number: str | None = Form(None),
    digital_youth_pass_available: str | None = Form(None),
    send_protocol_email: str | None = Form(None),
    email: str | None = Form(None),
    signature_name: str = Form(...),
    signature_image_path: str | None = Form(None),
    guardian_signature_name: str | None = Form(None),
    guardian_signature_image_path: str | None = Form(None),
    guardian_address: str | None = Form(None),
    legal_texts_accepted: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Submit registration."""
    event = require_event(event_id, db)

    if event.closed:
        raise HTTPException(status_code=403, detail="Diese Veranstaltung ist geschlossen.")

    # Parse birth year
    try:
        parsed_birth_date = datetime.strptime(birth_date, "%Y-%m-%d").date()
        birth_year = parsed_birth_date.year
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Geburtsdatum.")

    minor = calculate_age_on_date(parsed_birth_date, date.today()) < 18

    def render_submit_error(message: str):
        return templates.TemplateResponse(
            request,
            "checkin_step7_summary.html",
            {
                "request": request,
                "event": event,
                "start_number": start_number,
                "driver_profile_id": driver_profile_id,
                "first_name": first_name,
                "last_name": last_name,
                "birth_date": birth_date,
                "club": club,
                "class_id": class_id,
                "class_name": get_class_name(class_id),
                "adac_member_number": adac_member_number,
                "youth_pass_number": youth_pass_number,
                "digital_youth_pass_available": normalize_boolean(digital_youth_pass_available),
                "send_protocol_email": normalize_boolean(send_protocol_email),
                "email": email,
                "signature_name": signature_name,
                "signature_image_path": signature_image_path,
                "guardian_signature_name": guardian_signature_name,
                "guardian_signature_image_path": guardian_signature_image_path,
                "guardian_address": guardian_address,
                "is_minor": minor,
                "error": message,
            },
            status_code=400,
        )

    if is_start_number_occupied(event_id, start_number, db):
        return render_submit_error("Startnummer mittlerweile belegt.")

    if minor and not (guardian_signature_name or "").strip():
        return render_submit_error("Unterschrift Erziehungsberechtigten erforderlich.")
    if not (signature_image_path or "").strip():
        return render_submit_error("Bitte im Unterschriftenfeld unterschreiben.")
    if minor and not (guardian_signature_image_path or "").strip():
        return render_submit_error("Bitte Unterschrift Erziehungsberechtigte/r erfassen.")
    if minor and not (guardian_address or "").strip():
        return render_submit_error("Anschrift Erziehungsberechtigten erforderlich.")
    if not normalize_boolean(legal_texts_accepted):
        return render_submit_error("Bitte akzeptieren Sie Haftungsausschluss und Datenschutz.")

    # Verknüpfung mit bestehendem Profil oder Neuerstellung
    # Fix v.1.7.2: Verhindert doppelte Registrierungen durch Prüfung von Vorname, 
    # Nachname und Geburtsjahr in Kombination mit der Event-ID.
    clean_driver_profile_id = int(driver_profile_id) if str(driver_profile_id or "").strip().isdigit() else None
    if has_existing_event_entry(
        db=db,
        event_id=event.id,
        driver_profile_id=clean_driver_profile_id,
        adac_member_number=adac_member_number,
        youth_pass_number=youth_pass_number,
        first_name=first_name,
        last_name=last_name,
        birth_year=birth_year,
    ):
        return render_submit_error("Fahrer bereits registriert.")

    # Create registration
    registration = Registration(
        event_id=event.id,
        driver_profile_id=clean_driver_profile_id,
        registration_source="terminal",
        registration_status="under_review",
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        birth_date=birth_date,
        birth_year=birth_year,
        age_at_event=get_event_year(event) - birth_year,
        class_id=class_id,
        club=club.strip(),
        adac_member_number=adac_member_number.strip() if adac_member_number else None,
        phone=None,
        email=email.strip() if email else None,
        start_number=start_number,
        youth_pass_number=youth_pass_number.strip() if youth_pass_number else None,
        digital_youth_pass_available=normalize_boolean(digital_youth_pass_available),
        send_protocol_email=normalize_boolean(send_protocol_email),
        legal_texts_accepted=True,
        legal_texts_accepted_at=now_string(),
        payment_status="open",
        mail_status="pending" if normalize_boolean(send_protocol_email) else "not_requested",
        created_at=now_string(),
        updated_at=now_string(),
        submitted_at=now_string(),
        confirmed_at=None,
        confirmed_by=None,
    )

    db.add(registration)
    db.flush()

    # Create signature
    signature = RegistrationSignature(
        registration_id=registration.id,
        signature_type="driver",
        name=signature_name.strip(),
        address=None,
        signature_image_path=signature_image_path,
        signed_at=now_string(),
    )

    db.add(signature)

    if minor:
        guardian_signature = RegistrationSignature(
            registration_id=registration.id,
            signature_type="guardian",
            name=guardian_signature_name.strip(),
            address=guardian_address.strip(),
            signature_image_path=guardian_signature_image_path,
            signed_at=now_string(),
        )
        db.add(guardian_signature)

    db.commit()

    return RedirectResponse(url="/checkin/done", status_code=303)


@app.get("/checkin/legal")
def checkin_legal(request: Request):
    """Legal texts for terminal check-in."""
    return templates.TemplateResponse(
        request,
        "checkin_legal.html",
        {
            "request": request,
        },
    )


@app.get("/checkin/done")
def checkin_done(request: Request):
    """Registration complete."""
    return templates.TemplateResponse(
        request,
        "checkin_done.html",
        {
            "request": request,
        },
    )
