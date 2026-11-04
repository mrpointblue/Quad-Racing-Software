from backend.models import RaceRun
from backend.database import SessionLocal
from datetime import datetime
from backend.application.runs import get_lane_results_for_run
from backend.application.settings_service import get_setting, set_setting


def send_start_display_numbers(lane_a_number: int | None, lane_b_number: int | None) -> bool:
    """Send start numbers to ESP32 display"""
    db = SessionLocal()
    try:
        # Get settings
        enabled = get_setting(db, "startdisplay_enabled", "false")
        if enabled != "true":
            return True  # Not an error, just not enabled
        
        host = get_setting(db, "startdisplay_host", "").strip()
        if not host:
            return True  # Not configured
        
        port = get_setting(db, "startdisplay_port", "80").strip()
        endpoint = get_setting(db, "startdisplay_endpoint", "/display").strip()
        
        # Build URL
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        
        url = f"http://{host}:{port}{endpoint}"
        
        payload = {
            "mode": "start_numbers" if lane_a_number is not None or lane_b_number is not None else "idle",
            "lane_a_start_number": lane_a_number,
            "lane_b_start_number": lane_b_number,
        }
        
        # Save status
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_setting(db, "startdisplay_last_send", now)
        set_setting(db, "startdisplay_last_lane_a", str(lane_a_number) if lane_a_number is not None else "")
        set_setting(db, "startdisplay_last_lane_b", str(lane_b_number) if lane_b_number is not None else "")
        # Send request
        import httpx
        import json
        headers = {"Content-Type": "application/json"}
        data = json.dumps(payload)
        
        with httpx.Client(timeout=1.0) as client:
            response = client.post(url, data=data, headers=headers)
            
            if response.status_code == 200:
                set_setting(db, "startdisplay_last_success", now)
                set_setting(db, "startdisplay_last_error", "")
                set_setting(db, "startdisplay_last_payload", f"A:{lane_a_number if lane_a_number is not None else '--'} B:{lane_b_number if lane_b_number is not None else '--'}")
                return True
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                set_setting(db, "startdisplay_last_error", error_msg)
                return False
                
    except httpx.TimeoutException:
        error_msg = "Timeout after 1s"
        set_setting(db, "startdisplay_last_error", error_msg)
        set_setting(db, "startdisplay_last_payload", f"A:{lane_a_number if lane_a_number is not None else '--'} B:{lane_b_number if lane_b_number is not None else '--'}")
        return False
    except httpx.ConnectError:
        error_msg = "Connection failed"
        set_setting(db, "startdisplay_last_error", error_msg)
        set_setting(db, "startdisplay_last_payload", f"A:{lane_a_number if lane_a_number is not None else '--'} B:{lane_b_number if lane_b_number is not None else '--'}")
        return False
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        set_setting(db, "startdisplay_last_error", error_msg)
        return False
    finally:
        db.commit()
        db.close()

def send_start_display_for_run(event_id: int, run_id: int) -> bool:
    """Send start numbers of a specific run to the display"""
    db = SessionLocal()
    try:
        race_run = db.query(RaceRun).filter(
            RaceRun.event_id == event_id,
            RaceRun.id == run_id,
        ).first()

        if not race_run:
            return False

        lane_a_result, lane_b_result = get_lane_results_for_run(race_run)

        lane_a_number = None
        lane_b_number = None

        if lane_a_result and lane_a_result.participant and not lane_a_result.participant.withdrawn:
            lane_a_number = lane_a_result.participant.start_number

        if lane_b_result and lane_b_result.participant and not lane_b_result.participant.withdrawn:
            lane_b_number = lane_b_result.participant.start_number

        return send_start_display_numbers(lane_a_number, lane_b_number)
    finally:
        db.close()
