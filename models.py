from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import json

Base = declarative_base()


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    career_url = Column(String(500), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    is_skipped = Column(Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "career_url": self.career_url,
            "notes": self.notes,
            "is_active": self.is_active,
            "is_skipped": self.is_skipped,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class KeywordProfile(Base):
    __tablename__ = "keyword_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    keywords = Column(Text, nullable=False)  # JSON array
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_keywords(self):
        """Parse keywords from JSON string"""
        try:
            return json.loads(self.keywords)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_keywords(self, keywords_list):
        """Store keywords as JSON string"""
        self.keywords = json.dumps(keywords_list)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "keywords": self.get_keywords(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    title = Column(String(500), nullable=False)
    location = Column(String(255), nullable=True)
    location_type = Column(String(50), nullable=True)  # remote, on_site, hybrid
    salary_range = Column(String(255), nullable=True)
    url = Column(String(500), nullable=False)
    posted_date = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    def infer_location_type(self):
        """Infer location type from location string if not set"""
        if self.location_type:
            return self.location_type

        if not self.location:
            return None

        location_lower = self.location.lower()
        if 'remote' in location_lower:
            return 'remote'
        elif 'hybrid' in location_lower:
            return 'hybrid'
        else:
            return 'on_site'

    def to_dict(self, company_name=None):
        return {
            "job_id": self.id,
            "company_id": self.company_id,
            "company": company_name,
            "title": self.title,
            "location": self.location,
            "location_type": self.location_type,
            "salary": self.salary_range,
            "url": self.url,
            "posted_date": self.posted_date.isoformat() if self.posted_date else None,
            "is_active": self.is_active,
        }


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    title = Column(String(500), nullable=False)
    location = Column(String(255), nullable=True)
    salary_range = Column(String(255), nullable=True)
    url = Column(String(500), nullable=False)
    status = Column(String(50), default="saved")  # saved, applied, rejected, interviewing, offer
    saved_date = Column(DateTime, default=datetime.utcnow)
    applied_date = Column(DateTime, nullable=True)
    stage_changed_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self, company_name=None):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "company_id": self.company_id,
            "company": company_name,
            "title": self.title,
            "location": self.location,
            "salary": self.salary_range,
            "url": self.url,
            "status": self.status,
            "saved_date": self.saved_date.isoformat() if self.saved_date else None,
            "applied_date": self.applied_date.isoformat() if self.applied_date else None,
            "stage_changed_date": self.stage_changed_date.isoformat() if self.stage_changed_date else None,
            "notes": self.notes,
        }
