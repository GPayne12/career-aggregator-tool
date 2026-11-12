import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Generator, List
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Company, Job, KeywordProfile, Setting
from scraper import JobScraper
import concurrent.futures as cf
import multiprocessing as mp
import survey_worker
from synonyms import expand_keywords
from profile_terms import facet_terms, has_term, WINDOW_DAYS

logger = logging.getLogger(__name__)

# Playwright raises TimeoutError on page load timeout; requests raises
# requests.exceptions.Timeout. Both indicate a company page that needs investigation.
_TIMEOUT_PHRASES = ("timeout", "timed out", "time out")

PER_COMPANY_CAP = 25

# The longest one company's read may take. A big Workday board, read 15 pages
# deep, takes about 40 seconds; a careers page that hands off to a board, a little more.
COMPANY_DEADLINE = 120


class _Cancelled(Exception):
    pass


class _Reader:
    """Career pages read in one child process, so a stalled read can be killed:
    at the company deadline, or at once when Stop is pressed."""

    def __init__(self):
        self.pool = None

    def read(self, url: str):
        if self.pool is None:
            self.pool = cf.ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"))
        future = self.pool.submit(survey_worker.read, url)
        started = time.time()
        while True:
            try:
                return future.result(timeout=1)
            except cf.TimeoutError:
                if CANCEL.is_set():
                    self.kill()
                    raise _Cancelled()
                if time.time() - started > COMPANY_DEADLINE:
                    self.kill()
                    raise TimeoutError(f"The career page timed out after {COMPANY_DEADLINE} seconds")

    def kill(self):
        if self.pool is not None:
            for proc in list((self.pool._processes or {}).values()):
                proc.kill()
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None

    def close(self):
        if self.pool is not None:
            try:
                self.pool.submit(survey_worker.close).result(timeout=15)
            except Exception:
                pass
            self.kill()


def _matches_keywords(title: str, keywords_lower: List[str]) -> bool:
    return any(has_term(title, kw) for kw in keywords_lower)


def _get_exclude_terms(db: Session) -> list:
    setting = db.query(Setting).filter(Setting.key == "exclude_terms").first()
    try:
        return json.loads(setting.value) if setting else []
    except Exception:
        return []


def _save_jobs(db: Session, scraper: JobScraper, company: Company,
               scraped_jobs: list, exclude_terms: list,
               keywords_lower: List[str]) -> int:
    cutoff = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
    exclude_lower = [t.lower() for t in exclude_terms]

    # Step 1: exclude terms + date filter, parse posted_date once
    candidates: List[tuple] = []
    for job_data in scraped_jobs:
        if exclude_lower and any(has_term(job_data.get('title', ''), t) for t in exclude_lower):
            continue
        posted_date = scraper.process_posted_date(job_data.get('posted_date'))
        if posted_date and posted_date < cutoff:
            continue
        candidates.append((job_data, posted_date))

    # Step 2: deduplicate within this batch, by link and by normalised title.
    # A posting without a link can't be saved or recognised later, so it's dropped.
    seen_titles: set = set()
    seen_urls: set = set()
    deduped: List[tuple] = []
    for job_data, posted_date in candidates:
        url = (job_data.get('url') or '').strip()
        if not url or url in seen_urls:
            continue
        norm = job_data.get('title', '').lower().strip()
        if norm in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(norm)
        job_data['url'] = url
        deduped.append((job_data, posted_date))

    # Step 3: cap at PER_COMPANY_CAP, keyword-matched jobs fill slots first
    if len(deduped) > PER_COMPANY_CAP:
        if keywords_lower:
            matched   = [(j, d) for j, d in deduped if     _matches_keywords(j.get('title', ''), keywords_lower)]
            unmatched = [(j, d) for j, d in deduped if not _matches_keywords(j.get('title', ''), keywords_lower)]
        else:
            matched, unmatched = [], deduped
        limited = (matched + unmatched)[:PER_COMPANY_CAP]
    else:
        limited = deduped

    # Step 4: upsert. The session runs with autoflush off, so a query can't see
    # rows added earlier in this loop; look postings up in a map kept here instead,
    # loaded once and updated as rows are added.
    by_url = {}
    for job in db.query(Job).filter(Job.company_id == company.id).order_by(Job.id).all():
        by_url.setdefault(job.url, job)
    added = 0
    for job_data, posted_date in limited:
        existing = by_url.get(job_data['url'])
        if existing:
            existing.fetched_at = datetime.utcnow()
            existing.is_active = True
            if job_data.get('description') and not existing.description:
                existing.description = job_data['description']
        else:
            location = job_data.get('location')
            job = Job(
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
            )
            db.add(job)
            by_url[job.url] = job
            added += 1
    return added


# What the survey is doing right now (for the page's live view, and for a page opened
# mid-survey), and the flag Stop sets. One survey at a time.
STATE: dict = {"running": False}
CANCEL = threading.Event()


def survey_status() -> dict:
    s = dict(STATE)
    now = time.time()
    s["seconds"] = round(now - s["started"]) if s.get("started") else 0
    s["company_seconds"] = round(now - s["company_started"]) if s.get("company_started") else 0
    s.pop("started", None); s.pop("company_started", None)
    return s


def iter_scrape() -> Generator[dict, None, None]:
    """Generator that scrapes all active companies and yields progress events.

    Event shapes:
      {"type": "start",    "total": N}
      {"type": "progress", "index": i, "total": N, "company": str,
                           "found": int, "added": int, "timed_out": bool, "error": str|None}
      {"type": "done",     "total_added": int, "pruned": int, "timed_out": int, "errors": int}
    """
    if STATE.get("running"):
        yield {"type": "busy", **survey_status()}
        return
    CANCEL.clear()
    STATE.clear()
    STATE.update(running=True, index=0, total=0, company=None, started=time.time(), company_started=None, found=0, added=0, errors=0)
    db: Session = SessionLocal()
    # Pages are read in a child process (survey_worker); this scraper only parses dates and places.
    scraper = JobScraper(rate_limit_seconds=0.4, max_pages=15, page_timeout_ms=30000)
    reader = _Reader()

    try:
        exclude_terms = _get_exclude_terms(db)

        raw_keywords = facet_terms(db, "want")
        keywords_lower = [kw.lower() for kw in expand_keywords(raw_keywords)] if raw_keywords else []

        # Only career pages George has verified are scraped; the rest wait in
        # Companies until he checks them.
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.is_skipped == False,
            Company.url_status == 'verified',
        ).all()
        held = db.query(Company).filter(
            Company.is_active == True,
            Company.is_skipped == False,
            (Company.url_status != 'verified') | (Company.url_status == None),
        ).count()
        total = len(companies)
        total_added = 0
        errors = 0
        timed_out_count = 0
        closed_total = 0

        STATE["total"] = total
        yield {"type": "start", "total": total, "held": held}

        stopped = False
        for i, company in enumerate(companies, 1):
            if CANCEL.is_set():
                stopped = True
                break
            STATE.update(index=i, company=company.name, company_started=time.time())
            yield {"type": "company", "index": i, "total": total, "company": company.name}
            if not company.career_url:
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": 0, "added": 0,
                       "timed_out": False, "error": "No career URL set"}
                continue

            try:
                scraped_jobs, truncated = reader.read(company.career_url)
                added = _save_jobs(db, scraper, company, scraped_jobs, exclude_terms, keywords_lower)
                # A posting that's no longer on the board has closed. Only when the
                # board answered in full: an empty, failed or cut-short read says
                # nothing about closures.
                if scraped_jobs and not truncated:
                    still_open = {j['url'].strip() for j in scraped_jobs if j.get('url')}
                    for job in db.query(Job).filter(Job.company_id == company.id, Job.is_active == True).all():
                        if job.url not in still_open:
                            job.is_active = False
                            closed_total += 1
                db.commit()
                total_added += added
                STATE["found"] += len(scraped_jobs); STATE["added"] += added
                logger.info(f"Scraped {company.name}: {len(scraped_jobs)} found, {added} new")
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": len(scraped_jobs),
                       "added": added, "timed_out": False, "error": None}

            except _Cancelled:
                stopped = True
                break
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
                STATE["errors"] += 1
                db.rollback()
                yield {"type": "progress", "index": i, "total": total,
                       "company": company.name, "found": 0, "added": 0,
                       "timed_out": is_timeout, "error": err_str}

        if stopped:
            # Stopped by request: keep what was read; the clean-up waits for a full survey.
            db.commit()
            yield {"type": "stopped", "index": STATE["index"], "total": total,
                   "total_added": total_added, "errors": errors}
            return

        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        pruned_jobs = db.query(Job).filter(
            Job.is_active == True,
            Job.fetched_at < seven_days_ago,
        ).all()
        for job in pruned_jobs:
            job.is_active = False
        stamp = db.query(Setting).filter(Setting.key == "last_survey").first()
        if stamp:
            stamp.value = datetime.utcnow().isoformat()
        else:
            db.add(Setting(key="last_survey", value=datetime.utcnow().isoformat()))
        db.commit()

        yield {"type": "done", "total_added": total_added, "closed": closed_total, "held": held,
               "pruned": len(pruned_jobs), "timed_out": timed_out_count, "errors": errors}

    except Exception as e:
        logger.error(f"Fatal error in iter_scrape: {e}")
        db.rollback()
        yield {"type": "error", "message": str(e)}
    finally:
        STATE["running"] = False
        CANCEL.clear()
        reader.close()
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
