import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Company, Job
from scraper import JobScraper

logger = logging.getLogger(__name__)


def scrape_all_companies():
    """Scrape all active companies for new jobs. Returns a summary dict."""
    db: Session = SessionLocal()
    try:
        scraper = JobScraper()
        companies = db.query(Company).filter(Company.is_active == True).all()
        total_added = 0
        errors = 0
        company_results = []

        for company in companies:
            try:
                scraped_jobs = scraper.scrape_career_page(company.career_url)

                added = 0
                for job_data in scraped_jobs:
                    existing = db.query(Job).filter(
                        Job.company_id == company.id,
                        Job.url == job_data['url']
                    ).first()

                    if existing:
                        existing.fetched_at = datetime.utcnow()
                        existing.is_active = True
                    else:
                        posted_date = scraper.process_posted_date(job_data.get('posted_date'))
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

                company.last_scraped = datetime.utcnow()
                db.commit()
                total_added += added
                company_results.append({"company": company.name, "found": len(scraped_jobs), "added": added, "error": None})
                logger.info(f"Scraped {company.name}: {len(scraped_jobs)} listings, {added} new")

            except Exception as e:
                logger.error(f"Error scraping {company.name}: {e}")
                errors += 1
                db.rollback()
                company_results.append({"company": company.name, "found": 0, "added": 0, "error": str(e)})

        # Prune jobs older than 14 days
        fourteen_days_ago = datetime.utcnow() - timedelta(days=14)
        pruned = db.query(Job).filter(Job.posted_date < fourteen_days_ago, Job.is_active == True).all()
        for job in pruned:
            job.is_active = False
        db.commit()

        return {
            "total_added": total_added,
            "pruned": len(pruned),
            "errors": errors,
            "companies": company_results,
        }

    except Exception as e:
        logger.error(f"Fatal error in scrape_all_companies: {e}")
        db.rollback()
        raise
    finally:
        db.close()
