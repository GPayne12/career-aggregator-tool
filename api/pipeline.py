from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from database import get_db
from models import PipelineJob, Job, Company

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


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


class PipelineResponse(BaseModel):
    saved: List[PipelineJobResponse]
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
    )


@router.post("/add", status_code=201)
def add_pipeline_job_manual(request: PipelineAddRequest, db: Session = Depends(get_db)):
    """Manually add a job to the pipeline without a search result."""
    valid_statuses = ["saved", "applied", "rejected", "interviewing", "offer"]
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
    )
    db.add(pipeline_job)
    db.commit()
    db.refresh(pipeline_job)
    return get_pipeline_job_response(pipeline_job, db)


@router.post("/save", status_code=201)
def save_job(request: PipelineSaveRequest, db: Session = Depends(get_db)):
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
    )
    db.add(pipeline_job)
    db.commit()
    db.refresh(pipeline_job)

    return {"id": pipeline_job.id, "message": "Job saved to pipeline"}


@router.get("", response_model=PipelineResponse)
def get_pipeline(db: Session = Depends(get_db)):
    """Get all pipeline jobs grouped by status"""
    statuses = ["saved", "applied", "rejected", "interviewing", "offer"]
    result = {}

    for status in statuses:
        jobs = db.query(PipelineJob).filter(PipelineJob.status == status).all()
        result[status] = [get_pipeline_job_response(job, db) for job in jobs]

    return PipelineResponse(**result)


@router.get("/{status}", response_model=List[PipelineJobResponse])
def get_pipeline_by_status(status: str, db: Session = Depends(get_db)):
    """Get pipeline jobs by status"""
    valid_statuses = ["saved", "applied", "rejected", "interviewing", "offer"]
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

    valid_statuses = ["saved", "applied", "rejected", "interviewing", "offer"]
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
