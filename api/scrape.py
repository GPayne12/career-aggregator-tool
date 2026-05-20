from fastapi import APIRouter, HTTPException
from scheduler import scrape_all_companies

router = APIRouter(prefix="/api/scrape", tags=["scrape"])


@router.post("")
def run_scrape():
    """Manually trigger a scrape of all active companies."""
    try:
        result = scrape_all_companies()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
