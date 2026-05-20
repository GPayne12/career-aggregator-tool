from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
import logging
from typing import List, Dict, Optional
import time

logger = logging.getLogger(__name__)

# Ordered from most-specific (ATS platforms) to most-generic
JOB_SELECTORS = [
    # Workday
    '[data-automation-id="jobPostingContainer"]',
    'li[data-automation-id="requisitionPostingCard"]',
    # Greenhouse
    '.opening',
    '.job-post',
    # Lever
    '.posting',
    # Ashby
    '[class*="ashby-job-posting-brief"]',
    # SmartRecruiters
    '.job-ad',
    # Generic
    '.job-posting',
    '.job-item',
    '.job-card',
    '[data-job-id]',
    'article.job',
    'div[data-position-id]',
    'tr.job-listing',
]


class JobScraper:
    """Scrapes job listings from company career pages using Playwright for JS rendering."""

    def __init__(self, rate_limit_seconds: float = 1.5):
        self.rate_limit_seconds = rate_limit_seconds
        self._playwright = None
        self._browser = None

    def _get_browser(self):
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def close(self):
        """Release the browser. Call when all scraping is done."""
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._browser = None
        self._playwright = None

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch fully-rendered HTML using a headless browser."""
        page = None
        try:
            time.sleep(self.rate_limit_seconds)
            browser = self._get_browser()
            page = browser.new_page()
            page.set_extra_http_headers({
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                )
            })
            page.goto(url, wait_until='networkidle', timeout=30000)
            return page.content()
        except PlaywrightTimeoutError:
            logger.warning(f"Network-idle timeout for {url} — returning partial content")
            return page.content() if page else None
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

    def infer_location_type(self, location: Optional[str]) -> Optional[str]:
        if not location:
            return None
        loc = location.lower()
        if 'remote' in loc:
            return 'remote'
        elif 'hybrid' in loc:
            return 'hybrid'
        return 'on_site'

    def _resolve_url(self, href: str, base_url: str) -> str:
        if href.startswith('http'):
            return href
        return urljoin(base_url, href)

    def parse_jobs(self, html: str, base_url: str) -> List[Dict]:
        soup = BeautifulSoup(html, 'html.parser')

        # Try each selector until one yields results
        elements = []
        matched_selector = None
        for selector in JOB_SELECTORS:
            elements = soup.select(selector)
            if elements:
                matched_selector = selector
                logger.info(f"Matched selector '{selector}': {len(elements)} elements")
                break

        if not elements:
            logger.warning(f"No job elements found for {base_url}")
            return []

        jobs = []
        for el in elements:
            try:
                title, url, location, salary, posted_date = None, None, None, None, None

                # Title — prefer explicit title elements, fall back to first link text
                for tag in ['h1', 'h2', 'h3', 'h4']:
                    t = el.find(tag)
                    if t and t.get_text(strip=True):
                        title = t.get_text(strip=True)
                        break

                # Link
                link = el.find('a', href=True)
                if link:
                    url = self._resolve_url(link['href'], base_url)
                    if not title:
                        title = link.get_text(strip=True)

                # Workday: title link has data-automation-id
                if not title:
                    wd_link = el.find('a', attrs={'data-automation-id': True})
                    if wd_link:
                        title = wd_link.get_text(strip=True)
                        url = self._resolve_url(wd_link['href'], base_url)

                # Location
                loc_el = el.find(['span', 'div', 'dd'], class_=lambda c: c and any(
                    x in c.lower() for x in ['location', 'job-location', 'city']
                ))
                if not loc_el:
                    loc_el = el.find(string=lambda s: s and any(
                        x in s.lower() for x in ['remote', 'hybrid', ', ']
                    ))
                if loc_el:
                    location = loc_el.get_text(strip=True) if hasattr(loc_el, 'get_text') else str(loc_el).strip()

                # Salary
                sal_el = el.find(string=lambda s: s and any(x in s for x in ['$', '€', '£', 'salary', 'Salary']))
                if sal_el:
                    salary = sal_el.strip()

                # Posted date
                date_el = el.find(string=lambda s: s and any(
                    x in s.lower() for x in ['ago', 'posted', 'days', 'hours', 'weeks']
                ))
                if date_el:
                    posted_date = date_el.strip()

                if title and url:
                    jobs.append({
                        'title': title,
                        'location': location,
                        'salary': salary,
                        'url': url,
                        'posted_date': posted_date,
                    })
            except Exception as e:
                logger.error(f"Error parsing element: {e}")
                continue

        return jobs

    def detect_platform(self, url: str) -> Optional[str]:
        if any(x in url for x in ['myworkdayjobs.com', 'workday.com']):
            return 'workday'
        if any(x in url for x in ['greenhouse.io', 'boards.greenhouse.io', 'job-boards.greenhouse.io']):
            return 'greenhouse'
        if any(x in url for x in ['lever.co', 'jobs.lever.co']):
            return 'lever'
        if 'ashbyhq.com' in url:
            return 'ashby'
        if 'smartrecruiters.com' in url:
            return 'smartrecruiters'
        return 'generic'

    def scrape_career_page(self, url: str) -> List[Dict]:
        platform = self.detect_platform(url)
        if platform == 'workday':
            raise ValueError(
                "Workday career pages block automated scraping. "
                "Use the company's Greenhouse, Lever, or Ashby job board URL instead."
            )
        logger.info(f"Scraping {url} (platform: {platform})")
        html = self.fetch_page(url)
        if not html:
            return []
        jobs = self.parse_jobs(html, url)
        logger.info(f"Found {len(jobs)} jobs from {url}")
        return jobs

    def process_posted_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        s = date_str.lower().strip()
        if 'ago' in s:
            try:
                n = int(s.split()[0])
                if 'hour' in s:
                    return datetime.utcnow() - timedelta(hours=n)
                if 'day' in s:
                    return datetime.utcnow() - timedelta(days=n)
                if 'week' in s:
                    return datetime.utcnow() - timedelta(weeks=n)
            except (ValueError, IndexError):
                pass
        return None
