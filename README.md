# Signal Stack - Company Intelligence & Monday Morning Brief

Enter a company name + website. The app researches it, interviews the founder (10 adaptive questions + follow-ups),
discovers and justifies sources in four categories (Customers, Competitors, Market, Internal), monitors them,
analyses what matters, ranks signals and writes a Monday Morning Brief. No competitors, sources or questions are hard-coded.

## Run locally (Windows / Mac / Linux)

1. Python 3.10+. `python -m venv venv`, activate it, then `pip install -r requirements.txt`
2. Free Gemini key: https://aistudio.google.com/apikey . Copy `.env.example` to `.env` and paste the key.
3. `python app.py` and open http://localhost:5000

The scheduler starts with the app, so keep it running for scheduled jobs. Deployment: `Procfile` works on Render/Railway free tiers
(single worker; SQLite file lives on the instance - free tiers may wipe it on redeploy, so demo locally if that matters).

## Reproduce the demo

1. Home: add `MeetUniversity` / `https://www.meetuniversity.com`. Research runs (verified facts vs assumptions shown).
2. Interview tab: answer 10 questions (skip allowed). Follow-ups appear when answers are vague. Edit answers later from Profile.
3. Signal Stack -> "Discover sources with AI". Review reasons in Source Manager, approve (all or individually), add/remove your own.
4. Monday Brief -> "Generate brief now" (crawl, analyse, brief). Watch progress in Runs & Health.
5. Intelligence Feed: filter by category, competitor, date, relevance, impact, verification.
6. Internal tab: upload CSV / Excel / PDF / text. Findings join the feed and the brief.
7. Stress test: add 2 other companies from different industries (use "Auto-draft assumed answers" to save time; these are flagged AI-assumed).
8. Context change: Profile -> "Business context changed?" -> describe the change. Sources are re-scored, stale ones deactivated, new ones proposed, and "what changed" is logged.
9. Monthly audit: Monday Brief -> "Run monthly source audit" (dead URLs, duplicates, inactive, declining relevance, new discoveries).

## Architecture

| Module | Role |
|---|---|
| `research.py` | Fetches homepage + key pages (robots-aware), LLM builds profile with verified_facts (with source URL) separate from assumptions; adaptive interview; profile builder |
| `discover.py` | LLM proposes 5-10 competitors, customer-voice queries, market/regulatory sources from the profile; code verifies URLs (HTTP + robots), creates sources with reasons |
| `crawler.py` | Visits approved sources: RSS (Google News search feeds), Hacker News API, web pages with change detection (hash + line diff). De-duplicates, stores evidence + links + discovery date |
| `analysis.py` | LLM scores relevance/impact/urgency/evidence strength from stored evidence; code computes source reliability, recency, novelty; ranking score |
| `brief.py` | LLM writes brief only from stored records; every item must cite real record ids (uncited items are dropped); appendix built from DB |
| `audit.py` | Monthly health audit + context-change reassessment |
| `internal.py` | CSV/Excel/PDF/text -> objections, lost-deal reasons, requests, patterns |
| `jobs.py` | APScheduler: weekly brief, monthly audit, optional frequent crawl; every run logged (failures visible in dashboard) |

Storage: SQLite (`signalstack.db`, schema in `db.py`). Tables: companies, qa, sources, records, briefs, actions, runs, changes.

### Ranking
`score = 100 * (0.30 relevance + 0.25 impact + 0.15 urgency + 0.10 source reliability + 0.10 recency + 0.10 novelty)`.
Three measures are kept separate and never merged: source reliability (code heuristic: gov/edu, known outlets, discussion forum, page type),
evidence strength (does the stored snippet itself support the claim, 0-10), strategic relevance (0-10 vs company profile).
Verification status = reliability AND evidence strength. An AI confidence score is never used as proof. Evidence ("what happened") and interpretation ("Interpretation: ...") are stored and shown separately.

### Respecting access rules
robots.txt checked for every web page fetch, 1.5 s per-host gap, identifiable user agent, no login/auth bypass. RSS and HN API are published machine-readable endpoints.

### Honesty rules
No seed data. If a category has no records, the brief says so. Failing sources are listed under Limitations. Internal findings only come from uploaded files.

## Cost statement
Everything is free/open source: Flask, SQLite, APScheduler, feedparser, BeautifulSoup, pandas. AI: Google Gemini free tier (AI Studio key).
Google News RSS and Hacker News Algolia API need no key. No paid APIs or scraping platforms.

Free-tier limitations: Gemini free tier is rate limited (~15 requests/min, daily caps; the client throttles and retries; quotas and model names change, set `GEMINI_MODEL` if needed).
Google News RSS gives headline + link + publisher, not full article text, so evidence strength for those items is capped (bare headlines score 4-6). Page change detection cannot read JavaScript-rendered pages.
Free hosting may sleep or reset SQLite; scheduled jobs only run while the app is awake.

## Known limitations
- Reddit/LinkedIn are not crawled (terms and auth); customer voice comes from HN, news/blog discussion feeds and any customer pages the AI proposes that pass robots checks.
- Email delivery is "email-ready HTML download" (no SMTP configured).
## Live demo (bonus)
https://signal-stack-b5yp.onrender.com
- Render free plan par hai: pehli request 50 second tak le sakti hai, aur service idle mein sleep hoti hai.
- Free plan par persistent disk nahi hai, isliye live app mein naya data restart par reset ho jata hai.
- Scheduler sleep ke dauran nahi chalta.
- Asli demo, screenshots aur sample briefs local run se bane hain (video aur samples/reports).
