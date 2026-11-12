import re
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from database import get_db
from models import Job, Company, KeywordProfile, PipelineJob, Setting
from profile_terms import (facet_terms, has_term, is_engineering, WINDOW_DAYS, ADJACENT, NOT_ADJACENT,
                           adjacent_title, craft_in, sales_only, CORE_TITLE, LIGHT, light_role, bridge_role)
from regions import keep as keep_region
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
    # Which track its title matched (engineering / learning), and where it is in the pipeline.
    track: Optional[str] = None
    pipeline_status: Optional[str] = None
    applied_date: Optional[str] = None


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
    matched = []
    weighted_score = 0.0
    for kw in keywords:
        kw_lower = kw.lower()
        for term in [kw_lower] + SYNONYMS.get(kw_lower, []):
            # Whole words only: a term inside another word ("ai" in "email") doesn't count.
            if has_term(title, term):
                weighted_score += 2
            elif has_term(description, term):
                weighted_score += 1
            else:
                continue
            matched.append(kw)
            break  # don't double-count multiple synonyms for the same keyword
    max_score = len(keywords) * 2
    pct = min(100, round(weighted_score / max_score * 100)) if max_score else 0
    return pct, matched


def _fit(job, via: Optional[str], title_terms: List[str], can: List[str], want: List[str]) -> tuple:
    """How well a posting fits, 0–100, and why. Honest rather than flattering:
      title,   up to 50: a core learning-design title 50; another of your role terms 35;
                         an engineering-light, customer-experience, AI-hybrid,
                         workforce or design role 25;
                         a learning-adjacent title word 20; in by its description only 10
      details, up to 50: 5 for each detail from your profile the description names
                         (craft, your skills, your role terms), up to 10 of them.
    A posting with no description to read can't score high: there's little to go on."""
    if via == "title":
        title_part = 50 if CORE_TITLE.search(job.title or "") else 35
    elif via == "adjacent":
        title_part = 50 if CORE_TITLE.search(job.title or "") else 20
    elif via in ("light", "bridge"):
        title_part = 25
    else:
        title_part = 10 if via == "text" else 0
    desc = job.description or ""
    details = list(dict.fromkeys(craft_in(desc) + [k for k in can if has_term(desc, k)] + [k for k in want if has_term(desc, k)]))
    return min(100, title_part + min(50, 5 * len(details))), list(dict.fromkeys(title_terms + details))[:8]


def _profile_score(job, want: List[str], can: List[str]) -> tuple:
    """What you want leads (the usual title-weighted score); what you can do adds
    the share of your skills the posting asks for (up to 8 count as all of them)."""
    w, w_matched = _score(job.title, want, None) if want else (0, [])   # what you want: titles only
    if not can:
        return w, w_matched
    text = f"{job.title} {job.description or ''}".lower()
    c_matched = [kw for kw in can if has_term(text, kw)]
    c = min(100, round(len(c_matched) / min(len(can), 8) * 100))
    score = round(.7 * w + .3 * c) if want else c
    return score, w_matched + [k for k in c_matched if k not in w_matched]


@router.get("", response_model=JobsListResponse)
def search_jobs(
    profile_ids: Optional[str] = Query(None),   # comma-separated IDs, e.g. "1,2,3"
    profile_id: Optional[int] = Query(None),     # single-profile compat (dashboard)
    location_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    region: Optional[str] = Query(None),       # na (continental North America), corridor (DC to Boston, plus remote), any
    per_company_limit: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    seven_days_ago = datetime.utcnow() - timedelta(days=WINDOW_DAYS)   # the window (7 days)

    _setting = db.query(Setting).filter(Setting.key == "exclude_terms").first()
    exclude_terms = json.loads(_setting.value) if _setting else []

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

    # Selected facets: want ones by track (they read titles), can ones (they read the text).
    selected_ids: List[int] = []
    if profile_ids:
        selected_ids = [int(x) for x in profile_ids.split(",") if x.strip().isdigit()]
    elif profile_id:
        selected_ids = [profile_id]
    want_facets: List[tuple] = []
    can_keywords: List[str] = []
    profile_label: Optional[str] = None
    if selected_ids:
        profiles = db.query(KeywordProfile).filter(KeywordProfile.id.in_(selected_ids)).order_by(KeywordProfile.position, KeywordProfile.id).all()
        # Paused want facets (no track) aren't searched.
        want_facets = [(p.track, p.get_keywords()) for p in profiles if p.facet_side == "want" and p.track]
        can_keywords = facet_terms(db, "can", selected_ids)
        if len(profiles) == 1:
            profile_label = profiles[0].name
        elif profiles:
            profile_label = f"{len(profiles)} profiles"
    want_keywords = list(dict.fromkeys(k for _, ks in want_facets for k in ks))

    if location_type:
        base_query = base_query.filter(Job.location_type == location_type)
    if location:
        base_query = base_query.filter(Job.location.ilike(f"%{location}%"))

    all_rows = base_query.order_by(Job.posted_date.desc()).all()
    pipeline = {pj.job_id: pj for pj in db.query(PipelineJob).filter(PipelineJob.job_id != None).all()}

    def title_hits(title: str, keywords: List[str]) -> int:
        # Weighted by length: "AI enablement" beats "enablement" when both tracks match.
        return sum(len(k) for k in keywords if any(has_term(title, t) for t in [k] + SYNONYMS.get(k.lower(), [])))

    results = []
    for job, company_name in all_rows:
        # Words left out of titles, whole words ("intern" no longer hides "Internal …").
        # Where: read from the posting's location, before each company's cap.
        if region in ("na", "corridor") and not keep_region(job.location, region, job.location_type):
            continue
        if any(has_term(job.title, t) for t in exclude_terms):
            continue
        track, via, title_terms = None, None, []
        if want_facets and is_engineering(job.title):
            continue          # engineering titles count for neither track
        if want_facets and NOT_ADJACENT.search(job.title):
            continue          # "Paid Training", clinical facilitators: not learning roles
        if want_facets:
            # Generous: a wanted term in the title, or any learning-adjacent title word,
            # or one piece of instructional-design craft named in the description.
            hits = [(title_hits(job.title, ks), tr, [k for k in ks if any(has_term(job.title, t) for t in [k] + SYNONYMS.get(k.lower(), []))])
                    for tr, ks in want_facets]
            best = max(hits, key=lambda h: h[0])
            if best[0]:
                track, via, title_terms = best[1], "title", best[2]
            elif adjacent_title(job.title):
                track, via, title_terms = ("ai" if re.search(r"\bAI\b", job.title) else "learning"), "adjacent", [m.lower() for m in ADJACENT.findall(job.title)][:2]
            elif light_role(job.title, job.description, can_keywords):
                # Engineering-light or customer-experience, with instructional-design overlap.
                track, via, title_terms = ("ai" if re.search(r"\bAI\b", job.title) else "learning"), "light", [LIGHT.search(job.title).group(0).lower()]
            elif (bridge := bridge_role(job.title, job.description)):
                # Customer success and experience, AI hybrids, workforce and design roles.
                track, via, title_terms = bridge[0], "bridge", [bridge[1]]
            elif craft_in(job.description) and not sales_only(job.title):
                track, via = "learning", "text"
            else:
                continue
        elif can_keywords and not any(has_term(f"{job.title} {job.description or ''}", k) for k in can_keywords):
            continue
        days_old = (datetime.utcnow() - job.posted_date).days if job.posted_date else 0
        score, matched = _fit(job, via, title_terms, can_keywords, want_keywords)
        pj = pipeline.get(job.id)
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
            is_saved=pj is not None,
            relevance_score=score,
            matched_keywords=matched,
            track=track,
            pipeline_status=pj.status if pj else None,
            applied_date=pj.applied_date.isoformat() if pj and pj.applied_date else None,
        ))

    # Ranked: best first, then newest. Then the per-company cap keeps each company's best.
    if selected_ids:
        results.sort(key=lambda j: (-j.relevance_score, j.days_old))
    if per_company_limit:
        seen: dict = {}
        kept = []
        for r in results:
            seen[r.company] = seen.get(r.company, 0) + 1
            if seen[r.company] <= per_company_limit:
                kept.append(r)
        results = kept

    return JobsListResponse(profile=profile_label, total=len(results), results=results)


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    company = db.query(Company).filter(Company.id == job.company_id).first()
    return {**job.to_dict(company_name=company.name if company else None), "description": job.description}
