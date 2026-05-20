import csv
import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Company

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str
    career_url: str
    notes: Optional[str] = None


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    career_url: Optional[str] = None
    notes: Optional[str] = None


class CompanyResponse(BaseModel):
    id: int
    name: str
    career_url: str
    notes: Optional[str] = None
    is_active: bool


@router.get("", response_model=List[CompanyResponse])
def list_companies(db: Session = Depends(get_db)):
    """List all active companies"""
    companies = db.query(Company).filter(Company.is_active == True).all()
    return [
        CompanyResponse(
            id=c.id,
            name=c.name,
            career_url=c.career_url,
            notes=c.notes,
            is_active=c.is_active,
        )
        for c in companies
    ]


@router.get("/all", response_model=List[CompanyResponse])
def list_all_companies(db: Session = Depends(get_db)):
    """List all companies including inactive ones"""
    companies = db.query(Company).all()
    return [
        CompanyResponse(
            id=c.id,
            name=c.name,
            career_url=c.career_url,
            notes=c.notes,
            is_active=c.is_active,
        )
        for c in companies
    ]


@router.get("/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int, db: Session = Depends(get_db)):
    """Get a specific company"""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return CompanyResponse(
        id=company.id,
        name=company.name,
        career_url=company.career_url,
        notes=company.notes,
        is_active=company.is_active,
    )


@router.post("", response_model=CompanyResponse, status_code=201)
def create_company(company: CompanyCreate, db: Session = Depends(get_db)):
    """Create a new company"""
    # Check if company already exists
    existing = db.query(Company).filter(Company.name == company.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Company '{company.name}' already exists",
        )

    db_company = Company(
        name=company.name,
        career_url=company.career_url,
        notes=company.notes,
    )
    db.add(db_company)
    db.commit()
    db.refresh(db_company)

    return CompanyResponse(
        id=db_company.id,
        name=db_company.name,
        career_url=db_company.career_url,
        notes=db_company.notes,
        is_active=db_company.is_active,
    )


@router.put("/{company_id}", response_model=CompanyResponse)
def update_company(
    company_id: int, company: CompanyUpdate, db: Session = Depends(get_db)
):
    """Update a company"""
    db_company = db.query(Company).filter(Company.id == company_id).first()
    if not db_company:
        raise HTTPException(status_code=404, detail="Company not found")

    if company.name is not None:
        # Check if new name already exists
        existing = (
            db.query(Company)
            .filter(Company.name == company.name, Company.id != company_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Company '{company.name}' already exists",
            )
        db_company.name = company.name

    if company.career_url is not None:
        db_company.career_url = company.career_url
    if company.notes is not None:
        db_company.notes = company.notes

    db.commit()
    db.refresh(db_company)

    return CompanyResponse(
        id=db_company.id,
        name=db_company.name,
        career_url=db_company.career_url,
        notes=db_company.notes,
        is_active=db_company.is_active,
    )


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: int, db: Session = Depends(get_db)):
    """Soft delete a company (mark as inactive)"""
    db_company = db.query(Company).filter(Company.id == company_id).first()
    if not db_company:
        raise HTTPException(status_code=404, detail="Company not found")

    db_company.is_active = False
    db.commit()


@router.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Bulk-import companies from a CSV file (columns: name, career_url, notes)."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))

    if not {"name", "career_url"}.issubset(set(reader.fieldnames or [])):
        raise HTTPException(status_code=400, detail="CSV must contain 'name' and 'career_url' columns")

    added, skipped, errors = 0, 0, []

    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        career_url = (row.get("career_url") or "").strip()
        notes = (row.get("notes") or "").strip() or None

        if not name or not career_url:
            errors.append(f"Row {i}: missing name or career_url")
            continue

        existing = db.query(Company).filter(Company.name == name).first()
        if existing:
            skipped += 1
            continue

        db.add(Company(name=name, career_url=career_url, notes=notes))
        added += 1

    db.commit()
    return {"added": added, "skipped": skipped, "errors": errors}
