"""What Survey keeps when a posting is chewed, and what the pile teaches.

Chew = "the right field, not a target": the posting leaves Survey for good and is
never pursued; its full text is kept. Chewed postings only widen Survey's reach and
enrich its results. Nothing here lowers a score or hides a posting.

The pile is read three ways, with counted words and no model calls:
  widen_titles  role words in kept titles that the Profile doesn't search for yet
  language      phrases kept descriptions lean on, against every description read
  Taste         what pursued postings share that chewed ones don't: a small, capped
                lift, off until there is enough data

The loop (chew_store.py, api/chews.py): a suggestion George accepts changes what
Survey lets in; those postings get verdicts; the verdicts become the next round's
suggestions, and say whether the accepted word earned its place.
"""
import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set, Tuple

from feedback import role_key, level, work_words
from profile_terms import has_term, ADJACENT

MIN_KEPT = 8          # kept descriptions before language() says anything
MIN_TARGETS = 8       # pursued/applied postings before Taste lifts a score
MIN_OTHERS = 15       # chewed + removed postings before Taste lifts a score
MAX_ADJUST = 15       # Taste never moves a score further than this
HALF_LIFE_DAYS = 60   # an example counts half as much after this long

_STOP = set("""the and for with you our your are will that this from have has their they who all can not but
about into more than other work team teams role company including ability experience years strong across able also
such through using help based must well like make new within what when where while which would should could may any
each both over under per its we us as an of to in on at by or be is a it if so no job jobs position apply applicants
employment equal opportunity employer benefits salary pay range status gender race religion disability veteran
national origin sexual orientation identity visit page total compensation package please these those them was were
been being do does did how why here there then out up off only own same too very just""".split())
_TITLE_FILLER = {"senior", "sr", "lead", "manager", "specialist", "associate", "director", "principal", "staff",
                 "junior", "jr", "of", "the", "and", "for", "i", "ii", "iii", "&"}


def grams(text: Optional[str], n_max: int = 3) -> Set[str]:
    """Words and 2-3 word phrases as written (never joined across a filler word)."""
    w = re.findall(r"[a-z][a-z0-9&+\-]*", (text or "").lower())
    out: Set[str] = set()
    for n in range(1, n_max + 1):
        for i in range(len(w) - n + 1):
            g = w[i:i + n]
            if any(x in _STOP for x in g) or (n == 1 and len(g[0]) < 3):
                continue          # a phrase holds no filler word, at either end or inside
            out.add(" ".join(g))
    return out


class Chewed:
    """Which postings have been chewed: by id, by link, or the same title at the same company."""
    def __init__(self, chews):
        self.ids = {c.job_id for c in chews if c.job_id}
        self.urls = {c.url for c in chews if c.url}
        self.same = {(c.company_id, (c.title or "").strip().lower()) for c in chews}
        self.roles = Counter(r for r in (role_key(c.title) for c in chews) if r)

    def has(self, job) -> bool:
        return (job.id in self.ids or job.url in self.urls
                or (job.company_id, (job.title or "").strip().lower()) in self.same)


def widen_titles(kept: Iterable[Tuple[str, Optional[str]]], want_terms: List[str], window_titles: List[str],
                 all_titles: List[str], removed_titles: List[str], dismissed: Iterable[str] = ()) -> List[Dict]:
    """kept: (title, track) of pursued, saved and chewed postings. Returns up to 8 role
    phrases the Profile doesn't cover, with how many titles each would reach."""
    dismissed = {d.lower() for d in dismissed}
    support: Counter = Counter()
    tracks: Dict[str, Counter] = {}
    for title, track in kept:
        # Runs of words as the title has them: a phrase never joins across a dropped
        # word ("Learning & Development" is not "learning development").
        runs, run = [], []
        for w in role_key(title).split():
            if w in _TITLE_FILLER:
                runs.append(run)
                run = []
            else:
                run.append(w)
        runs.append(run)
        seen = set()
        for words in runs:
            for n in (3, 2, 1):
                for i in range(len(words) - n + 1):
                    p = " ".join(words[i:i + n])
                    if p in seen or any(has_term(p, k) for k in want_terms):
                        continue      # the Profile already searches for this
                    seen.add(p)
                    support[p] += 1
                    tracks.setdefault(p, Counter())[track or "learning"] += 1
    out = []
    # Best backed first, a phrase before a word; reach is counted only for those shown.
    for p, n in sorted(support.items(), key=lambda kv: (-kv[1], -len(kv[0].split()), kv[0])):
        one_word = len(p.split()) == 1
        if p in dismissed or (one_word and (n < 3 or ADJACENT.fullmatch(p))):
            continue              # one word needs three postings behind it, and to be news to Survey
        if any(q != p and has_term(q, p) and support[q] >= n for q in support):
            continue              # "customer" under "customer success": the longer phrase says it better
        bad = sum(1 for t in removed_titles if has_term(t, p))
        if bad >= n:
            continue              # removed at least as often as kept
        all_time = sum(1 for t in all_titles if has_term(t, p))
        if n == 1 and (len(p.split()) != 2 or all_time < 3):
            continue              # backed by one posting: only a two-word role other employers post too
        out.append({"term": p, "support": n, "removed": bad, "track": tracks[p].most_common(1)[0][0],
                    "in_window": sum(1 for t in window_titles if has_term(t, p)), "all_time": all_time})
        if len(out) == 8:
            break
    return out


def doc_freq(texts: Iterable[str]) -> Counter:
    df: Counter = Counter()
    for t in texts:
        df.update(grams(t))
    return df


def language(kept_texts: List[str], background_df: Counter, n_background: int, known: Iterable[str],
             dismissed: Iterable[str] = (), resume_text: str = "") -> Dict:
    """Phrases kept descriptions use far more than descriptions in general."""
    K = len(kept_texts)
    if K < MIN_KEPT:
        return {"ready": False, "have": K, "need": MIN_KEPT, "terms": []}
    skip = {k.lower() for k in known} | {d.lower() for d in dismissed}
    df = doc_freq(kept_texts)
    floor = max(3, math.ceil(0.3 * K))
    rows = []
    for g, k in df.items():
        if k < floor or g in skip or any(has_term(known_term, g) for known_term in skip):
            continue              # already known, or a piece of something known ("adult" in "adult learning")
        b = max(background_df.get(g, 0), k)
        lo = math.log(((k + 0.5) / (K - k + 0.5)) / ((b + 0.5) / (max(n_background, b) - b + 0.5)))
        if lo >= (2.5 if " " not in g else 1.5):     # a single word has to stand out more
            rows.append((lo, k, b, g))
    # A phrase beats a word inside it when nearly as many kept postings use it
    # ("adult learning" over "adult").
    words_in = {g: k for _, k, _, g in rows}
    rows = [r for r in rows if " " in r[3]
            or not any(o != r[3] and has_term(o, r[3]) and ko >= 0.8 * r[1] for o, ko in words_in.items())]
    rows.sort(key=lambda r: (-r[0], len(r[3].split()), r[3]))      # strongest first; the shorter wording on a tie
    picked: List[Dict] = []
    for lo, k, b, g in rows:
        if any(has_term(g, p["term"]) or has_term(p["term"], g) for p in picked):
            continue              # one line per idea: the strongest wording wins
        picked.append({"term": g, "kept": k, "of": K, "everywhere": b, "strength": round(lo, 1),
                       "in_resume": has_term(resume_text, g) if resume_text else None})
        if len(picked) == 12:
            break
    return {"ready": True, "have": K, "need": MIN_KEPT, "read": n_background, "terms": picked}


def features(title: Optional[str], description: Optional[str], location_type: Optional[str] = None) -> Set[str]:
    f = {"role:" + role_key(title), "level:" + level(title)}
    f.update("title:" + w for w in role_key(title).split() if w not in _TITLE_FILLER)
    if location_type:
        f.add("where:" + location_type)
    f.update("work:" + w for w in work_words(description, 30))
    return f


def age_weight(age_days: float) -> float:
    return 0.5 ** (max(0.0, age_days) / HALF_LIFE_DAYS)


class Taste:
    """targets, others: lists of (features, weight). Off (adjust returns 0) until both are big enough."""
    def __init__(self, targets: List[Tuple[Set[str], float]], others: List[Tuple[Set[str], float]]):
        self.have = (len(targets), len(others))
        self.ready = len(targets) >= MIN_TARGETS and len(others) >= MIN_OTHERS
        self.w: Dict[str, float] = {}
        if not self.ready:
            return
        T, O = sum(w for _, w in targets), sum(w for _, w in others)
        t, o, n = Counter(), Counter(), Counter()
        for f, w in targets:
            for x in f:
                t[x] += w
                n[x] += 1
        for f, w in others:
            for x in f:
                o[x] += w
                n[x] += 1
        for x, seen in n.items():
            if seen < 3:
                continue          # a feature needs three postings behind it
            lo = math.log(((t[x] + 2) / (T - t[x] + 2)) / ((o[x] + 2) / (O - o[x] + 2)))
            w = max(-1.0, min(1.0, lo / 3))
            # Level and place say less about a role than its words do.
            self.w[x] = w / 2 if x.startswith(("level:", "where:")) else w

    def adjust(self, title, description, location_type=None) -> Tuple[int, List[str]]:
        """A lift of 5 to MAX_ADJUST for a posting like the pursued ones, or nothing.
        What it shares with chewed postings cancels the lift; it never lowers a score."""
        if not self.ready:
            return 0, []
        hits = sorted((self.w[x] for x in features(title, description, location_type) if x in self.w),
                      key=abs, reverse=True)[:3]
        pts = min(MAX_ADJUST, round(5 * sum(hits)))
        return (pts, ["like postings you pursued"]) if pts >= 5 else (0, [])

    def strongest(self, n: int = 6) -> List[Tuple[str, float]]:
        return [(x, round(w, 2)) for x, w in sorted(self.w.items(), key=lambda kv: -kv[1])[:n] if w > 0]
