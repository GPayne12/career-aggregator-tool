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
    """Heavy engineering (software, ML, backend, data science). Engineering-light roles
    (solutions, implementation, support, developer advocacy, technical writing…) aren't."""
    return bool(title) and bool(_ENGINEERING.search(title)) and not _LEARNING.search(title) and not LIGHT.search(title)


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


# ── Survey's generous inclusion ─────────────────────────────────────────────
# A job is worth seeing when its title is learning-design adjacent, or its
# description names even one piece of instructional-design craft. Survey is for
# finding jobs worth applying to, not for screening the best-written listing.
ADJACENT = re.compile(r"\b(learning|training|trainer|enablement|education|educational|educator|course|courses|l&d|onboarding|"
                      r"teaching|teacher|academic|lms|instructor|instruction|instructional|facilitator|curriculum|"
                      r"content design(?:er)?|content developer|tutor|coach|coaching|literacy|upskilling|reskilling|"
                      r"workforce development|talent development|change management|adoption)\b", re.I)
# Not learning roles, whatever their title words say.
NOT_ADJACENT = re.compile(r"\b(paid training|no experience|machine learning|deep learning|reinforcement learning|mental health|group facilitator|"
                          r"therapist|clinician|counsel(?:or|ing))\b", re.I)
# "Higher Education" naming a market ("Consultant, Higher Education") isn't a learning role on its own.
MARKET_ONLY = re.compile(r"\b(higher education|education (?:sector|market|vertical))\b", re.I)
SALES = re.compile(r"\b(sales|account (?:executive|manager)|business development|recruit(?:er|ing)|partnerships?)\b", re.I)
SALES_OK = re.compile(r"\b(enablement|training|trainer|learning|instruction)", re.I)

# Instructional-design craft, as a description names it. One is enough to include a job.
CRAFT = ["instructional design", "instructional designer", "curriculum design", "curriculum development", "curriculum developer",
         "e-learning", "elearning", "addie", "storyboard", "storyboarding", "scorm", "xapi", "articulate storyline", "articulate 360",
         "articulate rise", "rise 360", "storyline", "captivate", "adult learning", "learning objectives", "needs analysis",
         "microlearning", "course design", "course development", "learning experience design", "learning management system", "lms",
         "learning design", "instructional materials", "training design", "blended learning"]

# Core learning-design titles: the strongest title fit.
CORE_TITLE = re.compile(r"\b(instructional design(?:er)?|learning design(?:er)?|learning experience design(?:er)?|curriculum (?:designer|developer|manager)|"
                        r"e-?learning (?:developer|designer)|learning architect|(?<!machine )learning engineer|learning technologist|"
                        r"instructional technologist)\b", re.I)


def adjacent_title(title: Optional[str]) -> bool:
    if not title or NOT_ADJACENT.search(title) or is_engineering(title):
        return False
    words = ADJACENT.findall(title)
    if not words:
        return False
    # Only a market name ("Higher Education") and nothing else learning-ish: not a learning role.
    return not (MARKET_ONLY.search(title) and len(words) == 1 and words[0].lower().startswith("educat"))


def craft_in(text: Optional[str]) -> List[str]:
    return [w for w in CRAFT if has_term(text, w)] if text else []


def sales_only(title: Optional[str]) -> bool:
    return bool(title) and bool(SALES.search(title)) and not SALES_OK.search(title)


# ── Engineering-light and customer-experience roles ─────────────────────────
# Included when they overlap with instructional design: a learning word in the
# title, or a piece of ID craft in the description, or two of your skills there.
LIGHT = re.compile(r"\b(solutions? (?:engineer|consultant|architect)|implementation (?:engineer|consultant|specialist|manager|lead)|"
                   r"support engineer|customer engineer|sales engineer|technical account manager|"
                   r"developer (?:advocate|relations|education|experience)|devrel|technical writer|"
                   r"documentation (?:engineer|specialist|writer|manager)|prompt engineer|technical program manager|"
                   r"customer (?:success|experience|education|enablement|onboarding|training|learning|support)|cx|"
                   r"support specialist|knowledge (?:manager|management)|help center|community manager|academy)\b", re.I)
LEARN_WORD = re.compile(r"\b(education|enablement|onboarding|training|academy|knowledge|adoption|learning|instruction)", re.I)
# Skills too common in any posting to count as overlap on their own.
_COMMON_SKILLS = {"accessibility", "ai agents", "process management", "team leadership", "design management"}


def light_role(title: Optional[str], description: Optional[str], can: List[str]) -> bool:
    if not title or not LIGHT.search(title) or NOT_ADJACENT.search(title):
        return False
    if LEARN_WORD.search(title) or craft_in(description):
        return True
    skills = [k for k in can if k.lower() not in _COMMON_SKILLS and has_term(description, k)] if description else []
    return len(skills) >= 2
