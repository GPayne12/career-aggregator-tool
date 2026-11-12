"""What Survey learns when a posting is removed, and why.

A removed posting never comes back: not the same posting, and not the same
title from the same company posted again under a new link. Each reason also
teaches Survey something, applied as a lower score (never a hidden posting):

  role              the kind of role is wrong: the same role, at any company,
                    scores 15 lower (up to 30)
  responsibilities  the work described is wrong: a posting describing much the
                    same work scores 10 lower (up to 20)
  experience        the level is wrong (too junior or too senior): a posting
                    at the same level scores 15 lower (up to 30). The level is
                    the title's seniority, and the years of experience it asks for.
"""
import json
import re
from collections import Counter
from typing import Dict, List, Optional

REASONS = ("role", "responsibilities", "experience")

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


def record(job, reason: str) -> Dict:
    """What a removal keeps, for teaching."""
    return {
        "role": role_key(job.title),
        "words": work_words(job.description) if reason == "responsibilities" else [],
        "level": level(job.title) if reason == "experience" else None,
        "years": years_asked(job.description) if reason == "experience" else None,
    }


class Lessons:
    def __init__(self, removals):
        self.ids = {r.job_id for r in removals if r.job_id}
        self.urls = {r.url for r in removals if r.url}
        self.same = {(r.company_id, (r.title or "").strip().lower()) for r in removals}
        self.roles = Counter()
        self.works: List[set] = []
        self.levels: List[Dict] = []
        for r in removals:
            kept = json.loads(r.learned_json or "{}")
            if r.reason == "role" and kept.get("role"):
                self.roles[kept["role"]] += 1
            if r.reason == "responsibilities" and kept.get("words"):
                self.works.append(set(kept["words"]))
            if r.reason == "experience" and kept.get("level"):
                self.levels.append(kept)

    def removed(self, job) -> bool:
        return (job.id in self.ids or job.url in self.urls
                or (job.company_id, (job.title or "").strip().lower()) in self.same)

    def penalty(self, job):
        """(points off, why) from what removals taught."""
        off, why = 0, []
        n = self.roles.get(role_key(job.title), 0)
        if n:
            off += min(30, 15 * n); why.append("like a role you removed")
        if self.works and job.description:
            mine = set(work_words(job.description))
            alike = sum(1 for w in self.works if mine and len(mine & w) / len(mine | w) >= 0.3)
            if alike:
                off += min(20, 10 * alike); why.append("like work you removed")
        if self.levels:
            lv, yrs = level(job.title), years_asked(job.description)
            # Same level, and (when both name them) within a year of the same experience.
            alike = sum(1 for k in self.levels if k["level"] == lv
                        and (yrs is None or k.get("years") is None or abs(yrs - k["years"]) <= 1))
            if alike:
                off += min(30, 15 * alike); why.append("a level you removed")
        return off, why
