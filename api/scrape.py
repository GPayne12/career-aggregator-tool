import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from database import SessionLocal
from models import Company, Job, Setting
import time
from scheduler import scrape_all_companies, survey_status, start_survey, CANCEL, EVENTS, STATE

router = APIRouter(prefix="/api/scrape", tags=["scrape"])


@router.post("")
def run_scrape():
    """Manually trigger a scrape of all active companies."""
    try:
        return scrape_all_companies()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
def scrape_summary():
    """Verified companies (surveyed), held ones (waiting), and when the last survey ran."""
    db = SessionLocal()
    try:
        live = db.query(Company).filter(Company.is_active == True, Company.is_skipped == False)
        verified = live.filter(Company.url_status == 'verified').count()
        last = db.query(Setting).filter(Setting.key == "last_survey").first()
        last_run = last.value if last else None
        if not last_run:
            seen = db.query(func.max(Job.fetched_at)).scalar()
            last_run = seen.isoformat() if seen else None
        return {"verified": verified, "held": live.count() - verified, "last_run": last_run}
    finally:
        db.close()


@router.get("/status")
def scrape_status():
    """Is a survey running, and what is it reading now? (For a page opened mid-survey.)"""
    return survey_status()


@router.post("/stop")
def scrape_stop():
    """Stop after the company being read; what was read is kept."""
    CANCEL.set()
    return {"stopping": bool(survey_status().get("running"))}


@router.get("/stream")
def stream_scrape():
    """Start a survey and stream its progress as Server-Sent Events. The survey runs
    in the background: if this stream closes, the survey carries on (follow it with
    /status). A survey already running answers "busy"."""
    def event_generator():
        if not start_survey():
            yield f"data: {json.dumps({'type': 'busy', **survey_status()})}\n\n"
            return
        i = 0
        while True:
            while i < len(EVENTS):
                event = EVENTS[i]; i += 1
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "stopped", "error"):
                    return
            if not STATE.get("running"):
                return
            time.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
