import re
import time
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def find_career_url(company_name: str) -> Optional[str]:
    """Find a career URL for a company using Clearbit domain lookup + pattern probing."""
    from urllib.parse import quote_plus

    # 1. Get the company's domain from Clearbit autocomplete (free, no auth)
    domain = None
    try:
        resp = requests.get(
            f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote_plus(company_name)}",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json()
            if results:
                domain = results[0].get('domain')
    except Exception as e:
        logger.warning(f"Clearbit lookup failed for {company_name}: {e}")

    if not domain:
        return None

    # 2. Try common career URL patterns
    candidates = [
        f"https://{domain}/careers",
        f"https://{domain}/jobs",
        f"https://{domain}/about/careers",
        f"https://{domain}/company/careers",
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
    ]

    # (pattern, board-root extractor): grab just the slug, not a specific job link
    ats_patterns = [
        (r'https?://(?:boards|job-boards)\.greenhouse\.io/([^/\s"\'<>?#]+)',
         lambda m: f"https://boards.greenhouse.io/{m.group(1)}"),
        (r'https?://jobs\.lever\.co/([^/\s"\'<>?#]+)',
         lambda m: f"https://jobs.lever.co/{m.group(1)}"),
        (r'https?://jobs\.ashbyhq\.com/([^/\s"\'<>?#]+)',
         lambda m: f"https://jobs.ashbyhq.com/{m.group(1)}"),
        (r'https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/([^/\s"\'<>?#]+)',
         lambda m: f"https://{m.group(1)}/{m.group(2)}"),
    ]

    for url in candidates:
        try:
            resp = requests.get(
                url,
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=8,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                continue
            # Check if the page embeds a known ATS — return the board root
            for pattern, to_root in ats_patterns:
                m = re.search(pattern, resp.text, re.I)
                if m:
                    found = to_root(m)
                    logger.info(f"Found ATS board for {company_name}: {found}")
                    return found
            # The career page itself works — use it
            logger.info(f"Found career page for {company_name}: {resp.url}")
            return resp.url
        except Exception:
            continue

    logger.warning(f"Could not find career URL for {company_name} (domain: {domain})")
    return None


HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}

# CSS selectors for generic sites — ordered most-specific to least
GENERIC_SELECTORS = [
    '.opening',
    '.job-post',
    '.posting',
    '[class*="ashby-job-posting-brief"]',
    '.job-ad',
    '.job-posting',
    '.job-item',
    '.job-card',
    '[data-job-id]',
    'article.job',
    'div[data-position-id]',
    'tr.job-listing',
]


class JobScraper:
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
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._browser = None
        self._playwright = None

    def detect_platform(self, url: str) -> str:
        if any(x in url for x in ['myworkdayjobs.com', 'workday.com/wday']):
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

    # ── Workday ──────────────────────────────────────────────────────────────

    def _scrape_workday(self, url: str) -> List[Dict]:
        """Use Workday's internal search API (no Playwright needed)."""
        parsed = urlparse(url)
        tenant = parsed.hostname.split('.')[0]

        # Board name is the first path segment; discover it via redirect if absent
        path_parts = [p for p in parsed.path.strip('/').split('/') if p]
        if path_parts:
            board = path_parts[0]
        else:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
                final_parts = [p for p in urlparse(resp.url).path.strip('/').split('/') if p]
                board = final_parts[0] if final_parts else 'External'
            except Exception as e:
                logger.warning(f"Workday redirect discovery failed for {url}: {e}")
                board = 'External'

        base = f"https://{parsed.hostname}"
        api_url = f"{base}/wday/cxs/{tenant}/{board}/jobs"
        logger.info(f"Workday API: POST {api_url}")

        jobs: List[Dict] = []
        offset = 0
        limit = 20

        while True:
            try:
                time.sleep(self.rate_limit_seconds)
                resp = requests.post(
                    api_url,
                    json={"limit": limit, "offset": offset, "searchText": "",
                          "locations": [], "categories": []},
                    headers={**HEADERS, 'Content-Type': 'application/json'},
                    timeout=30,
                )
                if resp.status_code != 200:
                    logger.warning(f"Workday API {resp.status_code} for {api_url}")
                    break

                data = resp.json()
                postings = data.get('jobPostings', [])
                if not postings:
                    break

                for p in postings:
                    title = p.get('title', '')
                    location = p.get('locationsText', '')
                    posted_on = p.get('postedOn', '')
                    ext_path = p.get('externalPath', '')
                    job_url = f"{base}{ext_path}" if ext_path else ''
                    if title and job_url:
                        jobs.append({'title': title, 'location': location,
                                     'salary': None, 'url': job_url,
                                     'posted_date': posted_on})

                if len(postings) < limit:
                    break
                offset += limit

            except Exception as e:
                logger.error(f"Workday API error for {api_url}: {e}")
                break

        logger.info(f"Workday: {len(jobs)} jobs from {url}")
        return jobs

    # ── Greenhouse ───────────────────────────────────────────────────────────

    def _scrape_greenhouse(self, url: str) -> List[Dict]:
        """Use Greenhouse's public boards API."""
        parsed = urlparse(url)
        hostname = parsed.hostname or ''
        path_parts = [p for p in parsed.path.strip('/').split('/') if p]

        if 'greenhouse.io' in hostname:
            slug = path_parts[0] if path_parts else None
        else:
            slug = None

        if not slug:
            logger.warning(f"Could not extract Greenhouse slug from {url}")
            return []

        api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        logger.info(f"Greenhouse API: GET {api_url}")

        try:
            time.sleep(self.rate_limit_seconds)
            resp = requests.get(api_url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"Greenhouse API {resp.status_code} for {api_url}")
                return []

            jobs: List[Dict] = []
            for job in resp.json().get('jobs', []):
                title = job.get('title', '')
                job_url = job.get('absolute_url', '')
                location = (job.get('location') or {}).get('name', '')
                updated_at = job.get('updated_at', '')
                if title and job_url:
                    jobs.append({'title': title, 'location': location,
                                 'salary': None, 'url': job_url,
                                 'posted_date': updated_at})

            logger.info(f"Greenhouse: {len(jobs)} jobs from {url}")
            return jobs

        except Exception as e:
            logger.error(f"Greenhouse API error for {url}: {e}")
            return []

    # ── Lever ────────────────────────────────────────────────────────────────

    def _scrape_lever(self, url: str) -> List[Dict]:
        """Use Lever's public postings API."""
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.strip('/').split('/') if p]
        company = path_parts[0] if path_parts else None

        if not company:
            logger.warning(f"Could not extract Lever company slug from {url}")
            return []

        api_url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        logger.info(f"Lever API: GET {api_url}")

        try:
            time.sleep(self.rate_limit_seconds)
            resp = requests.get(api_url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"Lever API {resp.status_code} for {api_url}")
                return []

            jobs: List[Dict] = []
            for p in resp.json():
                title = p.get('text', '')
                job_url = p.get('hostedUrl', '')
                cats = p.get('categories', {})
                location = cats.get('location', '') or ''
                created_ms = p.get('createdAt')
                posted = (datetime.utcfromtimestamp(created_ms / 1000).isoformat()
                          if created_ms else None)
                if title and job_url:
                    jobs.append({'title': title, 'location': location,
                                 'salary': None, 'url': job_url,
                                 'posted_date': posted})

            logger.info(f"Lever: {len(jobs)} jobs from {url}")
            return jobs

        except Exception as e:
            logger.error(f"Lever API error for {url}: {e}")
            return []

    # ── Ashby ────────────────────────────────────────────────────────────────

    def _scrape_ashby(self, url: str) -> List[Dict]:
        """Use Ashby's public posting API, fall back to generic scrape."""
        parsed = urlparse(url)
        hostname = parsed.hostname or ''
        path_parts = [p for p in parsed.path.strip('/').split('/') if p]

        if 'jobs.ashbyhq.com' in hostname:
            company = path_parts[0] if path_parts else None
        elif 'ashbyhq.com' in hostname:
            company = hostname.replace('.ashbyhq.com', '')
        else:
            company = None

        if not company:
            return self._scrape_generic(url)

        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
        logger.info(f"Ashby API: GET {api_url}")

        try:
            time.sleep(self.rate_limit_seconds)
            resp = requests.get(api_url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                return self._scrape_generic(url)

            jobs: List[Dict] = []
            for job in resp.json().get('jobs', []):
                title = job.get('title', '')
                job_url = job.get('jobUrl', '')
                location = job.get('location', '')
                published_at = job.get('publishedAt', '')
                if title and job_url:
                    jobs.append({'title': title, 'location': location,
                                 'salary': None, 'url': job_url,
                                 'posted_date': published_at})

            logger.info(f"Ashby: {len(jobs)} jobs from {url}")
            return jobs

        except Exception as e:
            logger.error(f"Ashby API error for {url}: {e}")
            return self._scrape_generic(url)

    # ── Generic (Playwright) ──────────────────────────────────────────────────

    def _fetch_page_playwright(self, url: str) -> Optional[str]:
        page = None
        try:
            time.sleep(self.rate_limit_seconds)
            browser = self._get_browser()
            page = browser.new_page()
            page.set_extra_http_headers({'User-Agent': HEADERS['User-Agent']})
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

    def _parse_generic_html(self, html: str, base_url: str) -> List[Dict]:
        soup = BeautifulSoup(html, 'html.parser')

        elements = []
        for selector in GENERIC_SELECTORS:
            elements = soup.select(selector)
            if elements:
                logger.info(f"Matched '{selector}': {len(elements)} elements for {base_url}")
                break

        if not elements:
            logger.warning(f"No job elements found for {base_url}")
            return []

        jobs: List[Dict] = []
        for el in elements:
            try:
                title = location = salary = posted_date = url = None

                for tag in ['h1', 'h2', 'h3', 'h4']:
                    t = el.find(tag)
                    if t and t.get_text(strip=True):
                        title = t.get_text(strip=True)
                        break

                link = el.find('a', href=True)
                if link:
                    url = self._resolve_url(link['href'], base_url)
                    if not title:
                        title = link.get_text(strip=True)

                loc_el = el.find(['span', 'div', 'dd'], class_=lambda c: c and any(
                    x in c.lower() for x in ['location', 'job-location', 'city']
                ))
                if not loc_el:
                    loc_el = el.find(string=lambda s: s and any(
                        x in s.lower() for x in ['remote', 'hybrid']
                    ))
                if loc_el:
                    location = (loc_el.get_text(strip=True)
                                if hasattr(loc_el, 'get_text') else str(loc_el).strip())

                sal_el = el.find(string=lambda s: s and any(
                    x in s for x in ['$', '€', '£', 'salary', 'Salary']
                ))
                if sal_el:
                    salary = sal_el.strip()

                date_el = el.find(string=lambda s: s and any(
                    x in s.lower() for x in ['ago', 'posted', 'days', 'hours', 'weeks']
                ))
                if date_el:
                    posted_date = date_el.strip()

                if title and url:
                    jobs.append({'title': title, 'location': location,
                                 'salary': salary, 'url': url,
                                 'posted_date': posted_date})
            except Exception as e:
                logger.error(f"Error parsing element: {e}")

        return jobs

    def _detect_ats_url(self, html: str, base_url: str) -> Optional[str]:
        """Scan rendered HTML for embedded ATS links and return the best one."""
        soup = BeautifulSoup(html, 'html.parser')
        ats_patterns = [
            # Greenhouse
            (r'https?://(?:boards|job-boards)\.greenhouse\.io/[^\s"\'<>]+', 'greenhouse'),
            # Lever
            (r'https?://jobs\.lever\.co/[^\s"\'<>]+', 'lever'),
            # Ashby
            (r'https?://jobs\.ashbyhq\.com/[^\s"\'<>]+', 'ashby'),
            # Workday
            (r'https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^\s"\'<>]+', 'workday'),
        ]
        for pattern, platform in ats_patterns:
            # Search href attributes first, then raw text
            for tag in soup.find_all(href=re.compile(pattern, re.I)):
                href = tag['href']
                m = re.search(pattern, href, re.I)
                if m:
                    # Strip to the board root (no job-specific paths)
                    ats_url = self._trim_to_board(m.group(0), platform)
                    logger.info(f"Detected {platform} ATS at {ats_url} (from {base_url})")
                    return ats_url
            # Also check raw page text (for JS-injected links)
            m = re.search(pattern, html, re.I)
            if m:
                ats_url = self._trim_to_board(m.group(0), platform)
                logger.info(f"Detected {platform} ATS at {ats_url} (from {base_url})")
                return ats_url
        return None

    def _trim_to_board(self, url: str, platform: str) -> str:
        """Trim an ATS job URL down to the board root so we scrape all jobs."""
        parsed = urlparse(url)
        if platform == 'greenhouse':
            # boards.greenhouse.io/{slug}/jobs/123 → boards.greenhouse.io/{slug}
            parts = [p for p in parsed.path.strip('/').split('/') if p]
            slug = parts[0] if parts else ''
            return f"https://{parsed.hostname}/{slug}"
        if platform == 'lever':
            # jobs.lever.co/{company}/abc123 → jobs.lever.co/{company}
            parts = [p for p in parsed.path.strip('/').split('/') if p]
            company = parts[0] if parts else ''
            return f"https://jobs.lever.co/{company}"
        if platform == 'ashby':
            # jobs.ashbyhq.com/{company}/xyz → jobs.ashbyhq.com/{company}
            parts = [p for p in parsed.path.strip('/').split('/') if p]
            company = parts[0] if parts else ''
            return f"https://jobs.ashbyhq.com/{company}"
        if platform == 'workday':
            # Keep tenant + first path segment (the board name)
            parts = [p for p in parsed.path.strip('/').split('/') if p]
            board = parts[0] if parts else ''
            return f"https://{parsed.hostname}/{board}" if board else f"https://{parsed.hostname}"
        return url

    def _scrape_generic(self, url: str) -> List[Dict]:
        html = self._fetch_page_playwright(url)
        if not html:
            return []

        jobs = self._parse_generic_html(html, url)
        if jobs:
            return jobs

        # CSS selectors found nothing — scan the rendered page for ATS links
        ats_url = self._detect_ats_url(html, url)
        if ats_url:
            return self.scrape_career_page(ats_url)

        return []

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def scrape_career_page(self, url: str) -> List[Dict]:
        platform = self.detect_platform(url)
        logger.info(f"Scraping {url} (platform: {platform})")

        if platform == 'workday':
            return self._scrape_workday(url)
        elif platform == 'greenhouse':
            return self._scrape_greenhouse(url)
        elif platform == 'lever':
            return self._scrape_lever(url)
        elif platform == 'ashby':
            return self._scrape_ashby(url)
        else:
            return self._scrape_generic(url)

    # ── Date parsing ─────────────────────────────────────────────────────────

    def process_posted_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        s = date_str.strip()

        # ISO 8601: Greenhouse ("2024-01-15T12:00:00.000Z"), Lever, Ashby
        try:
            return datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            pass

        s_lower = s.lower()

        # Workday "Posted Today" / "Posted 30+ Days Ago" / "Posted 2 Days Ago"
        if 'today' in s_lower:
            return datetime.utcnow()

        match = re.search(r'(\d+)\+?\s*(hour|day|week|month)', s_lower)
        if match:
            n = int(match.group(1))
            unit = match.group(2)
            if unit == 'hour':
                return datetime.utcnow() - timedelta(hours=n)
            if unit == 'day':
                return datetime.utcnow() - timedelta(days=n)
            if unit == 'week':
                return datetime.utcnow() - timedelta(weeks=n)
            if unit == 'month':
                return datetime.utcnow() - timedelta(days=n * 30)

        return None
