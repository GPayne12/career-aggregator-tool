from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import KeywordProfile

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


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


@router.get("", response_model=List[KeywordProfileResponse])
def list_profiles(db: Session = Depends(get_db)):
    """List all keyword profiles"""
    profiles = db.query(KeywordProfile).all()
    return [
        KeywordProfileResponse(
            id=p.id,
            name=p.name,
            keywords=p.get_keywords(),
        )
        for p in profiles
    ]


@router.get("/{profile_id}", response_model=KeywordProfileResponse)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get a specific profile"""
    profile = db.query(KeywordProfile).filter(KeywordProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return KeywordProfileResponse(
        id=profile.id,
        name=profile.name,
        keywords=profile.get_keywords(),
    )


@router.post("", response_model=KeywordProfileResponse, status_code=201)
def create_profile(profile: KeywordProfileCreate, db: Session = Depends(get_db)):
    """Create a new keyword profile"""
    # Check if profile already exists
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

    return KeywordProfileResponse(
        id=db_profile.id,
        name=db_profile.name,
        keywords=db_profile.get_keywords(),
    )


@router.put("/{profile_id}", response_model=KeywordProfileResponse)
def update_profile(
    profile_id: int, profile: KeywordProfileUpdate, db: Session = Depends(get_db)
):
    """Update a profile"""
    db_profile = (
        db.query(KeywordProfile).filter(KeywordProfile.id == profile_id).first()
    )
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile.name is not None:
        # Check if new name already exists
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

    return KeywordProfileResponse(
        id=db_profile.id,
        name=db_profile.name,
        keywords=db_profile.get_keywords(),
    )


@router.delete("/{profile_id}", status_code=204)
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a profile"""
    db_profile = (
        db.query(KeywordProfile).filter(KeywordProfile.id == profile_id).first()
    )
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db.delete(db_profile)
    db.commit()
