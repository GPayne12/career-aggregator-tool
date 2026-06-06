import os
import re
import tempfile
import pdfplumber
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict
from database import get_db
from models import KeywordProfile, Job
from synonyms import SYNONYMS

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

RESUME_SECTION_HEADERS = {"PROFESSIONAL SUMMARY", "EXPERIENCE", "SKILLS", "EDUCATION", "CERTIFICATIONS"}


class KeywordProfileCreate(BaseModel):
    name: str
    keywords: List[str]


class KeywordProfileUpdate(BaseModel):
    name: Optional[str] = None
    keywords: Optional[List[str]] = None


class KeywordProfileResponse(BaseModel):
    id: int
    name: str
    keywords: List[str]


def _parse_resume_pdf(path: str) -> dict:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    sections: dict = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper() in RESUME_SECTION_HEADERS:
            current = stripped.upper()
            sections[current] = []
        elif current is not None and stripped:
            sections[current].append(stripped)

    # Role titles: pipe-delimited lines in EXPERIENCE ("Title | Company | Dates")
    role_titles = []
    seen: set = set()
    for line in sections.get("EXPERIENCE", []):
        if "|" in line and not line.startswith("●"):
            title = line.split("|")[0].strip()
            if (3 < len(title) < 80
                    and "@" not in title
                    and "www" not in title.lower()
                    and title.lower() not in seen):
                role_titles.append(title)
                seen.add(title.lower())

    # Skills: comma-separated text block
    skills_text = " ".join(sections.get("SKILLS", []))
    skills = [s.strip() for s in skills_text.split(",") if s.strip() and len(s.strip()) > 2]

    return {"role_titles": role_titles, "skills": skills}


@router.post("/seed-from-resume")
async def seed_from_resume(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        suggestions = _parse_resume_pdf(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")
    finally:
        os.unlink(tmp_path)

    return suggestions


@router.get("", response_model=List[KeywordProfileResponse])
def list_profiles(db: Session = Depends(get_db)):
    profiles = db.query(KeywordProfile).all()
    return [
        KeywordProfileResponse(id=p.id, name=p.name, keywords=p.get_keywords())
        for p in profiles
    ]


@router.get("/{profile_id}/stats")
def get_profile_stats(profile_id: int, db: Session = Depends(get_db)) -> Dict[str, int]:
    profile = db.query(KeywordProfile).filter(KeywordProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    keywords = profile.get_keywords()
    active_jobs = db.query(Job.title, Job.description).filter(Job.is_active == True).all()

    stats: Dict[str, int] = {}
    for kw in keywords:
        kw_lower = kw.lower()
        check_terms = [kw_lower] + SYNONYMS.get(kw_lower, [])
        count = sum(
            1 for title, desc in active_jobs
            if any(
                t in (title or "").lower() or t in (desc or "").lower()
                for t in check_terms
            )
        )
        stats[kw] = count

    return stats


@router.get("/{profile_id}", response_model=KeywordProfileResponse)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.query(KeywordProfile).filter(KeywordProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return KeywordProfileResponse(id=profile.id, name=profile.name, keywords=profile.get_keywords())


@router.post("", response_model=KeywordProfileResponse, status_code=201)
def create_profile(profile: KeywordProfileCreate, db: Session = Depends(get_db)):
    existing = db.query(KeywordProfile).filter(KeywordProfile.name == profile.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile '{profile.name}' already exists",
        )
    db_profile = KeywordProfile(name=profile.name)
    db_profile.set_keywords(profile.keywords)
    db.add(db_profile)
    db.commit()
    db.refresh(db_profile)
    return KeywordProfileResponse(id=db_profile.id, name=db_profile.name, keywords=db_profile.get_keywords())


@router.put("/{profile_id}", response_model=KeywordProfileResponse)
def update_profile(profile_id: int, profile: KeywordProfileUpdate, db: Session = Depends(get_db)):
    db_profile = db.query(KeywordProfile).filter(KeywordProfile.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile.name is not None:
        existing = (
            db.query(KeywordProfile)
            .filter(KeywordProfile.name == profile.name, KeywordProfile.id != profile_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Profile '{profile.name}' already exists",
            )
        db_profile.name = profile.name

    if profile.keywords is not None:
        db_profile.set_keywords(profile.keywords)

    db.commit()
    db.refresh(db_profile)
    return KeywordProfileResponse(id=db_profile.id, name=db_profile.name, keywords=db_profile.get_keywords())


@router.delete("/{profile_id}", status_code=204)
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    db_profile = db.query(KeywordProfile).filter(KeywordProfile.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete(db_profile)
    db.commit()
