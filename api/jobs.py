import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from database import get_db
from models import Job, Company, KeywordProfile, PipelineJob
from synonyms import expand_keywords, SYNONYMS

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
    relevance_score: int = 0
    matched_keywords: List[str] = []


class JobsListResponse(BaseModel):
    profile: Optional[str]
    total: int
    results: List[JobResponse]


def _score(title: str, keywords: List[str], description: Optional[str] = None) -> tuple:
    """Return (0-100 score, matched keywords) scored against title and description.

    Title whole-word match: 2 pts. Title substring: 1 pt.
    Description whole-word: 1 pt. Description substring: 0.5 pt.
    Synonyms are checked alongside each keyword; the original keyword is reported.
    Max score is anchored to title-only (2 × n keywords); score is capped at 100.
    """
    if not keywords:
        return 0, []
    title_lower = title.lower()
    desc_lower = (description or '').lower()
    matched = []
    weighted_score = 0.0
    for kw in keywords:
        kw_lower = kw.lower()
        check_terms = [kw_lower] + SYNONYMS.get(kw_lower, [])
        for term in check_terms:
            in_title = term in title_lower
            in_desc = term in desc_lower
            if not in_title and not in_desc:
                continue
            matched.append(kw)
            if re.search(r'\b' + re.escape(term) + r'\b', title_lower):
                weighted_score += 2
            elif in_title:
                weighted_score += 1
            elif re.search(r'\b' + re.escape(term) + r'\b', desc_lower):
                weighted_score += 1
            else:
                weighted_score += 0.5
            break  # don't double-count multiple synonyms for the same keyword
    max_score = len(keywords) * 2
    pct = min(100, round(weighted_score / max_score * 100)) if max_score else 0
    return pct, matched


@router.get("", response_model=JobsListResponse)
def search_jobs(
    profile_ids: Optional[str] = Query(None),   # comma-separated IDs, e.g. "1,2,3"
    profile_id: Optional[int] = Query(None),     # single-profile compat (dashboard)
    location_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    per_company_limit: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    base_query = (
        db.query(Job, Company.name)
        .join(Company, Job.company_id == Company.id)
        .filter(
            and_(
                Job.is_active == True,
                Company.is_active == True,
                or_(
                    Job.posted_date >= seven_days_ago,
                    and_(Job.posted_date == None, Job.fetched_at >= seven_days_ago),
                ),
            )
        )
    )

    # Resolve selected profile IDs (multi-select takes precedence)
    selected_ids: List[int] = []
    if profile_ids:
        selected_ids = [int(x) for x in profile_ids.split(",") if x.strip().isdigit()]
    elif profile_id:
        selected_ids = [profile_id]

    # Collect unique keywords from all selected profiles (preserve order, dedupe by lowercase)
    all_keywords: List[str] = []
    profile_label: Optional[str] = None
    if selected_ids:
        profiles = db.query(KeywordProfile).filter(KeywordProfile.id.in_(selected_ids)).all()
        seen_lower: set = set()
        for p in profiles:
            for kw in p.get_keywords():
                if kw.lower() not in seen_lower:
                    all_keywords.append(kw)
                    seen_lower.add(kw.lower())
        if len(profiles) == 1:
            profile_label = profiles[0].name
        elif profiles:
            profile_label = f"{len(profiles)} profiles"

        # Filter: must match at least one keyword (+ synonyms) in title or description
        if all_keywords:
            expanded = expand_keywords(all_keywords)
            kw_conds = []
            for kw in expanded:
                kw_conds.append(Job.title.ilike(f"%{kw}%"))
                kw_conds.append(Job.description.ilike(f"%{kw}%"))
            base_query = base_query.filter(or_(*kw_conds))

    if location_type:
        base_query = base_query.filter(Job.location_type == location_type)
    if location:
        base_query = base_query.filter(Job.location.ilike(f"%{location}%"))

    all_rows = base_query.order_by(Job.posted_date.desc()).all()

    # Per-company cap (applied before scoring to keep dashboard fast)
    if per_company_limit:
        company_counts: dict = {}
        capped = []
        for row in all_rows:
            cid = row[0].company_id
            company_counts[cid] = company_counts.get(cid, 0) + 1
            if company_counts[cid] <= per_company_limit:
                capped.append(row)
        rows = capped
    else:
        rows = all_rows

    saved_ids = {pj.job_id for pj in db.query(PipelineJob.job_id).all() if pj.job_id}

    results = []
    for job, company_name in rows:
        days_old = (datetime.utcnow() - job.posted_date).days if job.posted_date else 0
        score, matched = _score(job.title, all_keywords, job.description)
        results.append(JobResponse(
            job_id=job.id,
            company=company_name,
            title=job.title,
            location=job.location,
            location_type=job.location_type,
            salary=job.salary_range,
            url=job.url,
            posted_date=job.posted_date.isoformat() if job.posted_date else None,
            days_old=days_old,
            is_saved=job.id in saved_ids,
            relevance_score=score,
            matched_keywords=matched,
        ))

    # When profiles active: sort by relevance desc, then recency asc as tiebreak
    if all_keywords:
        results.sort(key=lambda j: (-j.relevance_score, j.days_old))

    return JobsListResponse(profile=profile_label, total=len(results), results=results)


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    company = db.query(Company).filter(Company.id == job.company_id).first()
    return job.to_dict(company_name=company.name if company else None)
