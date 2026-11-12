"""Find a company's website and job board from its name alone.

Used for companies with no career page yet, and for the studio's intake deck
before a company is even added. Nothing here is trusted on its own: the result
carries a status, and Search only scrapes a company once George verifies it.

  status   meaning
  found    a job board answered with postings, and it matches the company
           (linked from the company's own careers page, or its name agrees)
  page     a careers page on the company's website, but no job board behind it
  website  only the website (a near guess; check the name it matched)
  none     nothing found

Order of evidence, strongest first:
  1. the company's own careers page links to a job board (Workday, Greenhouse,
     Lever, Ashby, SmartRecruiters)
  2. a job board with the company's name as its address, whose name agrees
  3. the careers page itself
"""
import re
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

import requests

logger = logging.getLogger(__name__)

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
}
TIMEOUT = 8

# The same normalisation as the studio's companyKey (src/studio/tools/intake.ts),
# so a name looked up from the intake deck finds its cached result here.
_STOP = r"\b(inc|llc|ltd|corp|corporation|co|company|the|plc|gmbh|group|us|usa)\b"


def company_key(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[®™.,'’\"]", "", s)
    s = re.sub(_STOP, " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _words(name: str) -> List[str]:
    return [w for w in company_key(name).split() if w != "and"]


def name_agrees(a: str, b: str) -> bool:
    """Do two company names plausibly mean the same company?
    'Insight Software' vs 'insightsoftware' agree; 'GE Aerospace' vs 'Lexington Menus' don't."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    ca, cb = "".join(wa), "".join(wb)
    if ca == cb or ca in cb or cb in ca:
        return True
    shared = set(wa) & set(wb)
    # Every word of the shorter name appears in the longer one.
    return len(shared) >= min(len(set(wa)), len(set(wb)))


def _slugs(name: str, domain: Optional[str]) -> List[str]:
    w = _words(name)
    out = []
    if domain:
        out.append(domain.split(".")[0])
    if w:
        out += ["".join(w), "-".join(w)]
    seen, res = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res[:3]


# ── Website ──────────────────────────────────────────────────────────────────

def find_website(name: str) -> Tuple[Optional[str], Optional[str], bool]:
    """(domain, the name it matched, whether that name agrees with ours).
    Picks the best-agreeing of Clearbit's suggestions, not just the first."""
    try:
        r = requests.get(
            f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote_plus(name)}",
            headers=UA, timeout=TIMEOUT)
        sugg = r.json() if r.ok else []
    except Exception as e:
        logger.warning(f"website lookup failed for {name}: {e}")
        return None, None, False
    if not sugg:
        return None, None, False
    agreeing = [s for s in sugg[:5] if s.get("domain") and name_agrees(name, s.get("name", ""))]
    # Among the agreeing ones, a domain that is the name itself wins (anthropic.com over anthropics.com).
    compact = "".join(_words(name))
    for s in agreeing:
        if s["domain"].split(".")[0] == compact:
            return s["domain"], s.get("name"), True
    if agreeing:
        return agreeing[0]["domain"], agreeing[0].get("name"), True
    top = sugg[0]
    return top.get("domain"), top.get("name"), False


# ── Job boards ───────────────────────────────────────────────────────────────

ATS_LINKS = [
    (r"https?://(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", "greenhouse",
     lambda m: f"https://boards.greenhouse.io/{m.group(1)}"),
    (r"https?://boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", "greenhouse",
     lambda m: f"https://boards.greenhouse.io/{m.group(1)}"),
    (r"https?://jobs\.lever\.co/([A-Za-z0-9_-]+)", "lever",
     lambda m: f"https://jobs.lever.co/{m.group(1)}"),
    (r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)", "ashby",
     lambda m: f"https://jobs.ashbyhq.com/{m.group(1)}"),
    (r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", "workday",
     lambda m: f"https://{m.group(1)}/{m.group(2)}"),
    (r"https?://(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", "smartrecruiters",
     lambda m: f"https://careers.smartrecruiters.com/{m.group(1)}"),
]
_NOT_A_BOARD = {"embed", "api", "v1", "jobs", "job", "search", "login", "careers", "en-us", "wday"}


def board_jobs(platform: str, url: str) -> Tuple[Optional[int], Optional[str]]:
    """Ask a job board how many postings it has. (count or None, the board's own name if it gives one)."""
    p = urlparse(url)
    parts = [x for x in p.path.strip("/").split("/") if x]
    slug = parts[0] if parts else ""
    try:
        if platform == "greenhouse":
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", headers=UA, timeout=TIMEOUT)
            if not r.ok:
                return None, None
            bname = r.json().get("name")
            j = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", headers=UA, timeout=TIMEOUT)
            return (len(j.json().get("jobs", [])) if j.ok else None), bname
        if platform == "lever":
            r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", headers=UA, timeout=TIMEOUT)
            return (len(r.json()) if r.ok and isinstance(r.json(), list) else None), None
        if platform == "ashby":
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", headers=UA, timeout=TIMEOUT)
            return (len(r.json().get("jobs", [])) if r.ok else None), None
        if platform == "workday":
            tenant = (p.hostname or "").split(".")[0]
            r = requests.post(f"https://{p.hostname}/wday/cxs/{tenant}/{slug}/jobs",
                              json={"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}},
                              headers={**UA, "Content-Type": "application/json"}, timeout=TIMEOUT)
            return (r.json().get("total") if r.ok else None), None
        if platform == "smartrecruiters":
            r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
                             headers=UA, timeout=TIMEOUT)
            return (r.json().get("totalFound") if r.ok else None), None
    except Exception as e:
        logger.info(f"board check failed {url}: {e}")
    return None, None


def board_titles(platform: str, url: str, cap: int = 400) -> Optional[List[str]]:
    """A board's posting titles (up to cap), so a match against the profile can be counted.
    Workday and SmartRecruiters page; a few pages are enough for a fair count."""
    p = urlparse(url)
    parts = [x for x in p.path.strip("/").split("/") if x and not re.fullmatch(r"[a-z]{2}(-[A-Za-z]{2})?", x)]
    slug = parts[0] if parts else ""
    try:
        if platform == "greenhouse":
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", headers=UA, timeout=TIMEOUT)
            return [j.get("title", "") for j in r.json().get("jobs", [])][:cap] if r.ok else None
        if platform == "lever":
            r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", headers=UA, timeout=TIMEOUT)
            return [j.get("text", "") for j in r.json()][:cap] if r.ok and isinstance(r.json(), list) else None
        if platform == "ashby":
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", headers=UA, timeout=TIMEOUT)
            return [j.get("title", "") for j in r.json().get("jobs", [])][:cap] if r.ok else None
        if platform == "workday":
            tenant = (p.hostname or "").split(".")[0]
            out: List[str] = []
            for offset in range(0, 100, 20):
                r = requests.post(f"https://{p.hostname}/wday/cxs/{tenant}/{slug}/jobs",
                                  json={"limit": 20, "offset": offset, "searchText": "", "appliedFacets": {}},
                                  headers={**UA, "Content-Type": "application/json"}, timeout=TIMEOUT)
                if not r.ok:
                    break
                page = [j.get("title", "") for j in r.json().get("jobPostings", [])]
                out += page
                if len(page) < 20:
                    break
            return out or None
        if platform == "smartrecruiters":
            r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100", headers=UA, timeout=TIMEOUT)
            return [j.get("name", "") for j in r.json().get("content", [])] if r.ok else None
    except Exception as e:
        logger.info(f"board titles failed {url}: {e}")
    return None


def boards_on_page(html: str) -> List[Tuple[str, str]]:
    out, seen = [], set()
    for pattern, platform, to_root in ATS_LINKS:
        for m in re.finditer(pattern, html, re.I):
            root = to_root(m)
            slug = urlparse(root).path.strip("/").split("/")[0].lower()
            if slug in _NOT_A_BOARD or root in seen:
                continue
            seen.add(root)
            out.append((platform, root))
    return out


def find_careers_page(domain: str) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    """(a careers page that answered, the job boards it links to)."""
    candidates = [f"https://{domain}/careers", f"https://www.{domain}/careers", f"https://careers.{domain}",
                  f"https://{domain}/jobs", f"https://jobs.{domain}", f"https://{domain}/about/careers",
                  f"https://{domain}/company/careers"]
    first_ok = None
    for url in candidates:
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            continue
        # A redirect home (careers → /) isn't a careers page.
        path = urlparse(r.url).path.strip("/")
        on_site = domain.split(".")[-2] in (urlparse(r.url).hostname or "")
        if on_site and not path and not (urlparse(r.url).hostname or "").startswith(("careers.", "jobs.")):
            continue
        boards = boards_on_page(r.text)
        if boards:
            return r.url, boards
        first_ok = first_ok or r.url
    return first_ok, []


# ── All together ─────────────────────────────────────────────────────────────

def discover(name: str) -> Dict:
    t0 = time.time()
    res: Dict = {"name": name, "key": company_key(name), "website": None, "website_name": None,
                 "website_agrees": False, "career_url": None, "platform": None, "jobs": None,
                 "board_name": None, "status": "none", "evidence": None,
                 "checked_at": datetime.utcnow().isoformat(timespec="seconds")}
    domain, matched, agrees = find_website(name)
    if domain:
        res.update(website=f"https://{domain}", website_name=matched, website_agrees=agrees, status="website")

    # 1. The company's own careers page, and the boards it links to.
    page, linked = (find_careers_page(domain) if domain and agrees else (None, []))
    for platform, url in linked:
        n, bname = board_jobs(platform, url)
        if n is not None:
            res.update(career_url=url, platform=platform, jobs=n, board_name=bname,
                       status="found", evidence="linked from the company's careers page")
            break

    # 2. A board at the company's own name, whose name agrees.
    if res["status"] != "found":
        for slug in _slugs(name, domain if agrees else None):
            for platform, url in (("greenhouse", f"https://boards.greenhouse.io/{slug}"),
                                  ("lever", f"https://jobs.lever.co/{slug}"),
                                  ("ashby", f"https://jobs.ashbyhq.com/{slug}")):
                n, bname = board_jobs(platform, url)
                if n is None:
                    continue
                # Greenhouse names its board; Lever and Ashby don't, so the address must be the name itself.
                ok = name_agrees(name, bname) if bname else (slug == "".join(_words(name)) or
                                                              (agrees and domain and slug == domain.split(".")[0]))
                if ok and n > 0:
                    res.update(career_url=url, platform=platform, jobs=n, board_name=bname,
                               status="found", evidence="a job board under the company's name")
                    break
            if res["status"] == "found":
                break

    # The postings' titles, so intake can say how many match the profile.
    if res["status"] == "found":
        res["titles"] = board_titles(res["platform"], res["career_url"])

    # 3. The careers page alone.
    if res["status"] != "found" and page:
        res.update(career_url=page, platform="page", status="page", evidence="careers page on the company's website")

    res["seconds"] = round(time.time() - t0, 1)
    return res
