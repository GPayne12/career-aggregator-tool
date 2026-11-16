"""Chew: take a posting off Survey and keep it as data. See chew.py for what it means."""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from chew import Chewed
from chew_store import ACCEPTED, DISMISSED, FIELD_FACET, TASTE, digest, field_facet, get_list, load_sets, put_list, _generation
from database import SessionLocal, get_db
from models import Chew, Company, Job, KeywordProfile, Setting
from posting import quick_text

router = APIRouter(prefix="/api/chews", tags=["chews"])


class ChewBody(BaseModel):
    job_id: int
    context: dict = {}


class TermBody(BaseModel):
    term: str
    kind: str = "title"            # title (a role word) or language (a phrase from descriptions)
    track: Optional[str] = None    # for a role word: learning or ai


def _read_text(chew_id: int) -> None:
    """A chewed posting with only a title: read its text once (one plain request, no browser)."""
    db = SessionLocal()
    try:
        c = db.query(Chew).filter(Chew.id == chew_id).first()
        if not c or c.description:
            return
        text = quick_text(c.url)
        if text:
            c.description = text
            job = db.query(Job).filter(Job.id == c.job_id).first() if c.job_id else None
            if job and not job.description:
                job.description = text
            db.commit()
    finally:
        db.close()


@router.post("", status_code=201)
def chew_job(body: ChewBody, background: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == body.job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if Chewed(db.query(Chew).all()).has(job):
        raise HTTPException(status_code=409, detail="Already chewed")
    c = Chew(job_id=job.id, company_id=job.company_id, title=job.title, url=job.url, location=job.location,
             location_type=job.location_type, posted_date=job.posted_date, description=job.description,
             source="survey", context_json=json.dumps(body.context or {}))
    db.add(c)
    db.commit()
    if not (job.description or "").strip():
        background.add_task(_read_text, c.id)
    return {"id": c.id, "chewed": job.id}


@router.get("")
def list_chews(db: Session = Depends(get_db)):
    """Every chewed posting, newest first."""
    names = {c.id: c.name for c in db.query(Company).all()}
    return [{"id": c.id, "job_id": c.job_id, "title": c.title, "company": names.get(c.company_id),
             "url": c.url, "location": c.location, "has_text": bool((c.description or "").strip()),
             "chewed_at": c.created_at.isoformat() if c.created_at else None,
             "context": json.loads(c.context_json or "{}")}
            for c in db.query(Chew).order_by(Chew.created_at.desc(), Chew.id.desc()).all()]


@router.get("/digest")
def get_digest(db: Session = Depends(get_db)):
    """What the pile suggests, what was accepted and what that has reached since."""
    return digest(db)


@router.post("/digest/accept")
def accept(body: TermBody, db: Session = Depends(get_db)):
    """Take a suggestion into the Profile, and note it so its results can be followed."""
    term = body.term.strip()
    if not term or body.kind not in ("title", "language"):
        raise HTTPException(status_code=400, detail="term, and kind title or language")
    if body.kind == "title":
        track = body.track if body.track in ("learning", "ai") else "learning"
        facet = (db.query(KeywordProfile).filter(KeywordProfile.side == "want", KeywordProfile.track == track)
                 .order_by(KeywordProfile.position, KeywordProfile.id).first())
        if not facet:
            raise HTTPException(status_code=404, detail=f"No searched facet on the {track} track")
    else:
        track = None
        facet = field_facet(db)
        if not facet:
            last = db.query(func.max(KeywordProfile.position)).scalar() or 0
            facet = KeywordProfile(name=FIELD_FACET, side="can", position=last + 1)
            facet.set_keywords([])
            db.add(facet)
            db.flush()
    words = facet.get_keywords()
    if term.lower() not in {w.lower() for w in words}:
        facet.set_keywords(words + [term])
    accepted = [a for a in get_list(db, ACCEPTED) if a["term"].lower() != term.lower()]
    sets = load_sets(db)
    accepted.append({"term": term, "kind": body.kind, "track": track, "facet_id": facet.id,
                     "at": datetime.utcnow().isoformat(), "after_id": db.query(func.max(Job.id)).scalar() or 0,
                     "gen": _generation(term, body.kind, sets["targets"] + sets["chewed"], accepted)})
    put_list(db, ACCEPTED, accepted)
    db.commit()
    return {"accepted": term, "facet": facet.name}


@router.post("/digest/dismiss")
def dismiss(body: TermBody, db: Session = Depends(get_db)):
    """Not this one: it isn't suggested again."""
    dismissed = get_list(db, DISMISSED)
    if body.term.lower() not in {d.lower() for d in dismissed}:
        put_list(db, DISMISSED, dismissed + [body.term.strip()])
        db.commit()
    return {"dismissed": body.term}


@router.post("/digest/retire")
def retire(body: TermBody, db: Session = Depends(get_db)):
    """Take an accepted word back out of the Profile; it isn't suggested again."""
    accepted = get_list(db, ACCEPTED)
    entry = next((a for a in accepted if a["term"].lower() == body.term.lower()), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Not an accepted word")
    facet = db.query(KeywordProfile).filter(KeywordProfile.id == entry.get("facet_id")).first()
    if facet:
        facet.set_keywords([w for w in facet.get_keywords() if w.lower() != body.term.lower()])
    put_list(db, ACCEPTED, [a for a in accepted if a is not entry])
    dismissed = get_list(db, DISMISSED)
    if body.term.lower() not in {d.lower() for d in dismissed}:
        put_list(db, DISMISSED, dismissed + [entry["term"]])
    db.commit()
    return {"retired": entry["term"]}


class TasteBody(BaseModel):
    on: bool


@router.post("/taste")
def set_taste(body: TasteBody, db: Session = Depends(get_db)):
    """Switch taste on or off. Off, Survey's scores are exactly what the Profile gives."""
    row = db.query(Setting).filter(Setting.key == TASTE).first()
    if row:
        row.value = "on" if body.on else "off"
    else:
        db.add(Setting(key=TASTE, value="on" if body.on else "off"))
    db.commit()
    return {"on": body.on}


@router.delete("/{chew_id}", status_code=204)
def undo_chew(chew_id: int, db: Session = Depends(get_db)):
    """Undo a chew: the posting returns to Survey."""
    c = db.query(Chew).filter(Chew.id == chew_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chew not found")
    db.delete(c)
    db.commit()
