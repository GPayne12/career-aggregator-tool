from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json
from database import SessionLocal, get_db
from posting import fetch_posting_text
from models import PipelineJob, Job, Company

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

# preparing and ready are the application-workflow stages between saving a job
# and applying (set by the portfolio studio's Render tool).
STATUSES = ["saved", "preparing", "ready", "applied", "interviewing", "offer", "rejected"]
MAX_WORK_BYTES = 2_000_000


class PipelineJobResponse(BaseModel):
    id: int
    job_id: Optional[int]
    company: str
    title: str
    location: Optional[str]
    salary: Optional[str]
    url: str
    status: str
    saved_date: str
    applied_date: Optional[str]
    notes: Optional[str]
    posting_text: Optional[str] = None
    posting_state: Optional[str] = None
    # Progress on the application, for list views (the work itself is on the item).
    has_suggestions: bool = False
    has_letter: bool = False
    checklist: Optional[dict] = None


class PipelineSaveRequest(BaseModel):
    job_id: int


class PipelineUpdateRequest(BaseModel):
    status: str
    notes: Optional[str] = None


class PipelineAddRequest(BaseModel):
    title: str
    company: str
    url: str
    status: str = "saved"
    salary: Optional[str] = None
    notes: Optional[str] = None
    posting_text: Optional[str] = None
    # For history imports (e.g. past LinkedIn applications): the real dates, and
    # no posting fetch (the page is gone or behind a login).
    applied_date: Optional[str] = None
    saved_date: Optional[str] = None
    fetch_posting: bool = True


class PostingRequest(BaseModel):
    posting_text: str


class PipelineItemResponse(PipelineJobResponse):
    work: Optional[dict] = None


class WorkRequest(BaseModel):
    work: dict


class ChecklistRequest(BaseModel):
    checklist: dict


class PipelineResponse(BaseModel):
    saved: List[PipelineJobResponse]
    preparing: List[PipelineJobResponse] = []
    ready: List[PipelineJobResponse] = []
    applied: List[PipelineJobResponse]
    rejected: List[PipelineJobResponse]
    interviewing: List[PipelineJobResponse]
    offer: List[PipelineJobResponse]


def get_pipeline_job_response(pipeline_job: PipelineJob, db: Session) -> PipelineJobResponse:
    """Convert PipelineJob to response format"""
    company = db.query(Company).filter(Company.id == pipeline_job.company_id).first()
    return PipelineJobResponse(
        id=pipeline_job.id,
        job_id=pipeline_job.job_id,
        company=company.name if company else "Unknown",
        title=pipeline_job.title,
        location=pipeline_job.location,
        salary=pipeline_job.salary_range,
        url=pipeline_job.url,
        status=pipeline_job.status,
        saved_date=pipeline_job.saved_date.isoformat() if pipeline_job.saved_date else None,
        applied_date=pipeline_job.applied_date.isoformat() if pipeline_job.applied_date else None,
        notes=pipeline_job.notes,
        posting_text=pipeline_job.posting_text,
        posting_state=pipeline_job.posting_state,
        **_progress(pipeline_job),
    )


def _progress(pj: PipelineJob) -> dict:
    try:
        work = json.loads(pj.work_json) if pj.work_json else {}
    except ValueError:
        work = {}
    try:
        checklist = json.loads(pj.checklist_json) if pj.checklist_json else None
    except ValueError:
        checklist = None
    return {
        # Render stores {jobDesc, result: {suggestions: [...]}} per job.
        "has_suggestions": bool(((work.get("suggestions") or {}).get("result") or {}).get("suggestions")),
        "has_letter": bool((work.get("draft") or {}).get("doc")),
        "checklist": checklist,
    }


def _fetch_posting_into(pipeline_job_id: int, url: str):
    """Background task: fetch the posting and store it on the pipeline entry."""
    text, state = fetch_posting_text(url)
    db = SessionLocal()
    try:
        pj = db.query(PipelineJob).filter(PipelineJob.id == pipeline_job_id).first()
        if pj and pj.posting_state == "fetching":
            pj.posting_text = text
            pj.posting_state = state
            db.commit()
    finally:
        db.close()


@router.post("/add", status_code=201)
def add_pipeline_job_manual(request: PipelineAddRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Manually add a job to the pipeline without a search result."""
    valid_statuses = STATUSES
    if request.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")

    # Find or create the company record
    company = db.query(Company).filter(Company.name == request.company).first()
    if not company:
        company = Company(name=request.company, career_url="")
        db.add(company)
        db.flush()

    pipeline_job = PipelineJob(
        job_id=None,
        company_id=company.id,
        title=request.title,
        location=None,
        salary_range=request.salary,
        url=request.url,
        status=request.status,
        notes=request.notes,
        posting_text=(request.posting_text or "").strip() or None,
        posting_state="manual" if (request.posting_text or "").strip() else ("fetching" if request.url and request.fetch_posting else None),
    )
    for field in ("applied_date", "saved_date"):
        value = getattr(request, field)
        if value:
            try:
                setattr(pipeline_job, field, datetime.fromisoformat(value[:19]))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Bad {field}: {value}")
    if request.status == "applied" and not pipeline_job.applied_date:
        pipeline_job.applied_date = datetime.utcnow()
    db.add(pipeline_job)
    db.commit()
    db.refresh(pipeline_job)
    if pipeline_job.posting_state == "fetching":
        background.add_task(_fetch_posting_into, pipeline_job.id, request.url)
    return get_pipeline_job_response(pipeline_job, db)


@router.post("/save", status_code=201)
def save_job(request: PipelineSaveRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Save a job to the pipeline"""
    # Get the job
    job = db.query(Job).filter(Job.id == request.job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check if already saved
    existing = db.query(PipelineJob).filter(
        and_(PipelineJob.job_id == request.job_id, PipelineJob.company_id == job.company_id)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Job already saved to pipeline")

    # Create pipeline job
    pipeline_job = PipelineJob(
        job_id=job.id,
        company_id=job.company_id,
        title=job.title,
        location=job.location,
        salary_range=job.salary_range,
        url=job.url,
        status="saved",
        # The scrape already has the full text for Greenhouse and Lever postings.
        posting_text=job.description if job.description and len(job.description) >= 400 else None,
        posting_state="ready" if job.description and len(job.description) >= 400 else "fetching",
    )
    db.add(pipeline_job)
    db.commit()
    db.refresh(pipeline_job)
    if pipeline_job.posting_state == "fetching":
        background.add_task(_fetch_posting_into, pipeline_job.id, job.url)

    return {"id": pipeline_job.id, "message": "Job saved to pipeline"}


def _item(pj: PipelineJob, db: Session) -> PipelineItemResponse:
    base = get_pipeline_job_response(pj, db).dict()
    load = lambda s: json.loads(s) if s else None  # noqa: E731
    return PipelineItemResponse(**base, work=load(pj.work_json))


def _get(pipeline_job_id: int, db: Session) -> PipelineJob:
    pj = db.query(PipelineJob).filter(PipelineJob.id == pipeline_job_id).first()
    if not pj:
        raise HTTPException(status_code=404, detail="Pipeline job not found")
    return pj


@router.get("/item/{pipeline_job_id}", response_model=PipelineItemResponse)
def get_pipeline_item(pipeline_job_id: int, db: Session = Depends(get_db)):
    """One pipeline entry with its posting, work and checklist (for the studio's Render tool)."""
    return _item(_get(pipeline_job_id, db), db)


@router.put("/item/{pipeline_job_id}/work", response_model=PipelineItemResponse)
def set_work(pipeline_job_id: int, request: WorkRequest, db: Session = Depends(get_db)):
    """Store Render's work on this application. The first save moves a saved job to preparing."""
    pj = _get(pipeline_job_id, db)
    data = json.dumps(request.work)
    if len(data) > MAX_WORK_BYTES:
        raise HTTPException(status_code=413, detail="That draft is too large to keep.")
    pj.work_json = data
    if pj.status == "saved":
        pj.status = "preparing"
        pj.stage_changed_date = datetime.utcnow()
    db.commit()
    db.refresh(pj)
    return _item(pj, db)


@router.put("/item/{pipeline_job_id}/checklist", response_model=PipelineItemResponse)
def set_checklist(pipeline_job_id: int, request: ChecklistRequest, db: Session = Depends(get_db)):
    """Store the application checklist (resume updated, letter exported, …)."""
    pj = _get(pipeline_job_id, db)
    pj.checklist_json = json.dumps(request.checklist)
    db.commit()
    db.refresh(pj)
    return _item(pj, db)


@router.put("/item/{pipeline_job_id}/posting", response_model=PipelineJobResponse)
def set_posting(pipeline_job_id: int, request: PostingRequest, db: Session = Depends(get_db)):
    """Store a pasted posting (when the fetch failed, or for a manual entry)."""
    pj = db.query(PipelineJob).filter(PipelineJob.id == pipeline_job_id).first()
    if not pj:
        raise HTTPException(status_code=404, detail="Pipeline job not found")
    pj.posting_text = request.posting_text.strip() or None
    pj.posting_state = "manual" if pj.posting_text else None
    db.commit()
    db.refresh(pj)
    return get_pipeline_job_response(pj, db)


@router.get("", response_model=PipelineResponse)
def get_pipeline(db: Session = Depends(get_db)):
    """Get all pipeline jobs grouped by status"""
    statuses = STATUSES
    result = {}

    for status in statuses:
        jobs = db.query(PipelineJob).filter(PipelineJob.status == status).all()
        result[status] = [get_pipeline_job_response(job, db) for job in jobs]

    return PipelineResponse(**result)


@router.get("/{status}", response_model=List[PipelineJobResponse])
def get_pipeline_by_status(status: str, db: Session = Depends(get_db)):
    """Get pipeline jobs by status"""
    valid_statuses = STATUSES
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    jobs = db.query(PipelineJob).filter(PipelineJob.status == status).all()
    return [get_pipeline_job_response(job, db) for job in jobs]


@router.put("/{pipeline_job_id}", response_model=PipelineJobResponse)
def update_pipeline_job(
    pipeline_job_id: int,
    request: PipelineUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update a pipeline job status and notes"""
    pipeline_job = db.query(PipelineJob).filter(
        PipelineJob.id == pipeline_job_id
    ).first()
    if not pipeline_job:
        raise HTTPException(status_code=404, detail="Pipeline job not found")

    valid_statuses = STATUSES
    if request.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")

    pipeline_job.status = request.status
    if request.notes is not None:
        pipeline_job.notes = request.notes
    if request.status == "applied":
        pipeline_job.applied_date = datetime.utcnow()
    pipeline_job.stage_changed_date = datetime.utcnow()

    db.commit()
    db.refresh(pipeline_job)

    return get_pipeline_job_response(pipeline_job, db)


@router.delete("/{pipeline_job_id}", status_code=204)
def delete_pipeline_job(pipeline_job_id: int, db: Session = Depends(get_db)):
    """Remove a job from the pipeline"""
    pipeline_job = db.query(PipelineJob).filter(
        PipelineJob.id == pipeline_job_id
    ).first()
    if not pipeline_job:
        raise HTTPException(status_code=404, detail="Pipeline job not found")

    db.delete(pipeline_job)
    db.commit()
