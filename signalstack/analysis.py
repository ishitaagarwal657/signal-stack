"""Intelligence analysis + ranking.
Separate measures (never merged into one 'confidence'):
  - source reliability  (code: domain/kind heuristic)
  - evidence strength   (LLM: does the stored snippet itself support the stated fact? 0-10)
  - strategic relevance (LLM: 0-10 against the company profile)
Verification status is derived from reliability + evidence strength, not from model confidence."""
import json, math, re
from datetime import datetime, timezone
import db, llm, fetch, research

HIGH_NEWS = ("reuters", "bloomberg", "economictimes", "thehindu", "timesofindia", "livemint", "techcrunch", "ndtv",
             "indianexpress", "bbc", "hindustantimes", "business-standard", "financialexpress", "forbes", "wsj", "ft.com",
             "theguardian", "guardian", "abcnews", "abc news", "icefmonitor", "icef monitor", "entrackr", "openmagazine", "open magazine", "thelogic", "the logic", "outlook", "newlandchase", "thewire", "houseofcommons", "commonslibrary", "telanganatoday", "theprint", "moneycontrol", "inc42", "yourstory", "edtechreview", "insidehighered", "timeshighereducation", "pti")


def reliability(rec, src):
    host = fetch.host(rec["url"] or "")
    pub = (rec["publisher"] or "").lower()
    if rec["kind"] == "internal":
        return 0.7
    if host.endswith((".gov", ".gov.in", ".edu", ".ac.in", ".nic.in")) or ".gov." in host or ".edu." in host:
        return 0.95
    if rec["kind"] == "page_change":
        return 0.9 if src and src["category"] == "competitors" else 0.75
    if any(n in host or n.replace(" ", "") in pub.replace(" ", "") for n in HIGH_NEWS):
        return 0.8
    if rec["kind"] == "discussion":
        return 0.4
    return 0.55


def _recency(pub, disc):
    d = pub or disc
    try:
        dt = datetime.fromisoformat(d)
        days = max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        days = 30
    return math.exp(-days / 14)


def _tokens(s):
    return set(re.findall(r"[a-z0-9]{4,}", (s or "").lower()))


def _novelty(rec):
    others = db.q("select title from records where company_id=? and id<? order by id desc limit 300", (rec["company_id"], rec["id"]))
    a = _tokens(rec["title"])
    for o in others:
        b = _tokens(o["title"])
        if a and b and len(a & b) / len(a | b) > 0.6:
            return 0.3
    return 1.0


def score(rel, imp, urg, cred, rec_, nov):
    s = 100 * (0.30 * rel / 10 + 0.25 * imp / 10 + 0.15 * urg / 10 + 0.10 * cred + 0.10 * rec_ + 0.10 * nov)
    return round(s, 1)


def explain(r):
    return (f"relevance {r['relevance']:.0f}/10, impact {r['impact']:.0f}/10, urgency {r['urgency']:.0f}/10, "
            f"source reliability {r['credibility']:.2f}, recency {r['recency']:.2f}, novelty {r['novelty']:.1f}")


def analyze_company(cid, log=print):
    st = db.settings(cid)
    # round-robin across sources (newest first inside each source) so every category/competitor gets analysed,
    # not just the sources that publish most often
    todo = db.q("""select * from (
                     select r.*, row_number() over (partition by source_id order by coalesce(published, discovered) desc) rn
                     from records r where company_id=? and analysis is null and kind!='internal')
                   order by rn, coalesce(published, discovered) desc limit ?""", (cid, int(st["max_analyze"])))
    if not todo:
        return 0
    prof = research.profile_brief(cid)
    done = 0
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        items = [{"id": r["id"], "category": r["category"], "competitor": r["competitor"], "title": r["title"],
                  "publisher": r["publisher"], "published": r["published"], "snippet": r["snippet"]} for r in batch]
        try:
            out = llm.ask(f"""You are a strategic intelligence analyst for the company below.
{prof}

Analyse each collected item. Be strict: most generic news is NOT relevant. Score only what the title/snippet support.
Return JSON {{"items": [{{
 "id": int,
 "what_happened": str (facts ONLY, restated from the title/snippet - no speculation),
 "evidence_strength": 0-10 (does the title/snippet itself clearly support that fact? a bare headline = 4-6, detailed official text = 8-10),
 "relevance": 0-10 (to THIS company's business, customers, competitors, market),
 "impact": 0-10 (potential effect on revenue, customers, positioning or operations),
 "urgency": 0-10,
 "type": "opportunity"|"threat"|"neutral",
 "business_area": str (e.g. Sales, Product, Pricing, Admissions/Regulation, Marketing, Partnerships, Operations),
 "why_it_matters": str (our INTERPRETATION, specific to this company; start with 'Interpretation:'),
 "suggested_action": str (concrete, or "" if none)
}}]}}
Items:
{json.dumps(items)}""", temperature=0.2)
        except llm.LLMError as e:
            log(f"analysis batch failed: {e}")
            continue
        by_id = {int(o["id"]): o for o in out.get("items", []) if "id" in o}
        for r in batch:
            o = by_id.get(r["id"])
            if not o:
                continue
            src = db.q("select * from sources where id=?", (r["source_id"],), one=True)
            cred = reliability(r, src)
            rec_ = _recency(r["published"], r["discovered"])
            nov = _novelty(r)
            rel, imp, urg = (float(o.get(k, 0) or 0) for k in ("relevance", "impact", "urgency"))
            ev = float(o.get("evidence_strength", 0) or 0)
            verified = "verified" if (cred >= 0.75 and ev >= 6) else ("partially verified" if ev >= 5 else "unverified")
            db.x("""update records set analysis=?, relevance=?, impact=?, urgency=?, credibility=?, recency=?, novelty=?,
                    evidence_strength=?, score=?, verified=? where id=?""",
                 (json.dumps(o), rel, imp, urg, cred, rec_, nov, ev, score(rel, imp, urg, cred, rec_, nov), verified, r["id"]))
            done += 1
        log(f"analysed {done}/{len(todo)}")
    return done
