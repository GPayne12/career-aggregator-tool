import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])

EXCLUDE_KEY = "exclude_terms"


def get_exclude_terms(db: Session) -> list:
    setting = db.query(Setting).filter(Setting.key == EXCLUDE_KEY).first()
    try:
        return json.loads(setting.value) if setting else []
    except Exception:
        return []


def _save_exclude_terms(db: Session, terms: list) -> list:
    setting = db.query(Setting).filter(Setting.key == EXCLUDE_KEY).first()
    if setting:
        setting.value = json.dumps(terms)
    else:
        db.add(Setting(key=EXCLUDE_KEY, value=json.dumps(terms)))
    db.commit()
    return terms


@router.get("/exclude-terms")
def list_exclude_terms(db: Session = Depends(get_db)):
    return get_exclude_terms(db)


class AddTermRequest(BaseModel):
    term: str


@router.post("/exclude-terms")
def add_exclude_term(body: AddTermRequest, db: Session = Depends(get_db)):
    term = body.term.strip().lower()
    if not term:
        raise HTTPException(status_code=400, detail="Term cannot be empty")
    terms = get_exclude_terms(db)
    if term not in [t.lower() for t in terms]:
        terms.append(term)
    return _save_exclude_terms(db, terms)


@router.delete("/exclude-terms/{term}")
def remove_exclude_term(term: str, db: Session = Depends(get_db)):
    terms = get_exclude_terms(db)
    terms = [t for t in terms if t.lower() != term.lower()]
    return _save_exclude_terms(db, terms)
