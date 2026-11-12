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
            if p.facet_side == "want"]


def title_matches(titles: List[str], tracks: List[tuple]) -> dict:
    """How many titles match what you want, whole words, by track (each title counted once)."""
    from synonyms import SYNONYMS
    out = {"learning": 0, "engineering": 0, "total": 0}
    for t in titles or []:
        best, hits = None, 0
        for track, kws in tracks:
            n = sum(1 for k in kws if any(has_term(t, w) for w in [k] + SYNONYMS.get(k.lower(), [])))
            if n > hits:
                best, hits = track, n
        if hits:
            out["total"] += 1
            if best in out:
                out[best] += 1
    return out
