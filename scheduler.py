import logging
from datetime import datetime, timedelta
from typing import Generator
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from database import SessionLocal
from models import Company, Job
from scraper import JobScraper

logger = logging.getLogger(__name__)


def _save_jobs(db: Session, scraper: JobScraper, company: Company, scraped_jobs: list) -> int:
    added = 0
    cutoff = datetime.utcnow() - timedelta(days=7)
    for job_data in scraped_jobs:
        posted_date = scraper.process_posted_date(job_data.get('posted_date'))
        if posted_date and posted_date < cutoff:
            continue

        existing = db.query(Job).filter(
            Job.company_id == company.id,
            Job.url == job_data['url']
        ).first()
        if existing:
            existing.fetched_at = datetime.utcnow()
            existing.is_active = True
        else:
            location = job_data.get('location')
            db.add(Job(
                company_id=company.id,
                title=job_data['title'],
                location=location,
                location_type=scraper.infer_location_type(location),
                salary_range=job_data.get('salary'),
                url=job_data['url'],
                posted_date=posted_date,
                fetched_at=datetime.utcnow(),
                is_active=True,
            ))
            added += 1
    return added


def iter_scrape() -> Generator[dict, None, None]:
    """Generator that scrapes all active companies and yields progress events.

    Event shapes:
      {"type": "start",    "total": N}
      {"type": "progress", "index": i, "total": N, "company": str,
                           "found": int, "added": int, "error": str|None}
      {"type": "done",     "total_added": int, "pruned": int, "errors": int}
    """
    db: Session = SessionLocal()
    scraper = JobScraper()
    try:
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.is_skipped == False,
        ).all()
        total = len(companies)
        total_added = 0
        errors = 0

        yield {"type": "start", "total": total}

        for i, company in enumerate(companies, 1):
            if not company.career_url:
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": 0, "added": 0,
                       "error": "No career URL set"}
                continue
            try:
                scraped_jobs = scraper.scrape_career_page(company.career_url)
                added = _save_jobs(db, scraper, company, scraped_jobs)
                db.commit()
                total_added += added
                logger.info(f"Scraped {company.name}: {len(scraped_jobs)} listings, {added} new")
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": len(scraped_jobs),
                       "added": added, "error": None}
            except Exception as e:
                logger.error(f"Error scraping {company.name}: {e}")
                errors += 1
                db.rollback()
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": 0,
                       "added": 0, "error": str(e)}

        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        pruned_jobs = db.query(Job).filter(
            Job.is_active == True,
            Job.fetched_at < seven_days_ago,
        ).all()
        for job in pruned_jobs:
            job.is_active = False
        db.commit()

        yield {"type": "done", "total_added": total_added,
               "pruned": len(pruned_jobs), "errors": errors}

    except Exception as e:
        logger.error(f"Fatal error in iter_scrape: {e}")
        db.rollback()
        yield {"type": "error", "message": str(e)}
    finally:
        scraper.close()
        db.close()


def scrape_all_companies() -> dict:
    """Scrape all active companies. Returns a summary dict."""
    result = {"total_added": 0, "pruned": 0, "errors": 0, "companies": []}
    for event in iter_scrape():
        if event["type"] == "progress":
            result["companies"].append({
                "company": event["company"],
                "found": event["found"],
                "added": event["added"],
                "error": event["error"],
            })
            if event["error"]:
                result["errors"] += 1
            result["total_added"] += event["added"]
        elif event["type"] == "done":
            result["pruned"] = event["pruned"]
    return result
