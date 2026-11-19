"""The chewed pile and its neighbours, read from the database for chew.py.

Three piles of postings George has given a verdict on:
  targets   in the Pipeline (pursued, applied and on: full weight; saved: half)
  chewed    the right field, not a target: chewed in Survey, or phased out in the
            Pipeline (phasing out is chewing; see phase_out)
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

# A saved job counts half until Render has work on it (once, the Preparing stage).
TARGET_WEIGHT = {"applied": 1.0, "interviewing": 1.0, "offer": 1.0, "saved": 0.5}
FIELD_FACET = "From the field"      # the skills facet that holds accepted field language
ACCEPTED, DISMISSED, TASTE = "chew_accepted", "chew_dismissed", "chew_taste"
MIN_TEXT = 400                      # a description shorter than this isn't read as one
# A word accepted this many rounds away from George's own Profile is flagged as drift.
FAR_GENERATION = 3


def _target_weight(pj: PipelineJob) -> float:
    return 1.0 if pj.status == "saved" and pj.work_json else TARGET_WEIGHT[pj.status]


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
                        "weight": _target_weight(pj) * chew.age_weight(_age(pj.stage_changed_date or pj.saved_date, now))})
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


# ── Phasing out: the Pipeline's way of chewing ─────────────────────────────
# A phased-out application is chewed: the right field, not a target (any more). It
# leaves the Pipeline, its posting stays off Survey for good, and its text joins the
# pile. The whole entry (dates, notes, Render's work) is kept in the chew's context,
# so undoing the chew puts the application back exactly as it was, under the same id.
PIPELINE_SOURCE = "pipeline"
LIVE_STATUSES = ("saved", "applied", "interviewing", "offer")
_ROW = ("id", "job_id", "company_id", "title", "location", "salary_range", "url", "status", "posting_text",
        "posting_state", "work_json", "checklist_json", "saved_date", "applied_date", "stage_changed_date",
        "notes", "created_at")
_DATES = {"saved_date", "applied_date", "stage_changed_date", "created_at"}


def phase_out(db: Session, pj: PipelineJob, when: Optional[datetime] = None) -> Chew:
    """Chew a pipeline entry and take it out of the Pipeline. The caller commits."""
    job = db.query(Job).filter(Job.id == pj.job_id).first() if pj.job_id else None
    snap = {k: (getattr(pj, k).isoformat() if k in _DATES and getattr(pj, k) else getattr(pj, k)) for k in _ROW}
    c = Chew(job_id=pj.job_id, company_id=pj.company_id, title=pj.title, url=pj.url or (job.url if job else None),
             location=pj.location or (job.location if job else None),
             location_type=job.location_type if job else None, posted_date=job.posted_date if job else None,
             description=pj.posting_text or (job.description if job else None), source=PIPELINE_SOURCE,
             context_json=json.dumps({"track": _track(pj.title), "phased_out_from": pj.status, "pipeline": snap}),
             created_at=when or datetime.utcnow())
    db.add(c)
    db.delete(pj)
    db.flush()
    return c


def restore(db: Session, c: Chew) -> Optional[PipelineJob]:
    """Undo a phase-out: the application returns to the stage it left. The caller
    deletes the chew and commits. None when the chew didn't come from the Pipeline."""
    snap = json.loads(c.context_json or "{}").get("pipeline") if c.source == PIPELINE_SOURCE else None
    if not snap:
        return None
    fields = {k: (datetime.fromisoformat(v) if k in _DATES and v else v) for k, v in snap.items() if k in _ROW}
    if fields.get("status") not in LIVE_STATUSES:
        # Phased out before phasing out chewed (an old Archived or Phased out entry).
        fields["status"] = "applied" if fields.get("applied_date") else "saved"
    if fields.get("id") and db.query(PipelineJob).filter(PipelineJob.id == fields["id"]).first():
        fields.pop("id")          # the id was reused meanwhile: a new one, then
    pj = PipelineJob(**fields)
    db.add(pj)
    db.flush()
    return pj


def phased_out_count(db: Session) -> int:
    return db.query(Chew).filter(Chew.source == PIPELINE_SOURCE).count()


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
