import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from scheduler import scrape_all_companies, iter_scrape

router = APIRouter(prefix="/api/scrape", tags=["scrape"])


@router.post("")
def run_scrape():
    """Manually trigger a scrape of all active companies."""
    try:
        return scrape_all_companies()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
