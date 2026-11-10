"""Fetch the full text of one job posting, for the application workflow.

Called once, when a job is saved to the pipeline (not during scrapes, which
would cost one extra request per job at every tracked company).

Order of attempts, cheapest first:
  1. The page's schema.org JobPosting JSON-LD, which most applicant tracking
     systems embed for search engines; it holds the full description.
  2. The readable text of the page's main content.
  3. The same two, after rendering the page in a headless browser, for sites
     that build their content with JavaScript (slow: several seconds).
"""
import json
import logging
import re
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

from scraper import HEADERS

logger = logging.getLogger(__name__)
MIN_CHARS = 400


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                desc = BeautifulSoup(item["description"], "html.parser").get_text("\n")
                if len(desc) >= MIN_CHARS // 2:
                    return _clean(desc)
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body
    text = _clean(main.get_text("\n")) if main else ""
    return text if len(text) >= MIN_CHARS else None


def fetch_posting_text(url: str) -> Tuple[Optional[str], str]:
    """Returns (text, state): state is 'ready' or 'failed'."""
    if not url:
        return None, "failed"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if resp.ok:
            text = _from_html(resp.text)
            if text:
                return text, "ready"
    except requests.RequestException as e:
        logger.info("posting fetch failed for %s: %s", url, e)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(url, timeout=25000, wait_until="networkidle")
            html = page.content()
            browser.close()
        text = _from_html(html)
        if text:
            return text, "ready"
    except Exception as e:  # noqa: BLE001 - any render failure means "paste it"
        logger.info("posting render failed for %s: %s", url, e)
    return None, "failed"
