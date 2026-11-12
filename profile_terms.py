"""The Profile's words, by side.

want: roles and directions George is after. They decide which postings count as
      a match (in the title), which fill a company's 25 slots first, and a
      company's P score.
can:  skills he brings. They lift postings whose text asks for them when ranking.
"""
import re
from functools import lru_cache
from typing import List, Optional
from sqlalchemy.orm import Session
from models import KeywordProfile


def facet_terms(db: Session, side: Optional[str] = None, ids: Optional[List[int]] = None) -> List[str]:
    q = db.query(KeywordProfile)
    if ids:
        q = q.filter(KeywordProfile.id.in_(ids))
    seen, out = set(), []
    for p in q.order_by(KeywordProfile.position, KeywordProfile.id).all():
        if side and p.facet_side != side:
            continue
        if p.facet_side == "want" and not p.track:
            continue   # a want facet with no track is paused: not searched
        for kw in p.get_keywords():
            if kw.lower() not in seen:
                seen.add(kw.lower()); out.append(kw)
    return out


@lru_cache(maxsize=4096)
def _word(term: str):
    # A term counts only as a whole word or phrase: "AI" isn't in "email",
    # "intern" isn't in "internal".
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower().strip()) + r"(?![a-z0-9])")


def has_term(text: Optional[str], term: str) -> bool:
    return bool(text) and bool(term.strip()) and _word(term).search(text.lower()) is not None


def want_tracks(db: Session) -> List[tuple]:
    """Want facets as (track, keywords); the same terms Survey matches in titles."""
    return [(p.track, p.get_keywords()) for p in db.query(KeywordProfile).order_by(KeywordProfile.position, KeywordProfile.id).all()
            if p.facet_side == "want" and p.track]


def title_matches(titles: List[str], tracks: List[tuple]) -> dict:
    """How many titles match what you want, whole words, by track (each title counted once)."""
    from synonyms import SYNONYMS
    out = {"learning": 0, "ai": 0, "total": 0}
    for t in titles or []:
        if is_engineering(t):
            continue
        best, hits = None, 0
        for track, kws in tracks:
            n = sum(len(k) for k in kws if any(has_term(t, w) for w in [k] + SYNONYMS.get(k.lower(), [])))   # the more specific term wins
            if n > hits:
                best, hits = track, n
        if hits:
            out["total"] += 1
            if best in out:
                out[best] += 1
    return out


# Engineering titles count for neither track, unless the title is unmistakably
# about learning ("E-learning Developer", "Learning Engineer", "Instructional ...").
_ENGINEERING = re.compile(r"\b(engineer|engineering|software|developer|scientist|machine learning|sre|devops|backend|front[- ]?end|full[- ]?stack)\b", re.I)
_LEARNING = re.compile(r"\b(instructional|e-?learning|curriculum|(?<!machine )learning engineer|learning design|learning experience|learning technolog)", re.I)


def is_engineering(title: Optional[str]) -> bool:
    return bool(title) and bool(_ENGINEERING.search(title)) and not _LEARNING.search(title)


# How far back Survey looks, and keeps postings when a board is read (George's
# choice: a week).
WINDOW_DAYS = 7

# Learning-design work as a posting's description describes it. A posting whose
# description shows at least TEXT_MIN of these counts as a learning-design role
# even when its title says only "Specialist" or "Program Manager".
LEARNING_WORK = [
    "instructional design", "instructional designer", "learning design", "learning designer", "learning experience",
    "curriculum", "course design", "course development", "e-learning", "elearning", "learning management system", "lms",
    "addie", "storyboard", "storyboards", "adult learning", "learning objectives", "training materials", "training programs",
    "training program", "facilitator guide", "articulate", "storyline", "rise 360", "captivate", "camtasia", "scorm", "xapi",
    "needs analysis", "training needs", "microlearning", "blended learning", "learning programs", "learning and development",
    "enablement programs", "knowledge base", "learners", "learning content", "assessment design", "subject matter experts",
]
TEXT_MIN = 3


def learning_work(text: Optional[str]) -> List[str]:
    """Distinct learning-design signs in a posting's text."""
    return [w for w in LEARNING_WORK if has_term(text, w)] if text else []
