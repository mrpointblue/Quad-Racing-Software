from backend.models import RaceRun
from sqlalchemy.orm import Session
import threading
import time
from backend.application.logo_runtime import get_logo_status_payload
from backend.application.runs import get_lane_results_for_run, get_run_repeat_scope, is_lane_editable_for_repeat_scope


LOGO_RUN_DB_SYNC_LOCK = threading.Lock()

LOGO_RUN_DB_SYNC_STATE: dict[int, dict] = {}

LOGO_RUN_DB_SYNC_MIN_INTERVAL_SECONDS = 1.5

def should_sync_logo_run_times(run_id: int, lane_a_ms, lane_b_ms) -> bool:
    now = time.monotonic()
    values = (lane_a_ms, lane_b_ms)

    with LOGO_RUN_DB_SYNC_LOCK:
        state = LOGO_RUN_DB_SYNC_STATE.get(run_id)

        if state:
            if state.get("values") == values:
                return False

            if now - state.get("synced_at", 0) < LOGO_RUN_DB_SYNC_MIN_INTERVAL_SECONDS:
                return False

        LOGO_RUN_DB_SYNC_STATE[run_id] = {
            "values": values,
            "synced_at": now,
        }
        return True

def sync_logo_times_for_run(
    race_run: RaceRun,
    db: Session,
    status: dict | None = None,
) -> dict:
    if status is None:
        status = get_logo_status_payload(db)

    if not (
        status.get("ok")
        and not race_run.confirmed
        and race_run.status in ["running", "started"]
    ):
        return status

    lane_a_result, lane_b_result = get_lane_results_for_run(race_run)
    repeat_scope = get_run_repeat_scope(db, race_run.id)
    times = status.get("times", {})
    lane_a_ms = times.get("lane_a_ms")
    lane_b_ms = times.get("lane_b_ms")
    should_commit = should_sync_logo_run_times(
        run_id=race_run.id,
        lane_a_ms=lane_a_ms,
        lane_b_ms=lane_b_ms,
    )

    if not should_commit:
        return status

    dirty = False

    if (
        is_lane_editable_for_repeat_scope("lane_a", repeat_scope)
        and isinstance(lane_a_ms, int)
        and lane_a_ms > 0
        and lane_a_result
    ):
        lane_a_result.raw_time_ms = lane_a_ms
        race_run.lane_a_time_ms = lane_a_ms
        dirty = True

    if (
        is_lane_editable_for_repeat_scope("lane_b", repeat_scope)
        and isinstance(lane_b_ms, int)
        and lane_b_ms > 0
        and lane_b_result
    ):
        lane_b_result.raw_time_ms = lane_b_ms
        race_run.lane_b_time_ms = lane_b_ms
        dirty = True

    if dirty:
        db.commit()
        db.refresh(race_run)

    return status
