# Signal Stack - Technical Documentation

## 1. Overview and architecture
Signal Stack turns any company name + website into a continuously monitored intelligence system and writes a Monday Morning Brief.
Stack: Python 3 / Flask (UI + API), SQLite (storage), APScheduler (automation), Google Gemini free tier (LLM), feedparser + BeautifulSoup + requests (collection), pandas + pypdf (internal uploads).
Choice rationale: a single process with SQLite keeps setup to three commands, needs no paid service and is easy to reproduce for the evaluators.

Pipeline: Company setup -> Research -> Adaptive interview -> Profile -> Source discovery -> Approval -> Crawl -> Analysis & ranking -> Brief -> Audit/Scheduler.
Tables: companies, qa (interview), sources, records (evidence), briefs, actions, runs (logs/failures), changes (context-change log).

## 2. Company understanding and interview
research.py fetches the homepage and up to four key pages (about, pricing, product, blog...) respecting robots.txt. The LLM returns verified_facts (each with the source URL, only things seen in fetched text) and assumptions (inferred) as separate lists, shown separately in the UI.
The interview is generated one question at a time from the research, the profile areas to cover (revenue, segments, problems, differentiation, competitors, market change, risks, growth, internal data, decisions) and all previous answers. Rules in the prompt: no repeated questions, company-specific wording, optional follow-up (max 3) only after a substantive answer (code checks answer length and vague words). A lock prevents duplicate questions. Questions can be skipped and answers edited later; the structured profile is rebuilt from the answers and stays editable.

## 3. Source discovery
discover.py asks the LLM, given the profile and today's date, for: 5-10 competitors/substitutes with a reason, customer-problem queries, market/regulatory queries and official pages. Code then verifies competitor and official URLs (HTTP 200 and robots.txt) before creating sources, creates news-feed and HN-search sources per query, and stores a reason with every source. Nothing is hard-coded: a different company produces different competitors, queries and sources. Sources start as "pending" and must be approved (or added/removed manually) in Source Manager. Selection principle per source: specific (tied to the profile), recent (news feeds limited to last 90 days, pages change-detected), credible (official/regulator pages and known outlets are scored higher).

## 4. Crawling and monitoring
crawler.py handles three source types: RSS/Google News search feeds, Hacker News Algolia API, and web pages (text hash + line diff for change detection). Rules: robots.txt checked for pages, identifiable user agent, per-host rate limit (4 s for Google News), back-off on HTTP 429, no authentication bypass. Each item is hashed (URL + normalised title) and de-duplicated; every record stores title, link, publisher, published date, discovery date and snippet. History is kept in the records table; failures and HTTP status are stored on the source.

## 5. Analysis and ranking
analysis.py sends batches of records plus the company profile to the LLM, which returns: what happened (facts only), evidence strength (does the stored snippet support the claim), relevance, impact, urgency, type (opportunity/threat/neutral), business area, interpretation (labelled), suggested action.
Separate measures are kept: source reliability (code heuristic: government/education domains, known outlets, forums lower), evidence strength (LLM, from stored text only), strategic relevance (LLM vs profile). Verification status is derived from reliability AND evidence strength, never from model confidence: verified / partially verified / unverified.
Ranking score = 100 x (0.30 relevance + 0.25 impact + 0.15 urgency + 0.10 reliability + 0.10 recency (exp decay, 14-day scale) + 0.10 novelty (title similarity vs earlier records)). The feed shows the factors behind every score.

## 6. Brief generation
brief.py selects top records per category (relevance >= 4, inside the lookback window), asks the LLM to write the ten required sections using only those records, and requires every item to cite record ids. Items with no valid id are dropped in code, so nothing can appear without a stored source. Rules in the prompt: evidence vs interpretation separated, unverified items worded as reported, no invented developments, empty categories say so. The Source Appendix is generated from the database (title, link, published and discovery date, snippet, reliability/evidence/relevance, status). Limitations (failing sources, thin customer data) are added automatically. Output is saved as Markdown and email-ready HTML; recommended actions are stored in an action tracker with owner and priority.

## 7. Internal intelligence
internal.py accepts CSV, Excel, PDF and text; it extracts text/statistics (counts for categorical columns) and asks the LLM to report only patterns present in the file (objections, lost-deal reasons, product requests, sales patterns, risks) with a quoted evidence line. Findings enter the same records table, feed and brief.

## 8. Scheduling and reliability
jobs.py defines two independent APScheduler jobs per company: weekly brief (crawl, analyse, brief; day/time/time zone from Settings) and monthly source audit (day of month), plus an optional recurring crawl. Manual triggers use the same code. Every run is logged in the runs table with status, log and error; failures are shown in Runs & Health. A per-company lock prevents overlapping runs of the same kind.
audit.py re-checks every source (dead URL, duplicates, no new records in 45 days, average relevance < 3.5), runs discovery for new candidates, and writes a monthly source-health report with removal/addition recommendations.
Business-context change: the founder describes the change; the LLM reassesses every source (keep/update reason or deactivate), discovery proposes new sources for the new situation, and a "what changed" summary is stored and shown.

## 9. Limitations
Gemini free-tier rate limits and model availability change; Google News gives headlines, not full text (evidence strength capped); JavaScript-heavy pages are not rendered; Reddit/LinkedIn are not crawled; customer voice is approximated by news/HN coverage; email delivery is HTML download only; free hosting may reset SQLite and sleep (schedules only run while the app is running).
