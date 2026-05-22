import csv
import io
import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import List, Optional
from database import get_db, SessionLocal
from models import Company
from scraper import find_career_url

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str
    career_url: Optional[str] = None


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    career_url: Optional[str] = None
    notes: Optional[str] = None
    score_a: Optional[int] = None
    score_m: Optional[int] = None
    score_p: Optional[int] = None


class CompanyResponse(BaseModel):
    id: int
    name: str
    career_url: str
    notes: Optional[str] = None
    is_active: bool
    is_skipped: bool = False
    score_a: Optional[int] = None
    score_m: Optional[int] = None
    score_p: Optional[int] = None


def _to_response(c: Company) -> CompanyResponse:
    return CompanyResponse(
        id=c.id, name=c.name, career_url=c.career_url or "",
        notes=c.notes, is_active=c.is_active, is_skipped=bool(c.is_skipped),
        score_a=c.score_a, score_m=c.score_m, score_p=c.score_p,
    )


# ── Fixed-path routes first (must come before /{company_id}) ─────────────────

@router.get("", response_model=List[CompanyResponse])
def list_companies(db: Session = Depends(get_db)):
    companies = db.query(Company).filter(Company.is_active == True).all()
    return [_to_response(c) for c in companies]


@router.get("/all", response_model=List[CompanyResponse])
def list_all_companies(db: Session = Depends(get_db)):
    companies = db.query(Company).all()
    return [_to_response(c) for c in companies]


@router.get("/discover-urls")
def discover_missing_urls():
    """SSE stream: find career URLs for all active companies that have none."""
    def generate():
        db = SessionLocal()
        try:
            companies = db.query(Company).filter(
                Company.is_active == True,
                or_(Company.career_url == None, Company.career_url == ""),
            ).all()
            total = len(companies)
            yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

            found_count = 0
            for i, company in enumerate(companies, 1):
                url = find_career_url(company.name)
                if url:
                    company.career_url = url
                    db.commit()
                    found_count += 1
                yield f"data: {json.dumps({'type': 'progress', 'index': i, 'total': total, 'company': company.name, 'url': url})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'found': found_count, 'total': total})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            db.close()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Bulk-import companies from a CSV with a 'name' column (career_url optional)."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))

    if 'name' not in (reader.fieldnames or []):
        raise HTTPException(status_code=400, detail="CSV must contain a 'name' column")

    added, skipped, errors = 0, 0, []

    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i}: missing name")
            continue

        if db.query(Company).filter(Company.name == name).first():
            skipped += 1
            continue

        career_url = (row.get("career_url") or "").strip()
        db.add(Company(name=name, career_url=career_url))
        added += 1

    db.commit()
    return {"added": added, "skipped": skipped, "errors": errors}


# ── Param routes after all fixed paths ───────────────────────────────────────

@router.post("/{company_id}/skip", response_model=CompanyResponse)
def toggle_company_skip(company_id: int, db: Session = Depends(get_db)):
    """Toggle the is_skipped flag for a company (skipped companies are excluded from scraping)."""
    db_company = db.query(Company).filter(Company.id == company_id).first()
    if not db_company:
        raise HTTPException(status_code=404, detail="Company not found")
    db_company.is_skipped = not bool(db_company.is_skipped)
    db.commit()
    db.refresh(db_company)
    return _to_response(db_company)


@router.get("/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int, db: Session = Depends(get_db)):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return _to_response(company)


@router.post("", response_model=CompanyResponse, status_code=201)
def create_company(company: CompanyCreate, db: Session = Depends(get_db)):
    existing = db.query(Company).filter(Company.name == company.name).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Company '{company.name}' already exists")

    db_company = Company(name=company.name, career_url=company.career_url or "")
    db.add(db_company)
    db.commit()
    db.refresh(db_company)
    return _to_response(db_company)


@router.put("/{company_id}", response_model=CompanyResponse)
def update_company(company_id: int, company: CompanyUpdate, db: Session = Depends(get_db)):
    db_company = db.query(Company).filter(Company.id == company_id).first()
    if not db_company:
        raise HTTPException(status_code=404, detail="Company not found")

    if company.name is not None:
        existing = (db.query(Company)
                    .filter(Company.name == company.name, Company.id != company_id)
                    .first())
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"Company '{company.name}' already exists")
        db_company.name = company.name

    if company.career_url is not None:
        db_company.career_url = company.career_url
    if company.notes is not None:
        db_company.notes = company.notes
    if company.score_a is not None:
        db_company.score_a = company.score_a
    if company.score_m is not None:
        db_company.score_m = company.score_m
    if company.score_p is not None:
        db_company.score_p = company.score_p

    db.commit()
    db.refresh(db_company)
    return _to_response(db_company)


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: int, db: Session = Depends(get_db)):
    db_company = db.query(Company).filter(Company.id == company_id).first()
    if not db_company:
        raise HTTPException(status_code=404, detail="Company not found")
    db_company.is_active = False
    db.commit()
