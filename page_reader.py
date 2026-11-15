"""Career pages read in a separate process, with a deadline.

Inside the web server, Playwright's sync API can stall with no way to time it
out (seen on Accenture 2026-11-12, and before that on Capital One). So the
survey and Landscape's Check never drive a browser in the server itself: each
has a PageReader, a child process it can kill when a read runs past its
deadline or the survey is stopped.

    survey   15 pages deep (300 postings, newest first), 30 seconds a page
    check    the first 2 pages, 25 seconds a page: answers in seconds
"""
import concurrent.futures as cf
import multiprocessing as mp
import threading
import time
from typing import Dict, List, Optional, Tuple

# ── In the child process ─────────────────────────────────────────────────────

_SETTINGS = {
    "survey": dict(rate_limit_seconds=0.4, max_pages=15, page_timeout_ms=30000),
    "check": dict(rate_limit_seconds=0.3, max_pages=2, page_timeout_ms=25000),
}
_scraper = None


def _read(kind: str, url: str) -> Tuple[List[Dict], bool]:
    """The postings on a career page, and whether the read was cut short.
    The browser is kept open between reads."""
    global _scraper
    if _scraper is None:
        from scraper import JobScraper
        _scraper = JobScraper(**_SETTINGS[kind])
    jobs = _scraper.scrape_career_page(url)
    return jobs, bool(getattr(_scraper, "truncated", False))


def _close() -> None:
    global _scraper
    if _scraper is not None:
        _scraper.close()
        _scraper = None


# ── In the server ────────────────────────────────────────────────────────────

class Cancelled(Exception):
    pass


class PageReader:
    """One child process reading career pages, one at a time.

    kind      "survey" or "check" (how deep it reads)
    deadline  seconds one read may take before the child is killed
    cancel    an Event: when set, the read in progress is abandoned at once
    idle      seconds after its last read when the child shuts itself down
    """

    def __init__(self, kind: str, deadline: int, cancel: Optional[threading.Event] = None,
                 idle: Optional[int] = None):
        self.kind, self.deadline, self.cancel, self.idle = kind, deadline, cancel, idle
        self.pool = None
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def read(self, url: str) -> Tuple[List[Dict], bool]:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            if self.pool is None:
                self.pool = cf.ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"))
            future = self.pool.submit(_read, self.kind, url)
            started = time.time()
            try:
                while True:
                    try:
                        return future.result(timeout=1)
                    except cf.TimeoutError:
                        if self.cancel is not None and self.cancel.is_set():
                            self._kill()
                            raise Cancelled()
                        if time.time() - started > self.deadline:
                            self._kill()
                            raise TimeoutError(f"The career page timed out after {self.deadline} seconds")
            finally:
                if self.idle and self.pool is not None:
                    self._timer = threading.Timer(self.idle, self.close)
                    self._timer.daemon = True
                    self._timer.start()

    def _kill(self) -> None:
        if self.pool is not None:
            for proc in list((self.pool._processes or {}).values()):
                proc.kill()
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None

    def close(self) -> None:
        """Close the browser and end the child."""
        if not self._lock.acquire(timeout=1):
            return        # a read is under way; it will set a new idle timer
        try:
            if self.pool is not None:
                try:
                    self.pool.submit(_close).result(timeout=15)
                except Exception:
                    pass
                self._kill()
        finally:
            self._lock.release()
