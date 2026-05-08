from datetime import datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates

from backend.database import SessionLocal
from backend.models import Event, RegistrationNotification


def current_time_string() -> str:
    return datetime.now().strftime("%H:%M:%S")


def registration_notification_context(request: Request) -> dict:
    db = SessionLocal()

    try:
        event_id = request.path_params.get("event_id")

        if event_id is None:
            active_event = db.query(Event).filter(Event.active == True).first()
            event_id = active_event.id if active_event else None

        if event_id is None:
            return {"pending_registration_notifications": []}

        notifications = db.query(RegistrationNotification).filter(
            RegistrationNotification.event_id == int(event_id),
            RegistrationNotification.acknowledged == False,
        ).order_by(
            RegistrationNotification.id.desc()
        ).all()

        return {"pending_registration_notifications": notifications}
    finally:
        db.close()


def register_template_helpers(templates: Jinja2Templates):
    templates.env.globals["current_time_string"] = current_time_string

    if registration_notification_context not in templates.context_processors:
        templates.context_processors.append(registration_notification_context)
