"""Removed postings, and how a title or description is read.

A removed posting never comes back: not the same posting, and not the same title
from the same company posted again under a new link. Removing is one click and
teaches nothing. (It once asked why, and lowered similar postings; George retired
that on 2026-11-16. What Survey learns now comes from chewed postings: chew.py.)

role_key, level, years_asked and work_words read a posting; chew.py uses them.
"""
import re
from collections import Counter
from typing import List, Optional

_SENIORITY = re.compile(r"\b(senior|sr\.?|staff|lead|principal|associate|junior|jr\.?|entry[- ]level|"
                        r"i{1,3}|iv|[1-4]|level \d)\b", re.I)


def role_key(title: Optional[str]) -> str:
    """The role a title names, without seniority or the team after a comma:
    "Sr. Customer Success Manager, Enterprise" → "customer success manager"."""
    t = re.split(r"\s[-–—|(]\s?|,|\(|:", title or "")[0]
    t = _SENIORITY.sub(" ", t.lower())
    return re.sub(r"[^a-z&+ ]", " ", t).split() and " ".join(re.sub(r"[^a-z&+ ]", " ", t).split()) or ""


_STOP = set("""about above after again against their there these those which while would could should
other being have having where when what with within without from into your yours they them this that
will team teams work working role roles company including ability experience years strong across
able also more most such through using across help helps based must well like make makes great""".split())


def work_words(text: Optional[str], n: int = 30) -> List[str]:
    """The words a description leans on most: a rough fingerprint of the work."""
    words = [w for w in re.findall(r"[a-z]{5,}", (text or "").lower()) if w not in _STOP]
    return [w for w, _ in Counter(words).most_common(n)]


_LEVELS = [  # checked in this order: the first that matches is the title's level
    ("executive", re.compile(r"\b(vice president|vp|svp|evp|chief|head of|director)\b", re.I)),
    ("lead", re.compile(r"\b(lead|staff|principal|senior manager|group manager)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|iii|iv)\b", re.I)),
    ("entry", re.compile(r"\b(junior|jr\.?|associate|entry[- ]level|coordinator|assistant|i)\b", re.I)),
]
_YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)\b", re.I)


def level(title: Optional[str]) -> str:
    """The seniority a title names: entry, mid, senior, lead or executive."""
    for name, rx in _LEVELS:
        if rx.search(title or ""):
            return name
    return "mid"


def years_asked(text: Optional[str]) -> Optional[int]:
    """The years of experience a posting asks for (the smallest figure it names, 1 to 20)."""
    found = [int(m.group(1)) for m in _YEARS.finditer(text or "") if 1 <= int(m.group(1)) <= 20]
    return min(found) if found else None


class Lessons:
    """Which postings were removed: by id, by link, or the same title at the same company."""
    def __init__(self, removals):
        self.ids = {r.job_id for r in removals if r.job_id}
        self.urls = {r.url for r in removals if r.url}
        self.same = {(r.company_id, (r.title or "").strip().lower()) for r in removals}

    def removed(self, job) -> bool:
        return (job.id in self.ids or job.url in self.urls
                or (job.company_id, (job.title or "").strip().lower()) in self.same)
