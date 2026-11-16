"""Checks for chew.py, on made-up postings. No database, no network.

    .venv/bin/python test_chew.py
"""
from types import SimpleNamespace as P

import chew

failed = 0


def check(name, ok, note=""):
    global failed
    failed += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name, note)


# ── Which postings count as chewed ───────────────────────────────────────────
pile = chew.Chewed([P(job_id=1, url="https://a.example/1", company_id=7, title="Customer Education Manager")])
check("the same posting", pile.has(P(id=1, url="x", company_id=0, title="x")))
check("the same link", pile.has(P(id=9, url="https://a.example/1", company_id=0, title="x")))
check("the same title at the same company, reposted", pile.has(P(id=9, url="new", company_id=7, title="customer education manager ")))
check("the same title elsewhere is a different posting", not pile.has(P(id=9, url="new", company_id=8, title="Customer Education Manager")))
check("its role is remembered", pile.roles["customer education manager"] == 1, str(dict(pile.roles)))

# ── Phrases ──────────────────────────────────────────────────────────────────
g = chew.grams("Design the learning experiences for our adult learners")
check("phrases are read as written", "learning experiences" in g and "adult learners" in g)
check("no phrase starts or ends on a filler word", "the learning" not in g and "experiences for" not in g)

# ── Role words ───────────────────────────────────────────────────────────────
want = ["instructional designer", "enablement"]
kept = [("Customer Education Manager", "learning"), ("Senior Customer Education Specialist", "learning"),
        ("Learning & Development Partner", "learning"), ("Field Enablement Lead", "learning"),
        ("Neonatal Follow Up Coordinator", "learning")]
market = ["Customer Education Manager", "Customer Education Lead", "Director, Customer Education", "Software Engineer"]
out = chew.widen_titles(kept, want, market, market, [])
terms = [s["term"] for s in out]
check("a role word two kept titles share is suggested", "customer education" in terms, str(terms))
check("with what it would reach", next(s for s in out if s["term"] == "customer education")["in_window"] == 3)
check("a word inside that phrase is not suggested beside it", "customer" not in terms and "education" not in terms)
check("words the Profile already searches for are not suggested", not any("enablement" in t for t in terms))
check("a phrase never joins across a dropped word", "learning development" not in terms)
check("a one-off title nobody else posts is not suggested", not any("neonatal" in t for t in terms))
check("a dismissed word stays away", "customer education" not in
      [s["term"] for s in chew.widen_titles(kept, want, market, market, [], dismissed=["Customer Education"])])
check("a word removed as often as kept is dropped", "customer education" not in
      [s["term"] for s in chew.widen_titles(kept, want, market, market, ["Customer Education Rep", "Customer Education Intern"])])

# ── The field's language ─────────────────────────────────────────────────────
field = "You will run feedback loops with subject matter experts and design learning experiences for adult learning. "
other = "You will write backend services, own the on-call rotation and review pull requests for the platform. "
background = chew.doc_freq([other * 5] * 200 + [field * 5] * 4)
few = chew.language([field * 5] * 3, background, 204, known=[])
check("it waits for enough kept postings", not few["ready"] and few["have"] == 3 and few["need"] == chew.MIN_KEPT)
lang = chew.language([field * 5] * 9, background, 204, known=["adult learning"])
words = [t["term"] for t in lang["terms"]]
check("then it names what kept postings lean on", lang["ready"] and "feedback loops" in words, str(words))
check("a known term is not suggested, nor a piece of one", "adult learning" not in words and "adult" not in words)
check("one line per idea", len(words) == len(set(words)) and not any(a != b and a in b.split() for a in words for b in words))
check("no more than twelve", len(words) <= 12)

# ── Taste ────────────────────────────────────────────────────────────────────
target = lambda: (chew.features("Learning Experience Designer", field * 5, "remote"), 1.0)
chewed = lambda: (chew.features("Customer Success Manager", other * 5, "remote"), 1.0)
small = chew.Taste([target()] * 2, [chewed()] * 20)
check("off until there is enough of both", not small.ready and small.adjust("Learning Experience Designer", field) == (0, []))
taste = chew.Taste([target()] * chew.MIN_TARGETS, [chewed()] * chew.MIN_OTHERS)
up, why = taste.adjust("Learning Experience Designer", field * 5, "remote")
check("a posting like the pursued ones is lifted, and says so", 5 <= up <= chew.MAX_ADJUST and why == ["like postings you pursued"], f"+{up}")
check("a posting like the chewed ones is not lowered", taste.adjust("Customer Success Manager", other * 5, "remote") == (0, []))
check("an unrelated posting is left alone", taste.adjust("Tax Accountant", "ledgers and filings", None) == (0, []))
check("an old example counts for less", chew.age_weight(chew.HALF_LIFE_DAYS) == 0.5 and chew.age_weight(0) == 1.0)

print("ALL PASSED" if not failed else f"{failed} FAILED")
raise SystemExit(1 if failed else 0)
