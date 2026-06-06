import json
import logging
from datetime import datetime, timedelta
from typing import Generator
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Company, Job, Setting
from scraper import JobScraper

logger = logging.getLogger(__name__)

# Playwright raises TimeoutError on page load timeout; requests raises
# requests.exceptions.Timeout. Both indicate a company page that needs investigation.
_TIMEOUT_PHRASES = ("timeout", "timed out", "time out")


def _get_exclude_terms(db: Session) -> list:
    setting = db.query(Setting).filter(Setting.key == "exclude_terms").first()
    try:
        return json.loads(setting.value) if setting else []
    except Exception:
        return []


def _save_jobs(db: Session, scraper: JobScraper, company: Company,
               scraped_jobs: list, exclude_terms: list) -> int:
    added = 0
    cutoff = datetime.utcnow() - timedelta(days=7)
    exclude_lower = [t.lower() for t in exclude_terms]

    for job_data in scraped_jobs:
        title_lower = job_data.get('title', '').lower()

        if exclude_lower and any(t in title_lower for t in exclude_lower):
            continue

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
            if job_data.get('description') and not existing.description:
                existing.description = job_data['description']
        else:
            location = job_data.get('location')
            db.add(Job(
                company_id=company.id,
                title=job_data['title'],
                location=location,
                location_type=scraper.infer_location_type(location),
                salary_range=job_data.get('salary'),
                url=job_data['url'],
                description=job_data.get('description'),
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
                           "found": int, "added": int, "timed_out": bool, "error": str|None}
      {"type": "done",     "total_added": int, "pruned": int, "timed_out": int, "errors": int}
    """
    db: Session = SessionLocal()
    scraper = JobScraper()

    try:
        exclude_terms = _get_exclude_terms(db)

        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.is_skipped == False,
        ).all()
        total = len(companies)
        total_added = 0
        errors = 0
        timed_out_count = 0

        yield {"type": "start", "total": total}

        for i, company in enumerate(companies, 1):
            if not company.career_url:
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": 0, "added": 0,
                       "timed_out": False, "error": "No career URL set"}
                continue

            try:
                scraped_jobs = scraper.scrape_career_page(company.career_url)
                added = _save_jobs(db, scraper, company, scraped_jobs, exclude_terms)
                db.commit()
                total_added += added
                logger.info(f"Scraped {company.name}: {len(scraped_jobs)} found, {added} new")
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": len(scraped_jobs),
                       "added": added, "timed_out": False, "error": None}

            except Exception as e:
                err_str = str(e)
                is_timeout = any(p in err_str.lower() for p in _TIMEOUT_PHRASES)
                if is_timeout:
                    timed_out_count += 1
                    logger.warning(f"Timeout scraping {company.name}: {err_str[:120]}")
                    scraper.close()  # reset browser so the next company gets a clean context
                else:
                    logger.error(f"Error scraping {company.name}: {err_str}")
                errors += 1
                db.rollback()
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": 0, "added": 0,
                       "timed_out": is_timeout, "error": err_str}

        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        pruned_jobs = db.query(Job).filter(
            Job.is_active == True,
            Job.fetched_at < seven_days_ago,
        ).all()
        for job in pruned_jobs:
            job.is_active = False
        db.commit()

        yield {"type": "done", "total_added": total_added,
               "pruned": len(pruned_jobs), "timed_out": timed_out_count, "errors": errors}

    except Exception as e:
        logger.error(f"Fatal error in iter_scrape: {e}")
        db.rollback()
        yield {"type": "error", "message": str(e)}
    finally:
        scraper.close()
        db.close()


def scrape_all_companies() -> dict:
    """Scrape all active companies. Returns a summary dict."""
    result = {"total_added": 0, "pruned": 0, "timed_out": 0, "errors": 0, "companies": []}
    for event in iter_scrape():
        if event["type"] == "progress":
            result["companies"].append({
                "company": event["company"],
                "found": event["found"],
                "added": event["added"],
                "timed_out": event.get("timed_out", False),
                "error": event["error"],
            })
            if event["error"]:
                result["errors"] += 1
            result["total_added"] += event["added"]
        elif event["type"] == "done":
            result["pruned"] = event["pruned"]
            result["timed_out"] = event.get("timed_out", 0)
    return result
