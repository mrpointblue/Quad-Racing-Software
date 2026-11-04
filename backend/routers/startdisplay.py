from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
from backend.database import get_db
from backend.application.settings_service import get_setting, set_setting


router = APIRouter()


@router.post("/ui/settings/startdisplay")
def ui_save_startdisplay_settings(
    startdisplay_enabled: str = Form("false"),
    startdisplay_host: str = Form(""),
    startdisplay_port: str = Form("80"),
    startdisplay_endpoint: str = Form("/display"),
    db: Session = Depends(get_db),
):
    set_setting(db, "startdisplay_enabled", startdisplay_enabled)
    set_setting(db, "startdisplay_host", startdisplay_host.strip())
    set_setting(db, "startdisplay_port", startdisplay_port.strip())
    set_setting(db, "startdisplay_endpoint", startdisplay_endpoint.strip())
    db.commit()

    return RedirectResponse(
        url="/ui/settings?success=Startdisplay-Einstellungen gespeichert",
        status_code=303,
    )

@router.post("/ui/settings/startdisplay/test")
def ui_test_startdisplay_connection(
    db: Session = Depends(get_db),
):
    try:
        import httpx
        import json
        
        enabled = get_setting(db, "startdisplay_enabled", "false")
        if enabled != "true":
            return RedirectResponse(
                url="/ui/settings?error=Startdisplay nicht aktiviert",
                status_code=303,
            )
        
        host = get_setting(db, "startdisplay_host", "").strip()
        if not host:
            return RedirectResponse(
                url="/ui/settings?error=Kein Host konfiguriert",
                status_code=303,
            )
        
        port = get_setting(db, "startdisplay_port", "80").strip()
        endpoint = get_setting(db, "startdisplay_endpoint", "/display").strip()
        
        # Build URL
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        
        url = f"http://{host}:{port}{endpoint}"
        
        # Send test payload
        test_payload = {
            "mode": "start_numbers",
            "lane_a_start_number": 23,
            "lane_b_start_number": 54
        }
        
        headers = {"Content-Type": "application/json"}
        data = json.dumps(test_payload)
        
        with httpx.Client(timeout=1.0) as client:
            response = client.post(url, data=data, headers=headers)
            
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            set_setting(db, "startdisplay_last_send", now)
            
            if response.status_code == 200:
                set_setting(db, "startdisplay_last_success", now)
                set_setting(db, "startdisplay_last_error", "")
                set_setting(db, "startdisplay_last_test_payload", "A:23 B:54")
                db.commit()
                return RedirectResponse(
                    url="/ui/settings?success=Verbindung zum Startdisplay erfolgreich",
                    status_code=303,
                )
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                set_setting(db, "startdisplay_last_error", error_msg)
                db.commit()
                return RedirectResponse(
                    url=f"/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: {error_msg}",
                    status_code=303,
                )
                
    except httpx.TimeoutException:
        return RedirectResponse(
            url="/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: Timeout",
            status_code=303,
        )
    except httpx.ConnectError:
        return RedirectResponse(
            url="/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: Verbindung fehlgeschlagen",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/ui/settings?error=Verbindung zum Startdisplay fehlgeschlagen: {str(e)}",
            status_code=303,
        )
