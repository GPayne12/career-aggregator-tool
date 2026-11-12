import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from database import SessionLocal
from models import Company, Job, Setting
from scheduler import scrape_all_companies, iter_scrape

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


@router.get("/stream")
def stream_scrape():
    """Stream scrape progress as Server-Sent Events."""
    def event_generator():
        for event in iter_scrape():
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
