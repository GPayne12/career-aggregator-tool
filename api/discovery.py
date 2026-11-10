"""Look up a company's website and job board by name, before or after it's in Search.

  POST /api/discovery/lookup {names: [...]}            cached results only (fast; the intake deck)
  POST /api/discovery/run    {names: [...], refresh}   look up names not cached yet, a few at a time
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from discover import company_key, discover
from models import Discovery

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

RUN_LIMIT = 12     # names looked up per request; the caller asks again for more
WORKERS = 6


class Names(BaseModel):
    names: List[str]
    refresh: bool = False


def cached(db: Session, name: str) -> Optional[Dict]:
    row = db.query(Discovery).filter(Discovery.key == company_key(name)).first()
    return json.loads(row.result_json) if row else None


def store(db: Session, result: Dict) -> None:
    row = db.query(Discovery).filter(Discovery.key == result["key"]).first()
    if not row:
        row = Discovery(key=result["key"], name=result["name"])
        db.add(row)
    row.name = result["name"]
    row.result_json = json.dumps(result)
    row.checked_at = datetime.utcnow()


def run_many(db: Session, names: List[str], refresh: bool = False, limit: Optional[int] = None) -> Dict[str, Dict]:
    """Look up names (cached unless refresh), store new results, return {key: result}."""
    out: Dict[str, Dict] = {}
    todo: List[str] = []
    for n in names:
        k = company_key(n)
        if not k or k in out:
            continue
        hit = None if refresh else cached(db, n)
        if hit:
            out[k] = hit
        elif k not in {company_key(t) for t in todo}:
            todo.append(n)
    if limit is not None:
        todo = todo[:limit]
    if todo:
        with ThreadPoolExecutor(WORKERS) as ex:
            for r in ex.map(discover, todo):
                store(db, r)
                out[r["key"]] = r
        db.commit()
    return out


@router.post("/lookup")
def lookup(body: Names, db: Session = Depends(get_db)):
    rows = db.query(Discovery).filter(Discovery.key.in_([company_key(n) for n in body.names])).all()
    return {"results": {r.key: json.loads(r.result_json) for r in rows}}


@router.post("/run")
def run(body: Names, db: Session = Depends(get_db)):
    results = run_many(db, body.names, refresh=body.refresh, limit=RUN_LIMIT)
    missing = [n for n in body.names if company_key(n) and company_key(n) not in results]
    return {"results": results, "remaining": len(missing)}
