from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi.responses import RedirectResponse
from backend.models import RegistrationNotification
from fastapi import Request
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.common import now_string


router = APIRouter()


@router.post("/ui/notifications/{notification_id}/acknowledge")
def acknowledge_registration_notification(
    notification_id: int,
    request: Request,
    acknowledged_by: str = Form("Zeitnahme"),
    db: Session = Depends(get_db),
):
    notification = db.query(RegistrationNotification).filter(
        RegistrationNotification.id == notification_id,
    ).first()

    if notification:
        notification.acknowledged = True
        notification.acknowledged_at = now_string()
        notification.acknowledged_by = acknowledged_by.strip() or "Zeitnahme"
        db.commit()

    return RedirectResponse(
        url=request.headers.get("referer") or "/ui/events",
        status_code=303,
    )

@router.get("/api/events/{event_id}/notifications/pending")
def api_pending_registration_notifications(
    event_id: int,
    db: Session = Depends(get_db),
):
    notifications = db.query(RegistrationNotification).filter(
        RegistrationNotification.event_id == event_id,
        RegistrationNotification.acknowledged == False,
    ).order_by(
        RegistrationNotification.id.desc()
    ).all()

    return {
        "notifications": [
            {
                "id": notification.id,
                "group_name": notification.group_name,
                "message": notification.message,
                "created_at": notification.created_at or "",
            }
            for notification in notifications
        ]
    }
