"""The chewed pile and its neighbours, read from the database for chew.py.

Three piles of postings George has given a verdict on:
  targets   in the Pipeline (pursued, applied and on: full weight; saved: half)
  chewed    the right field, not a target
  removed   wrong
and two lists he controls: the suggestions he accepted (with what each has reached
since: the loop's check on itself) and the ones he dismissed.
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import chew
from feedback import Lessons
from models import Chew, Job, KeywordProfile, PipelineJob, Removal, Setting
from profile_terms import AI_WORD, CRAFT, LEARNING_WORK, WINDOW_DAYS, facet_terms, has_term

TARGET_WEIGHT = {"preparing": 1.0, "ready": 1.0, "applied": 1.0, "interviewing": 1.0, "offer": 1.0, "saved": 0.5}
FIELD_FACET = "From the field"      # the skills facet that holds accepted field language
ACCEPTED, DISMISSED, TASTE = "chew_accepted", "chew_dismissed", "chew_taste"
MIN_TEXT = 400                      # a description shorter than this isn't read as one
# A word accepted this many rounds away from George's own Profile is flagged as drift.
FAR_GENERATION = 3


def get_list(db: Session, key: str) -> list:
    row = db.query(Setting).filter(Setting.key == key).first()
    try:
        return json.loads(row.value) if row else []
    except (json.JSONDecodeError, TypeError):
        return []


def put_list(db: Session, key: str, value: list) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = json.dumps(value)
    else:
        db.add(Setting(key=key, value=json.dumps(value)))


def taste_on(db: Session) -> bool:
    """Taste lifts scores only once George has switched it on (it is off to begin with)."""
    row = db.query(Setting).filter(Setting.key == TASTE).first()
    return bool(row and row.value == "on")


def field_facet(db: Session) -> Optional[KeywordProfile]:
    return db.query(KeywordProfile).filter(KeywordProfile.name == FIELD_FACET).first()


def field_terms(db: Session) -> List[str]:
    """The field's language George accepted. It lives in a skills facet, so taking a
    phrase off the Profile page retires it here too."""
    facet = field_facet(db)
    return facet.get_keywords() if facet else []


def _track(title: Optional[str]) -> str:
    return "ai" if AI_WORD.search(title or "") else "learning"


def _age(when: Optional[datetime], now: datetime) -> float:
    return max(0.0, (now - when).total_seconds() / 86400) if when else 0.0


def load_sets(db: Session) -> Dict[str, List[dict]]:
    """targets, chewed, removed: each a list of {title, text, location_type, track, weight}."""
    now = datetime.utcnow()
    jobs = {}

    def job(job_id):
        if job_id and job_id not in jobs:
            jobs[job_id] = db.query(Job).filter(Job.id == job_id).first()
        return jobs.get(job_id)

    targets = []
    for pj in db.query(PipelineJob).filter(PipelineJob.status.in_(list(TARGET_WEIGHT))).all():
        j = job(pj.job_id)
        targets.append({"title": pj.title, "text": pj.posting_text or (j.description if j else None),
                        "location_type": j.location_type if j else None, "track": _track(pj.title),
                        "weight": TARGET_WEIGHT[pj.status] * chew.age_weight(_age(pj.stage_changed_date or pj.saved_date, now))})
    chewed = []
    for c in db.query(Chew).all():
        context = json.loads(c.context_json or "{}")
        chewed.append({"title": c.title, "text": c.description, "location_type": c.location_type,
                       "track": context.get("track") or _track(c.title),
                       "weight": chew.age_weight(_age(c.created_at, now))})
    removed = []
    for r in db.query(Removal).all():
        j = job(r.job_id)
        removed.append({"title": r.title, "text": j.description if j else None,
                        "location_type": j.location_type if j else None, "track": _track(r.title),
                        "weight": chew.age_weight(_age(r.created_at, now))})
    return {"targets": targets, "chewed": chewed, "removed": removed}


def build_taste(sets: Dict[str, List[dict]]) -> chew.Taste:
    feats = lambda rows: [(chew.features(r["title"], r["text"], r["location_type"]), r["weight"]) for r in rows]
    return chew.Taste(feats(sets["targets"]), feats(sets["chewed"] + sets["removed"]))


# Every description read, counted once and recounted only when more have been read.
_BACKGROUND: dict = {"n": None, "df": None}


def background(db: Session) -> Tuple[chew.Counter, int]:
    n = db.query(Job).filter(Job.description != None).count()
    if _BACKGROUND["n"] != n:
        texts = [d for (d,) in db.query(Job.description).filter(Job.description != None).all() if d and len(d) > MIN_TEXT]
        _BACKGROUND.update(n=n, df=chew.doc_freq(texts), docs=len(texts))
    return _BACKGROUND["df"], _BACKGROUND["docs"]


def _generation(term: str, kind: str, kept: List[dict], accepted: List[dict]) -> int:
    """How many rounds from George's own Profile: 1 when the postings behind a word
    came in on his own terms, one more than the furthest accepted word otherwise."""
    behind = [k for k in kept if has_term(k["title"] if kind == "title" else k["text"], term)]
    gens = [a.get("gen", 1) for a in accepted
            if any(has_term(k["title"], a["term"]) or has_term(k["text"], a["term"]) for k in behind)]
    return 1 + max(gens) if gens else 1


def yields(db: Session, accepted: List[dict]) -> List[dict]:
    """For each accepted word: the postings it has reached since, and what became of them."""
    if not accepted:
        return []
    lessons = Lessons(db.query(Removal).all())
    chewed = chew.Chewed(db.query(Chew).all())
    in_pipeline = {pj.job_id for pj in db.query(PipelineJob).filter(PipelineJob.job_id != None).all()
                   if pj.status in TARGET_WEIGHT}
    first = min(a.get("after_id", 0) for a in accepted)
    new_jobs = db.query(Job).filter(Job.id > first).all()
    out = []
    for a in accepted:
        hit = [j for j in new_jobs if j.id > a.get("after_id", 0)
               and has_term(j.title if a["kind"] == "title" else j.description, a["term"])]
        pursued = sum(1 for j in hit if j.id in in_pipeline)
        chewed_n = sum(1 for j in hit if chewed.has(j))
        removed_n = sum(1 for j in hit if lessons.removed(j))
        out.append({**a, "reached": len(hit), "pursued": pursued, "chewed": chewed_n, "removed": removed_n,
                    "waiting": len(hit) - pursued - chewed_n - removed_n,
                    # Removed more often than kept, on at least three: it hasn't earned its place.
                    "retire": removed_n >= 3 and removed_n > pursued + chewed_n,
                    "far": a.get("gen", 1) >= FAR_GENERATION})
    return out


def sources(db: Session, want: List[str], exclude: List[str]) -> Dict[str, list]:
    """Companies by what their postings have been worth: the ones George keeps from,
    and the ones read at length with never a title worth reading."""
    from models import Company
    from profile_terms import worth_reading
    names = {c.id: c.name for c in db.query(Company).all()}
    read, worth = {}, {}
    for cid, title in db.query(Job.company_id, Job.title).all():
        read[cid] = read.get(cid, 0) + 1
        if worth_reading(title, want, exclude):
            worth[cid] = worth.get(cid, 0) + 1
    kept = {}
    for cid, in db.query(Chew.company_id).all():
        kept[cid] = kept.get(cid, 0) + 1
    for cid, in db.query(PipelineJob.company_id).filter(PipelineJob.status.in_(list(TARGET_WEIGHT))).all():
        kept[cid] = kept.get(cid, 0) + 1
    best = sorted(({"company": names.get(c, "?"), "kept": n, "read": read.get(c, 0)} for c, n in kept.items()),
                  key=lambda r: -r["kept"])[:8]
    quiet = sorted(({"company": names.get(c, "?"), "read": n} for c, n in read.items() if n >= 50 and not worth.get(c)),
                   key=lambda r: -r["read"])[:8]
    return {"kept_from": best, "never_worth_reading": quiet}


def digest(db: Session) -> dict:
    sets = load_sets(db)
    kept = sets["targets"] + sets["chewed"]
    want, can = facet_terms(db, "want"), facet_terms(db, "can")
    dismissed, accepted = get_list(db, DISMISSED), get_list(db, ACCEPTED)
    cutoff = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
    all_titles, window_titles = [], []
    for title, active, fetched in db.query(Job.title, Job.is_active, Job.fetched_at).all():
        all_titles.append(title)
        if active and fetched and fetched >= cutoff:
            window_titles.append(title)
    titles = chew.widen_titles([(k["title"], k["track"]) for k in kept], want, window_titles, all_titles,
                               [r["title"] for r in sets["removed"]], dismissed)
    df, n = background(db)
    known = want + can + field_terms(db) + CRAFT + LEARNING_WORK
    language = chew.language([k["text"] for k in kept if k["text"] and len(k["text"]) > MIN_TEXT], df, n, known, dismissed)
    for s in titles:
        s["gen"] = _generation(s["term"], "title", kept, accepted)
    for s in language["terms"]:
        s["gen"] = _generation(s["term"], "language", kept, accepted)
    taste = build_taste(sets)
    setting = db.query(Setting).filter(Setting.key == "exclude_terms").first()
    exclude = json.loads(setting.value) if setting else []
    return {
        "counts": {k: len(v) for k, v in sets.items()},
        "titles": titles,
        "language": language,
        "taste": {"on": taste_on(db), "ready": taste.ready, "have": {"targets": taste.have[0], "others": taste.have[1]},
                  "need": {"targets": chew.MIN_TARGETS, "others": chew.MIN_OTHERS},
                  "lifts": [{"feature": f, "weight": w} for f, w in taste.strongest()] if taste.ready else []},
        "accepted": yields(db, accepted),
        "sources": sources(db, want, exclude),
    }
