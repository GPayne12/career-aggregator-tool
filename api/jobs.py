from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from database import get_db
from models import Job, Company, KeywordProfile, PipelineJob

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    job_id: int
    company: str
    title: str
    location: Optional[str]
    location_type: Optional[str]
    salary: Optional[str]
    url: str
    posted_date: Optional[str]
    days_old: int
    is_saved: bool = False


class JobsListResponse(BaseModel):
    profile: Optional[str]
    total: int
    results: List[JobResponse]


@router.get("", response_model=JobsListResponse)
def search_jobs(
    profile_id: Optional[int] = Query(None),
    location_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Search jobs by keyword profile and filters"""

    # Build base query: only active jobs from last 14 days
    fourteen_days_ago = datetime.utcnow() - timedelta(days=14)
    base_query = (
        db.query(Job, Company.name)
        .join(Company, Job.company_id == Company.id)
        .filter(
            and_(
                Job.is_active == True,
                Company.is_active == True,
                or_(
                    Job.posted_date.is_(None),
                    Job.posted_date >= fourteen_days_ago,
                ),
            )
        )
    )

    # Apply keyword filter if profile specified
    if profile_id:
        profile = db.query(KeywordProfile).filter(
            KeywordProfile.id == profile_id
        ).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        keywords = profile.get_keywords()
        if keywords:
            keyword_filters = [
                Job.title.ilike(f"%{kw}%") for kw in keywords
            ]
            base_query = base_query.filter(or_(*keyword_filters))

    # Apply location type filter
    if location_type:
        base_query = base_query.filter(Job.location_type == location_type)

    # Apply location text filter
    if location:
        base_query = base_query.filter(Job.location.ilike(f"%{location}%"))

    # Order by posted date descending
    jobs = base_query.order_by(Job.posted_date.desc()).all()

    profile_name = None
    if profile_id:
        profile = db.query(KeywordProfile).filter(
            KeywordProfile.id == profile_id
        ).first()
        if profile:
            profile_name = profile.name

    results = []
    saved_job_ids = {pj.job_id for pj in db.query(PipelineJob.job_id).all() if pj.job_id}

    for job, company_name in jobs:
        days_old = 0
        if job.posted_date:
            days_old = (datetime.utcnow() - job.posted_date).days

        results.append(
            JobResponse(
                job_id=job.id,
                company=company_name,
                title=job.title,
                location=job.location,
                location_type=job.location_type,
                salary=job.salary_range,
                url=job.url,
                posted_date=job.posted_date.isoformat() if job.posted_date else None,
                days_old=days_old,
                is_saved=job.id in saved_job_ids,
            )
        )

    return JobsListResponse(
        profile=profile_name,
        total=len(results),
        results=results,
    )


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    """Get a specific job"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    company = db.query(Company).filter(Company.id == job.company_id).first()
    return job.to_dict(company_name=company.name if company else None)
