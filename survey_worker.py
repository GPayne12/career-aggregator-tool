"""Reads one career page in a separate process.

The survey runs inside the web server's worker threads, where Playwright's sync
API can stall with no way to time it out (seen on Accenture, 2026-11-12: the
browser sat idle for minutes). Here every read happens in one child process
that the survey can kill when a company runs past its deadline or Stop is pressed.
"""
from typing import Dict, List, Tuple

from scraper import JobScraper

_scraper = None


def read(url: str) -> Tuple[List[Dict], bool]:
    """The postings on a career page, and whether the read was cut short."""
    global _scraper
    if _scraper is None:
        # 15 pages deep at most (300 postings, newest first); 30 seconds a page.
        _scraper = JobScraper(rate_limit_seconds=0.4, max_pages=15, page_timeout_ms=30000)
    jobs = _scraper.scrape_career_page(url)
    return jobs, bool(getattr(_scraper, "truncated", False))


def close() -> None:
    global _scraper
    if _scraper is not None:
        _scraper.close()
        _scraper = None
