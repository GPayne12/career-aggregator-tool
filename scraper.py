import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional
import time

logger = logging.getLogger(__name__)


class JobScraper:
    """Scrapes job listings from company career pages using BeautifulSoup"""

    def __init__(self, rate_limit_seconds: float = 1.5):
        self.rate_limit_seconds = rate_limit_seconds
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch HTML from a URL with rate limiting"""
        try:
            time.sleep(self.rate_limit_seconds)
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def infer_location_type(self, location: Optional[str]) -> Optional[str]:
        """Infer location type (remote/on_site/hybrid) from location string"""
        if not location:
            return None

        location_lower = location.lower()
        if 'remote' in location_lower:
            return 'remote'
        elif 'hybrid' in location_lower:
            return 'hybrid'
        else:
            return 'on_site'

    def parse_common_format(self, html: str) -> List[Dict]:
        """
        Generic parser for common HTML job listing formats.
        Looks for common job board patterns.
        """
        jobs = []
        soup = BeautifulSoup(html, 'html.parser')

        # Try to find job postings using common selectors
        job_selectors = [
            '.job-posting',
            '.job-item',
            '.job-card',
            '.position',
            '[data-job-id]',
            'article.job',
            'div[data-position-id]',
        ]

        job_elements = []
        for selector in job_selectors:
            job_elements = soup.select(selector)
            if job_elements:
                logger.info(f"Found {len(job_elements)} jobs using selector: {selector}")
                break

        if not job_elements:
            # If no specific selectors found, look for links that might be job listings
            logger.warning("Using fallback generic parsing")
            return jobs

        for element in job_elements:
            try:
                # Try common patterns for title, location, link
                title = None
                location = None
                url = None
                salary = None
                posted_date = None

                # Title
                title_elem = element.find(['h2', 'h3', 'h1', 'a.job-title'])
                if title_elem:
                    title = title_elem.get_text(strip=True)

                # Link
                link_elem = element.find('a', href=True)
                if link_elem:
                    url = link_elem['href']
                    if not title:
                        title = link_elem.get_text(strip=True)

                # Location
                location_elem = element.find(
                    string=lambda s: s and any(loc in str(s).lower() for loc in ['location', 'city', 'remote'])
                )
                if not location_elem:
                    location_elem = element.find(['span', 'div'], class_=['location', 'job-location'])
                if location_elem:
                    location = location_elem.get_text(strip=True)

                # Salary
                salary_elem = element.find(
                    string=lambda s: s and any(sal in str(s).lower() for sal in ['salary', '$', '€', '£'])
                )
                if salary_elem:
                    salary = salary_elem.get_text(strip=True)

                # Posted date (try to find relative dates)
                date_elem = element.find(
                    string=lambda s: s and any(d in str(s).lower() for d in ['ago', 'days', 'hours', 'posted'])
                )
                if date_elem:
                    posted_date = date_elem.get_text(strip=True)

                if title and url:
                    jobs.append({
                        'title': title,
                        'location': location,
                        'salary': salary,
                        'url': url,
                        'posted_date': posted_date,
                    })
            except Exception as e:
                logger.error(f"Error parsing job element: {e}")
                continue

        return jobs

    def scrape_career_page(self, url: str) -> List[Dict]:
        """
        Scrape a company's career page and extract job listings.
        Returns a list of job dictionaries.
        """
        logger.info(f"Scraping {url}")
        html = self.fetch_page(url)
        if not html:
            return []

        jobs = self.parse_common_format(html)
        logger.info(f"Found {len(jobs)} jobs from {url}")
        return jobs

    def process_posted_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Convert various date formats to datetime"""
        if not date_str:
            return None

        date_str = date_str.lower().strip()

        # Check for relative dates
        if 'ago' in date_str:
            try:
                if 'hour' in date_str:
                    hours = int(date_str.split()[0])
                    return datetime.utcnow() - timedelta(hours=hours)
                elif 'day' in date_str:
                    days = int(date_str.split()[0])
                    return datetime.utcnow() - timedelta(days=days)
                elif 'week' in date_str:
                    weeks = int(date_str.split()[0])
                    return datetime.utcnow() - timedelta(weeks=weeks)
            except (ValueError, IndexError):
                pass

        # For now, if we can't parse, return None (use fetched_at as proxy)
        return None
