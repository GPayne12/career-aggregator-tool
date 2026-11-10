# career-aggregator-tool
Repo for targeted company career website scraping with AI-assisted application status features

## Career pages: lookup and verification

Search finds each company's website and job board by name, and only scrapes a
company once its career page has been verified by hand.

- **Lookup** (`discover.py`): the website comes from Clearbit's company
  autocomplete, preferring a name that agrees with ours. The job board comes
  from, in order: a board linked from the company's own careers page (Workday,
  Greenhouse, Lever, Ashby, SmartRecruiters), then a board under the company's
  name whose name agrees, then the careers page alone. Results are cached in the
  `discoveries` table by normalised name, so names can be looked up before the
  company is added (`POST /api/discovery/lookup`, `POST /api/discovery/run`).
- **Status** (`companies.url_status`): `found` (a board answered and matches),
  `page`, `website`, `none`, `unverified` (typed in), and `verified`. Only
  `verified` companies are scraped. Changing a career page sets it back to
  `unverified`.
- **Companies page** (`/companies/manage`): edit the website and career page in
  place, **Check** runs a trial scrape (first pages only, nothing saved) and shows
  sample postings, **Verify** confirms the page.
- **Closed postings:** when a board answers, its postings missing from the latest
  scrape are marked inactive.
- **Workday links** are built as `/{board}/job/…`; without the board segment
  they don't open.
- **Browser:** careers pages are read with the installed Chrome
  (`channel="chrome"`), falling back to Playwright's own Chromium.
