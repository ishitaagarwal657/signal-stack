"""Initial company research + adaptive interview + profile building."""
import json, re, threading
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import db, fetch, llm

AREAS = [
    "primary business lines and revenue streams", "highest-value customer segments",
    "current customer problems and unmet needs", "why customers choose this company over alternatives",
    "important competitors or substitutes", "major industry/market/regulatory changes",
    "developments that could materially affect the business", "growth priorities for the next 6-12 months",
    "internal business information that should influence decisions", "decisions the intelligence system should help the founder make",
]


def research_company(cid):
    c = db.q("select * from companies where id=?", (cid,), one=True)
    url = c["url"] if c["url"].startswith("http") else "https://" + c["url"]
    pages, notes = [], []
    status, html, note = fetch.get(url)
    if status != 200:
        notes.append(f"Homepage fetch failed: {status} {note}")
    else:
        pages.append((url, fetch.clean_text(html, 5000)))
        soup = BeautifulSoup(html, "html.parser")
        wanted = ("about", "pricing", "product", "service", "program", "blog", "news", "student", "partner", "how")
        seen = {url}
        for a in soup.find_all("a", href=True):
            link = urljoin(url, a["href"]).split("#")[0]
            if urlparse(link).netloc.removeprefix("www.") != urlparse(url).netloc.removeprefix("www."):
                continue
            if link in seen or not any(w in link.lower() for w in wanted):
                continue
            seen.add(link)
            s, hh, _ = fetch.get(link)
            if s == 200:
                pages.append((link, fetch.clean_text(hh, 3500)))
            if len(pages) >= 5:
                break
    corpus = "\n\n".join(f"### {u}\n{t}" for u, t in pages) or "(no page content could be fetched)"
    out = llm.ask(f"""Company: {c['name']} ({url}).
Below is text fetched from the company's own public website. Produce a research profile as JSON with keys:
summary (3-4 sentences), products_services (list), customer_segments (list), positioning (string),
geography (string), business_model_guess (string),
verified_facts (list of objects {{"fact": str, "source_url": str}} - ONLY facts stated in the fetched text),
assumptions (list of strings - anything you infer or know from general knowledge but did NOT see in the text),
open_questions (list of things worth asking the founder).
Never invent facts. If the text is empty, say so in summary and put everything under assumptions.

{corpus}""")
    out["pages_fetched"] = [u for u, _ in pages]
    out["fetch_notes"] = notes
    db.x("update companies set research=? where id=?", (json.dumps(out), cid))
    return out


def qa(cid):
    return db.q("select * from qa where company_id=? order by seq", (cid,))


def main_count(rows):
    return sum(1 for r in rows if r["kind"] == "main")


_qlock = threading.Lock()
VAGUE = {"idk", "i dont know", "i don't know", "dont know", "don't know", "na", "no", "nice", "it is nice", "ok", "okay", "nothing", "-", "none"}


def substantive(ans):
    a = (ans or "").strip().lower().strip(".! ")
    return len(a) >= 25 and a not in VAGUE


def next_question(cid):
    with _qlock:  # stops two simultaneous page loads from inserting duplicate questions
        return _next_question(cid)


def _next_question(cid):
    """Returns the next unanswered question row (creating one with the LLM if needed), or None when the interview is complete."""
    rows = qa(cid)
    pending = [r for r in rows if r["answer"] is None and not r["skipped"]]
    if pending:
        return pending[0]
    if main_count(rows) >= 10 and not _wants_followup(rows):
        return None
    c = db.q("select * from companies where id=?", (cid,), one=True)
    research = db.jl(c["research"], {})
    history = "\n".join(
        f"Q{r['seq']} [{r['kind']}/{r['area']}]: {r['question']}\nA: {'(skipped)' if r['skipped'] else r['answer']}" for r in rows) or "(none yet)"
    n_main = main_count(rows)
    n_follow = sum(1 for r in rows if r["kind"] == "followup")
    can_follow = bool(rows) and n_follow < 3 and not rows[-1]["skipped"] and rows[-1]["kind"] == "main" and substantive(rows[-1]["answer"])
    out = llm.ask(f"""You are an expert business-intelligence analyst interviewing the founder of {c['name']} ({c['url']}).
Research so far (verified facts and assumptions): {json.dumps({k: research.get(k) for k in ['summary','products_services','customer_segments','positioning','verified_facts','open_questions']})[:3500]}
Coverage areas for the 10 main questions (examples, adapt wording to THIS business and industry): {AREAS}
Interview so far:
{history}

Main questions asked: {n_main}/10. Follow-ups used: {n_follow}/3.
Return JSON {{"kind": "main"|"followup", "area": str, "question": str}}.
Rules: ask ONE question. Never repeat or rephrase an earlier question. Make it specific to this company's industry, products and the research above (mention concrete things such as its segments, geography or products).
{"You MAY ask a follow-up (kind=followup) if the last answer was vague, surprising or opened an important new thread; otherwise ask the next main question." if can_follow else "Ask the next main question (kind=main)."}
Choose the coverage area not yet well covered. Keep the question under 45 words.""", temperature=0.5)
    kind = out.get("kind", "main")
    if kind == "followup" and not can_follow:
        kind = "main"
    if kind == "main" and n_main >= 10:
        return None
    seq = (rows[-1]["seq"] + 1) if rows else 1
    qid = db.x("insert into qa(company_id,seq,kind,area,question) values(?,?,?,?,?)",
               (cid, seq, kind, out.get("area", ""), out["question"].strip()))
    return db.q("select * from qa where id=?", (qid,), one=True)


def _wants_followup(rows):
    return False


def build_profile(cid):
    c = db.q("select * from companies where id=?", (cid,), one=True)
    rows = qa(cid)
    research = db.jl(c["research"], {})
    transcript = "\n".join(
        f"Q: {r['question']}\nA: {'(skipped - confidential)' if r['skipped'] else (r['answer'] or '(unanswered)')}" for r in rows)
    out = llm.ask(f"""Build a structured company intelligence profile for {c['name']} from the founder interview and research.
Use the founder's own answers as the source of truth; label anything from research only as such. Do not invent details; use "" for unknown.
Return JSON with these keys (string values, use newline-separated bullets for lists):
business_lines, revenue_streams, top_customer_segments, customer_problems, why_customers_choose_us, named_competitors,
market_changes, material_risks, growth_priorities, internal_information, decisions_to_support, geography_focus, confidential_or_skipped.
Research summary: {research.get('summary')}
Interview:
{transcript}""")
    out = {k: ("\n".join(v) if isinstance(v, list) else v) for k, v in out.items()}
    db.x("update companies set profile=? where id=?", (json.dumps(out), cid))
    return out


def profile_brief(cid):
    """Compact text version of everything known about the company, for use in prompts."""
    c = db.q("select * from companies where id=?", (cid,), one=True)
    r = db.jl(c["research"], {})
    p = db.jl(c["profile"], {})
    parts = [f"Company: {c['name']} ({c['url']})", f"Summary: {r.get('summary','')}",
             f"Products: {r.get('products_services','')}"]
    for k, v in p.items():
        if v:
            parts.append(f"{k}: {v}")
    if c.get("context_note"):
        parts.append(f"LATEST BUSINESS CONTEXT UPDATE (overrides older info): {c['context_note']}")
    return "\n".join(parts)[:6000]
