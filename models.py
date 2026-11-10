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
    score_a = Column(Integer, nullable=True)
    score_m = Column(Integer, nullable=True)
    score_p = Column(Integer, nullable=True)
    # Where the company lives online, and how sure we are of its career page.
    # url_status: verified (George confirmed it; only these are scraped),
    # found (a job board answered and matches), page (a careers page, no board),
    # website (website only), none, or unverified (typed in, not yet confirmed).
    website = Column(String(500), nullable=True)
    url_status = Column(String(20), nullable=True)
    url_source = Column(String(30), nullable=True)   # discovery, manual, legacy
    verified_at = Column(DateTime, nullable=True)
    check_json = Column(Text, nullable=True)         # the last Check: what a scrape would find

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "career_url": self.career_url,
            "website": self.website,
            "url_status": self.url_status,
            "notes": self.notes,
            "is_active": self.is_active,
            "is_skipped": self.is_skipped,
            "score_a": self.score_a,
            "score_m": self.score_m,
            "score_p": self.score_p,
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
    description = Column(Text, nullable=True)
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


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)


class Discovery(Base):
    """What discover.py found for a company name, cached by its normalised key
    (the same key the studio's intake uses). Covers names not yet in Search."""
    __tablename__ = "discoveries"

    key = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    result_json = Column(Text, nullable=False)
    checked_at = Column(DateTime, default=datetime.utcnow)


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
    # The full posting, kept with the application: copied from the scrape when it
    # has it, fetched when the job is saved, or pasted for a manual entry.
    posting_text = Column(Text, nullable=True)
    posting_state = Column(String(20), nullable=True)  # ready, fetching, failed, manual
    # The application work done in the portfolio studio's Render tool (letter
    # draft, resume suggestions) and its checklist, as JSON. Kept here so an
    # application survives a browser reset and reads the same on every machine.
    work_json = Column(Text, nullable=True)
    checklist_json = Column(Text, nullable=True)
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
