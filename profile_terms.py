"""The Profile's words, by side.

want: roles and directions George is after. They decide which postings count as
      a match (in the title), which fill a company's 25 slots first, and a
      company's P score.
can:  skills he brings. They lift postings whose text asks for them when ranking.
"""
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
