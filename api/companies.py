import csv
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import requests as http_requests
from database import get_db, SessionLocal
from models import Company, Discovery, Job, KeywordProfile
from profile_terms import facet_terms, has_term
from scraper import JobScraper
from discover import board_jobs, boards_on_page, company_key
from api.discovery import run_many

_CHECK_TIMEOUT = 6   # seconds per URL
_CHECK_WORKERS = 8   # concurrent checks

# url_status values. Only 'verified' companies are scraped.
STATUSES = ("verified", "found", "page", "website", "none", "unverified")

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str
    career_url: Optional[str] = None
    website: Optional[str] = None
    url_status: Optional[str] = None
    url_source: Optional[str] = None
    score_m: Optional[int] = None    # starting motivation, e.g. from LinkedIn activity


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    career_url: Optional[str] = None
    website: Optional[str] = None
    notes: Optional[str] = None
    score_a: Optional[int] = None
    score_m: Optional[int] = None


class CheckBody(BaseModel):
    career_url: Optional[str] = None


class VerifyBody(BaseModel):
    verified: bool = True


class CompanyResponse(BaseModel):
    id: int
    name: str
    career_url: str
    website: Optional[str] = None
    url_status: Optional[str] = None
    url_source: Optional[str] = None
    verified_at: Optional[str] = None
    check: Optional[Dict[str, Any]] = None
    suggested: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    is_active: bool
    is_skipped: bool = False
    score_a: Optional[int] = None
    score_m: Optional[int] = None
    score_p: Optional[int] = None


def _suggestion(c: Company, found: Optional[Dict]) -> Optional[Dict]:
    """What discovery found, when it would change something: a board or page
    different from the current career URL, or a website where there is none."""
    if not found:
        return None
    differs = found.get("career_url") and found["career_url"].rstrip("/") != (c.career_url or "").rstrip("/")
    adds_site = found.get("website") and not c.website
    if not (differs or adds_site):
        return None
    keep = ("career_url", "platform", "jobs", "board_name", "status", "evidence",
            "website", "website_name", "website_agrees", "checked_at")
    return {k: found.get(k) for k in keep}


def _to_response(c: Company, score_p_override: Optional[int] = None,
                 found: Optional[Dict] = None) -> CompanyResponse:
    try:
        check = json.loads(c.check_json) if c.check_json else None
    except ValueError:
        check = None
    return CompanyResponse(
        id=c.id, name=c.name, career_url=c.career_url or "",
        website=c.website, url_status=c.url_status, url_source=c.url_source,
        verified_at=c.verified_at.isoformat() if c.verified_at else None,
        check=check, suggested=_suggestion(c, found),
        notes=c.notes, is_active=c.is_active, is_skipped=bool(c.is_skipped),
        score_a=c.score_a, score_m=c.score_m,
        score_p=score_p_override if score_p_override is not None else c.score_p,
    )


def _discoveries(db: Session) -> Dict[str, Dict]:
    return {r.key: json.loads(r.result_json) for r in db.query(Discovery).all()}


def _compute_p_scores(db: Session) -> dict:
    """Return {company_id: p_score} capped at 3.

    A job counts if its title contains at least one keyword from any active profile,
    matching the same OR logic used by the search page.
    """
    profiles = db.query(KeywordProfile).all()
    keywords = list({kw.lower() for kw in facet_terms(db, "want")})
    if not keywords:
        return {}

    cutoff = datetime.utcnow() - timedelta(days=14)
    active_jobs = db.query(Job.company_id, Job.title).filter(
        Job.is_active == True,
        Job.fetched_at >= cutoff,
    ).all()

    counts: dict = {}
    for company_id, title in active_jobs:
        title_lower = title.lower()
        if any(has_term(title_lower, kw) for kw in keywords):
            counts[company_id] = counts.get(company_id, 0) + 1

    return {cid: min(3, n) for cid, n in counts.items()}


def _apply_discovery(c: Company, found: Dict) -> None:
    """Fill in what discovery found, without overriding anything George set.
    A verified company is left alone; a typed-in URL is kept (the finding
    shows as a suggestion instead)."""
    if found.get("website") and not c.website:
        c.website = found["website"]
    if c.url_status == "verified":
        return
    if not c.career_url and found.get("career_url"):
        c.career_url = found["career_url"]
        c.url_status = found["status"]
        c.url_source = "discovery"
    elif not c.career_url:
        c.url_status = found["status"]   # website or none
        c.url_source = "discovery"


# ── Fixed-path routes first (must come before /{company_id}) ─────────────────

@router.get("", response_model=List[CompanyResponse])
def list_companies(db: Session = Depends(get_db)):
    companies = db.query(Company).filter(Company.is_active == True).all()
    p_scores = _compute_p_scores(db)
    found = _discoveries(db)
    return [_to_response(c, p_scores.get(c.id, 0), found.get(company_key(c.name))) for c in companies]


@router.get("/field")
def company_field(db: Session = Depends(get_db)):
    """Everything the Overview's company field draws, in one call: each active
    company with its interest (motivation, advocacy, postings), its open and profile-matched postings,
    and its applications. The page decides the band and size from these."""
    from models import PipelineJob
    now = datetime.utcnow()
    week, fresh = now - timedelta(days=7), now - timedelta(days=3)
    profiles = db.query(KeywordProfile).all()
    keywords = list({kw.lower() for kw in facet_terms(db, "want")})

    open_jobs: Dict[int, int] = {}
    matched: Dict[int, int] = {}
    fresh_matched: Dict[int, int] = {}
    for cid, title, fetched in db.query(Job.company_id, Job.title, Job.fetched_at).filter(
            Job.is_active == True, Job.fetched_at >= week).all():
        open_jobs[cid] = open_jobs.get(cid, 0) + 1
        if keywords and any(has_term(title, k) for k in keywords):
            matched[cid] = matched.get(cid, 0) + 1
            if fetched and fetched >= fresh:
                fresh_matched[cid] = fresh_matched.get(cid, 0) + 1

    apps: Dict[int, List[Dict]] = {}
    for p in db.query(PipelineJob).order_by(PipelineJob.stage_changed_date.desc()).all():
        apps.setdefault(p.company_id, []).append({
            "id": p.id, "title": p.title, "url": p.url, "status": p.status,
            "applied_date": p.applied_date.isoformat() if p.applied_date else None,
            "changed": p.stage_changed_date.isoformat() if p.stage_changed_date else None,
        })

    out = []
    for c in db.query(Company).filter(Company.is_active == True).all():
        out.append({
            "id": c.id, "name": c.name, "website": c.website, "career_url": c.career_url or "",
            "url_status": c.url_status, "is_skipped": bool(c.is_skipped),
            "score_a": c.score_a, "score_m": c.score_m, "score_p": min(3, matched.get(c.id, 0)),
            "open_jobs": open_jobs.get(c.id, 0), "matched_jobs": matched.get(c.id, 0),
            "fresh_matched": fresh_matched.get(c.id, 0),
            "applications": apps.get(c.id, []),
        })
    return {"companies": out, "profiles": len(profiles), "now": now.isoformat(timespec="seconds")}


@router.get("/all", response_model=List[CompanyResponse])
def list_all_companies(db: Session = Depends(get_db)):
    companies = db.query(Company).all()
    p_scores = _compute_p_scores(db)
    return [_to_response(c, p_scores.get(c.id, 0)) for c in companies]


@router.get("/discover-urls")
def discover_missing_urls(refresh: bool = False):
    """SSE stream: look up the website and job board for every active company
    that isn't verified yet. Fills empty fields; never overrides a verified
    page or a URL George typed (those get a suggestion instead)."""
    def generate():
        db = SessionLocal()
        try:
            companies = db.query(Company).filter(
                Company.is_active == True, Company.is_skipped == False,
                (Company.url_status != "verified") | (Company.url_status == None),
            ).all()
            total = len(companies)
            yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"
            found_count = 0
            batch = 6
            for i in range(0, total, batch):
                chunk = companies[i:i + batch]
                results = run_many(db, [c.name for c in chunk], refresh=refresh)
                for j, c in enumerate(chunk, i + 1):
                    r = results.get(company_key(c.name))
                    if r:
                        _apply_discovery(c, r)
                        found_count += r["status"] == "found"
                    db.commit()
                    yield f"data: {json.dumps({'type': 'progress', 'index': j, 'total': total, 'company': c.name, 'status': (r or {}).get('status'), 'url': (r or {}).get('career_url')})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'found': found_count, 'total': total})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            db.close()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Bulk-import companies from a CSV with a 'name' column (career_url optional)."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))

    if 'name' not in (reader.fieldnames or []):
        raise HTTPException(status_code=400, detail="CSV must contain a 'name' column")

    added, skipped, errors = 0, 0, []

    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i}: missing name")
            continue

        if db.query(Company).filter(Company.name == name).first():
            skipped += 1
            continue

        career_url = (row.get("career_url") or "").strip()
        db.add(Company(name=name, career_url=career_url,
                       url_status="unverified" if career_url else None,
                       url_source="manual" if career_url else None))
        added += 1

    db.commit()
    return {"added": added, "skipped": skipped, "errors": errors}


# ── URL health check ─────────────────────────────────────────────────────────

def _check_one(company_id: int, url: str) -> dict:
    """HEAD then GET fallback; returns {id, ok, status_code}.

    Green (ok=True):  any HTTP response except 404/410 — a 403 means the
                      server exists and blocked a bot; the URL is still valid.
    Red  (ok=False):  404, 410 (page genuinely gone), or no response at all.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CareerAggregator/1.0)"}
    try:
        r = http_requests.head(url, timeout=_CHECK_TIMEOUT, allow_redirects=True,
                               headers=headers)
        # Some servers reject HEAD — fall back to streaming GET
        if r.status_code in (405, 400):
            r = http_requests.get(url, timeout=_CHECK_TIMEOUT, allow_redirects=True,
                                  headers=headers, stream=True)
            r.close()
        ok = r.status_code not in (404, 410)
        return {"id": company_id, "ok": ok, "status_code": r.status_code}
    except Exception as exc:
        return {"id": company_id, "ok": False, "status_code": None, "error": str(exc)[:120]}


@router.get("/url-check")
def url_check(ids: str = Query(..., description="Comma-separated company IDs"),
              db: Session = Depends(get_db)):
    """Check whether each company's career URL is reachable. Returns a list of results."""
    try:
        id_list = [int(i) for i in ids.split(",") if i.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be comma-separated integers")

    companies = db.query(Company).filter(Company.id.in_(id_list)).all()
    to_check = [(c.id, c.career_url) for c in companies if c.career_url]
    no_url   = [c.id for c in companies if not c.career_url]

    results = [{"id": cid, "ok": None, "status_code": None} for cid in no_url]

    with ThreadPoolExecutor(max_workers=_CHECK_WORKERS) as pool:
        futures = {pool.submit(_check_one, cid, url): cid for cid, url in to_check}
        for future in as_completed(futures):
            results.append(future.result())

    return results


# ── Param routes after all fixed paths ───────────────────────────────────────

def _company(db: Session, company_id: int) -> Company:
    c = db.query(Company).filter(Company.id == company_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    return c


@router.post("/{company_id}/check")
def check_company(company_id: int, body: CheckBody, db: Session = Depends(get_db)):
    """A trial scrape: what Search would collect from this career page, without
    saving any jobs. Reads only the first pages so it answers in seconds."""
    c = _company(db, company_id)
    url = (body.career_url or c.career_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="No career page to check")
    scraper = JobScraper(rate_limit_seconds=0.3, max_pages=2, page_timeout_ms=25000)
    t0 = time.time()
    error = None
    jobs: List[Dict] = []
    try:
        jobs = scraper.scrape_career_page(url)
    except Exception as e:
        error = str(e)[:200]
    finally:
        scraper.close()
    platform = scraper.detect_platform(url)
    board_url = url if platform != "generic" else None
    # A careers page that hands off to a job board: name the board, so George
    # can point the company straight at it.
    if platform == "generic" and jobs:
        linked = boards_on_page(" ".join(j.get("url") or "" for j in jobs[:10]))
        if linked:
            platform, board_url = linked[0]
    total = len(jobs)
    if board_url and platform in ("workday", "greenhouse", "lever", "ashby", "smartrecruiters"):
        n, _ = board_jobs(platform, board_url)
        if n is not None:
            total = max(total, n)
    result = {
        "url": url, "platform": platform, "board_url": board_url,
        "via_page": board_url is not None and board_url.rstrip("/") != url.rstrip("/"),
        "found": total, "read": len(jobs),
        "samples": [{"title": j.get("title"), "url": j.get("url"), "location": j.get("location")} for j in jobs[:3]],
        "seconds": round(time.time() - t0, 1), "error": error,
        "at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    # Kept with the company only when it checked the page the company has.
    if url.rstrip("/") == (c.career_url or "").rstrip("/"):
        c.check_json = json.dumps(result)
        db.commit()
    return result


@router.post("/{company_id}/verify", response_model=CompanyResponse)
def verify_company(company_id: int, body: VerifyBody, db: Session = Depends(get_db)):
    """George confirms (or withdraws) the career page. Only verified pages are scraped."""
    c = _company(db, company_id)
    if body.verified:
        if not c.career_url:
            raise HTTPException(status_code=400, detail="Add a career page before verifying")
        c.url_status, c.verified_at = "verified", datetime.utcnow()
    else:
        c.url_status, c.verified_at = "unverified", None
    db.commit()
    db.refresh(c)
    return _to_response(c)


@router.post("/{company_id}/discover", response_model=CompanyResponse)
def rediscover_company(company_id: int, db: Session = Depends(get_db)):
    """Look this company up again (fresh, not cached)."""
    c = _company(db, company_id)
    found = run_many(db, [c.name], refresh=True).get(company_key(c.name))
    if found:
        _apply_discovery(c, found)
        db.commit()
        db.refresh(c)
    return _to_response(c, found=found)


@router.post("/{company_id}/skip", response_model=CompanyResponse)
def toggle_company_skip(company_id: int, db: Session = Depends(get_db)):
    """Toggle the is_skipped flag for a company (skipped companies are excluded from scraping)."""
    db_company = _company(db, company_id)
    db_company.is_skipped = not bool(db_company.is_skipped)
    db.commit()
    db.refresh(db_company)
    return _to_response(db_company)


@router.get("/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int, db: Session = Depends(get_db)):
    c = _company(db, company_id)
    found = {r.key: json.loads(r.result_json) for r in
             db.query(Discovery).filter(Discovery.key == company_key(c.name)).all()}
    return _to_response(c, found=found.get(company_key(c.name)))


@router.post("", response_model=CompanyResponse, status_code=201)
def create_company(company: CompanyCreate, db: Session = Depends(get_db)):
    existing = db.query(Company).filter(Company.name == company.name).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Company '{company.name}' already exists")
    if company.url_status is not None and company.url_status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"url_status must be one of {', '.join(STATUSES)}")
    if company.url_status == "verified":
        raise HTTPException(status_code=400, detail="Verify a company after adding it")
    if company.score_m is not None and not (0 <= company.score_m <= 3):
        raise HTTPException(status_code=400, detail="score_m must be between 0 and 3")

    career_url = (company.career_url or "").strip()
    url_status = company.url_status or ("unverified" if career_url else None)
    db_company = Company(name=company.name, career_url=career_url,
                         website=(company.website or "").strip() or None,
                         url_status=url_status,
                         url_source=company.url_source or ("manual" if career_url else None),
                         score_m=company.score_m)
    db.add(db_company)
    db.commit()
    db.refresh(db_company)
    return _to_response(db_company)


@router.put("/{company_id}", response_model=CompanyResponse)
def update_company(company_id: int, company: CompanyUpdate, db: Session = Depends(get_db)):
    db_company = _company(db, company_id)

    if company.name is not None:
        existing = (db.query(Company)
                    .filter(Company.name == company.name, Company.id != company_id)
                    .first())
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"Company '{company.name}' already exists")
        db_company.name = company.name

    for field, val in [('score_a', company.score_a), ('score_m', company.score_m)]:
        if val is not None and not (0 <= val <= 3):
            raise HTTPException(status_code=400, detail=f"{field} must be between 0 and 3")

    if company.career_url is not None:
        new_url = company.career_url.strip()
        if new_url.rstrip("/") != (db_company.career_url or "").rstrip("/"):
            # A changed career page has to be verified again before it's scraped.
            db_company.career_url = new_url
            db_company.url_status = "unverified" if new_url else None
            db_company.url_source = "manual"
            db_company.verified_at = None
            db_company.check_json = None
    if company.website is not None:
        db_company.website = company.website.strip() or None
    if company.notes is not None:
        db_company.notes = company.notes
    if company.score_a is not None:
        db_company.score_a = company.score_a
    if company.score_m is not None:
        db_company.score_m = company.score_m

    db.commit()
    db.refresh(db_company)
    found = {r.key: json.loads(r.result_json) for r in
             db.query(Discovery).filter(Discovery.key == company_key(db_company.name)).all()}
    return _to_response(db_company, found=found.get(company_key(db_company.name)))


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: int, db: Session = Depends(get_db)):
    db_company = _company(db, company_id)
    db_company.is_active = False
    db.commit()
