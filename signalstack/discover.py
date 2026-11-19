"""Source discovery: the LLM proposes competitors, communities and market watch-items from the company profile;
code then verifies URLs (robots + HTTP) and turns everything into concrete monitorable sources."""
import json
from datetime import date
from urllib.parse import quote_plus
import db, fetch, llm, research

CATS = ["customers", "competitors", "market", "internal"]


def gnews(query):
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"


def hn(query):
    return f"https://hn.algolia.com/api/v1/search_by_date?query={quote_plus(query)}&tags=story&hitsPerPage=15"


def add_source(cid, category, kind, name, url, reason, query="", competitor=None, origin="discovered"):
    key = fetch.norm_url(url)
    exists = db.q("select id from sources where company_id=? and url=?", (cid, key), one=True)
    if exists:
        return None
    return db.x("""insert into sources(company_id,category,kind,name,url,query,reason,status,origin,competitor)
                   values(?,?,?,?,?,?,?,?,?,?)""",
                (cid, category, kind, name, key if kind == "page" else url, query, reason, "pending", origin, competitor))


def verify_page(url):
    s, html, note = fetch.get(url)
    return s == 200, s


def discover(cid, exclude_urls=None, instruction=""):
    """Returns number of new sources added."""
    c = db.q("select * from companies where id=?", (cid,), one=True)
    prof = research.profile_brief(cid)
    existing = db.q("select name,category,url from sources where company_id=? and status!='removed'", (cid,))
    out = llm.ask(f"""You are building a Signal Stack (an intelligence-monitoring source list) for this company.
{prof}

Today's date is {date.today().isoformat()}. Do NOT put years in search queries (no '2024', '2025'); describe the topic so results are always the latest.
Existing sources (do not repeat): {json.dumps([e['name'] for e in existing])[:1500]}
{instruction}

Return JSON:
{{
 "competitors": [{{"name": str, "website": "https://...", "news_query": str (a news-search phrase that cannot be confused with other meanings of the name, e.g. "Noise smartwatch" not "Noise"; include the product category), "why": str (why this is a relevant competitor or substitute for THIS company)}}]  (5-10 real companies/substitutes),
 "customer_queries": [{{"query": str (a short phrase real customers/parents/students/buyers would search or complain about), "why": str}}]  (5-7),
 "customer_pages": [{{"name": str, "url": "https://..." (public forum, Q&A, review or community page where customers discuss these problems), "why": str}}] (0-4, only if you are confident the URL exists),
 "market_queries": [{{"query": str (news-search query about regulation, policy, funding, industry or technology changes that could materially affect this business), "why": str}}] (5-7),
 "market_pages": [{{"name": str, "url": "https://..." (official regulator / government / industry body news or notices page), "why": str}}] (2-5, only confident URLs)
}}
Everything must be specific to this company's real problem space and geography - no generic business news.""", temperature=0.4)
    added = 0
    # competitors: verify site is live, then monitor site + news
    for comp in out.get("competitors", [])[:10]:
        name, site = comp.get("name"), comp.get("website", "")
        if not name:
            continue
        why = comp.get("why", "")
        ok = False
        if site:
            ok, _ = verify_page(site)
            if ok and add_source(cid, "competitors", "page", f"{name} - website", site,
                                 f"Competitor/substitute: {why} Watching their site for pricing, product or positioning changes.", competitor=name):
                added += 1
        nq = (comp.get("news_query") or "").strip() or f'"{name}"'
        if add_source(cid, "competitors", "rss", f"{name} - news", gnews(nq),
                      f"Competitor/substitute: {why} News feed for launches, funding, partnerships, hiring and expansion.",
                      query=nq, competitor=name):
            added += 1
    for it in out.get("customer_queries", [])[:7]:
        if add_source(cid, "customers", "hn", f"HN: {it['query']}", hn(it["query"]), f"Customer voice - {it.get('why','')}", query=it["query"]):
            added += 1
        if add_source(cid, "customers", "rss", f"News/blog mentions: {it['query']}", gnews(it["query"]),
                      f"Customer problem coverage - {it.get('why','')}", query=it["query"]):
            added += 1
    for it in out.get("customer_pages", [])[:4]:
        if it.get("url") and verify_page(it["url"])[0]:
            if add_source(cid, "customers", "page", it["name"], it["url"], f"Customer discussion - {it.get('why','')}"):
                added += 1
    for it in out.get("market_queries", [])[:7]:
        if add_source(cid, "market", "rss", f"Market watch: {it['query']}", gnews(it["query"]), it.get("why", ""), query=it["query"]):
            added += 1
    for it in out.get("market_pages", [])[:5]:
        if it.get("url") and verify_page(it["url"])[0]:
            if add_source(cid, "market", "page", it["name"], it["url"], f"Official/industry source - {it.get('why','')}"):
                added += 1
    if not db.q("select id from sources where company_id=? and category='internal'", (cid,), one=True):
        add_source(cid, "internal", "upload", "Internal uploads (CSV, Excel, PDF, text)", f"internal://company/{cid}",
                   "Founder-provided CRM exports, call transcripts, support conversations and feedback. Analysed on upload.")
        added += 1
    return added
