"""Monday Morning Brief: LLM writes the analysis from ranked records; the source appendix and all links come from the DB."""
import json, html as H
from datetime import datetime, timezone, timedelta
import db, llm, research, analysis

CAT_TITLES = [("customers", "Customer Signals"), ("competitors", "Competitor Intelligence"),
              ("market", "Market Intelligence"), ("internal", "Internal Business Intelligence")]


def pick_records(cid):
    st = db.settings(cid)
    since = (datetime.now(timezone.utc) - timedelta(days=int(st["lookback_days"]))).isoformat()
    rows = db.q("""select * from records where company_id=? and analysis is not null and relevance>=4
                   and (coalesce(published, discovered) >= ? or kind='internal') order by score desc""", (cid, since))
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], [])
        if len(by_cat[r["category"]]) < 8:
            by_cat[r["category"]].append(r)
    return by_cat


def _compact(r):
    a = db.jl(r["analysis"], {})
    return {"id": r["id"], "category": r["category"], "competitor": r["competitor"], "title": r["title"],
            "published": r["published"], "publisher": r["publisher"], "evidence": a.get("what_happened"),
            "interpretation": (a.get("why_it_matters") or "").replace("Interpretation:", "").strip(), "type": a.get("type"), "area": a.get("business_area"),
            "score": r["score"], "verified": r["verified"], "suggested_action": a.get("suggested_action")}


def generate(cid, log=print):
    c = db.q("select * from companies where id=?", (cid,), one=True)
    by_cat = pick_records(cid)
    flat = [r for v in by_cat.values() for r in v]
    valid = {r["id"]: r for r in flat}
    prof = research.profile_brief(cid)
    data = {}
    if flat:
        data = llm.ask(f"""Write the content of a Monday Morning Intelligence Brief for the founder of this company.
{prof}

Use ONLY the records below (each has an id). Every claim must cite record ids. Separate EVIDENCE (what the source says) from INTERPRETATION (what it means for us).
Do not manufacture developments. If a category has no records, set "status":"none". Be specific to this company, not generic strategy advice.
Rules: (1) Do NOT write record ids inside sentences; put them only in the "ids" arrays. (2) Each item's ids must be only records that directly support what that item says. (3) If a record's verified status is "unverified", word the claim as reported/unconfirmed, never as established fact. (4) Give 5 executive_summary items whenever at least 5 records exist. (6) The customers section must contain ONLY records that show customer problems, complaints, questions, expectations or sentiment; a company's own news or a competitor/regulatory story does NOT belong there - if no record qualifies set customers status to none. (7) Do not repeat the same record as the main evidence in more than one category section. (5) Give at least 3 opportunities: an opportunity may arise from a threat or competitor move (e.g. a competitor expanding into test prep, rising demand for Germany guides, a regulation that students need help with), but each must be backed by cited records and a concrete next step.

Records (ranked): {json.dumps([_compact(r) for r in flat])}

Return JSON:
{{
 "executive_summary": [{{"headline": str, "so_what": str, "ids": [int]}}] (up to 5, most important first),
 "customers": {{"status":"ok"|"none","items":[{{"evidence": str, "interpretation": str, "implication": str, "ids":[int]}}]}},
 "competitors": same shape, "market": same shape, "internal": same shape,
 "opportunities": [{{"title": str, "evidence": str, "impact": str, "next_step": str, "priority": "High"|"Medium"|"Low", "ids":[int]}}] (aim for at least 3 ONLY if the evidence supports them; otherwise fewer),
 "risks": [{{"title": str, "possible_impact": str, "urgency": "High"|"Medium"|"Low", "ids":[int]}}],
 "actions": [{{"action": str, "reason": str, "owner": str (a role, e.g. Founder, Head of Sales, Marketing), "priority": "High"|"Medium"|"Low", "ids":[int]}}],
 "watch": [{{"item": str, "why": str, "ids":[int]}}],
 "limitations": [str] (data gaps honestly stated, e.g. few customer-voice sources, feeds that failed)
}}""", temperature=0.3)
    # drop anything that cites no real record -> keeps the brief traceable
    def keep(lst):
        out = []
        for it in lst or []:
            it["ids"] = [i for i in it.get("ids", []) if i in valid]
            if it["ids"]:
                out.append(it)
        return out
    for k in ("executive_summary", "opportunities", "risks", "actions", "watch"):
        data[k] = keep(data.get(k))
    for k, _ in CAT_TITLES:
        sec = data.get(k) or {}
        sec["items"] = keep(sec.get("items"))
        if not sec["items"]:
            sec["status"] = "none"
        data[k] = sec
    cited = sorted({i for k in ("executive_summary", "opportunities", "risks", "actions", "watch") for it in data[k] for i in it["ids"]} |
                   {i for k, _ in CAT_TITLES for it in data[k]["items"] for i in it["ids"]})
    appendix = [valid[i] for i in cited]
    failed = db.q("select name,last_http from sources where company_id=? and status='approved' and fail_count>0", (cid,))
    data["limitations"] = list(data.get("limitations") or []) + [f"Source currently failing: {f['name']} ({f['last_http']})" for f in failed]
    if not flat:
        data["limitations"].append("No analysed records met the relevance threshold in the lookback window. Run a refresh after approving sources.")
    md = render_md(c, data, appendix)
    page = render_html(c, data, appendix)
    bid = db.x("insert into briefs(company_id,kind,created,markdown,html,data) values(?,?,?,?,?,?)",
               (cid, "weekly", datetime.now(timezone.utc).isoformat(timespec="seconds"), md, page, json.dumps(data)))
    for a in data["actions"]:
        db.x("insert into actions(company_id,brief_id,text,reason,owner,priority) values(?,?,?,?,?,?)",
             (cid, bid, a.get("action"), a.get("reason"), a.get("owner"), a.get("priority")))
    return bid


def _refs(ids):
    return " ".join(f"[S{i}]" for i in ids)


import re as _re


def _clean(s):
    return _re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", s or "")


def render_md(c, d, appendix):
    L = [f"# Monday Morning Intelligence Brief - {c['name']}", f"_Generated {datetime.now().strftime('%d %b %Y %H:%M')}_", ""]
    L += ["## 1. Executive Summary"]
    if d["executive_summary"]:
        for n, e in enumerate(d["executive_summary"][:5], 1):
            L.append(f"{n}. **{e['headline']}** - {_clean(e['so_what'])} {_refs(e['ids'])}")
    else:
        L.append("No meaningful developments this period.")
    for n, (k, t) in enumerate(CAT_TITLES, 2):
        L += ["", f"## {n}. {t}"]
        sec = d[k]
        if sec["status"] == "none":
            L.append("No meaningful development in this category this period.")
        for it in sec["items"]:
            L += [f"- **Evidence:** {_clean(it.get('evidence'))} {_refs(it['ids'])}",
                  f"  - **Interpretation:** {_clean(it.get('interpretation'))}", f"  - **Implication:** {_clean(it.get('implication'))}"]
    L += ["", "## 6. Opportunities Identified"]
    for o in d["opportunities"] or []:
        L += [f"- **{o['title']}** (priority: {o.get('priority')}) {_refs(o['ids'])}", f"  - Evidence: {_clean(o.get('evidence'))}",
              f"  - Impact: {o.get('impact')}", f"  - Next step: {o.get('next_step')}"]
    if not d["opportunities"]:
        L.append("No evidence-backed opportunities identified this period.")
    L += ["", "## 7. Risks & Threats"]
    for r in d["risks"] or []:
        L.append(f"- **{r['title']}** (urgency: {r.get('urgency')}) - {_clean(r.get('possible_impact'))} {_refs(r['ids'])}")
    if not d["risks"]:
        L.append("No material risks identified this period.")
    L += ["", "## 8. Recommended Actions for This Week"]
    for a in d["actions"] or []:
        L.append(f"- [{a.get('priority')}] **{a['action']}** - {_clean(a.get('reason'))} _(owner: {a.get('owner')})_ {_refs(a['ids'])}")
    if not d["actions"]:
        L.append("No actions recommended.")
    L += ["", "## 9. What We Should Watch"]
    for w in d["watch"] or []:
        L.append(f"- **{w['item']}** - {_clean(w.get('why'))} {_refs(w['ids'])}")
    if not d["watch"]:
        L.append("Nothing flagged for early watch.")
    L += ["", "## 10. Source Appendix", "| Ref | Title | Source | Published | Discovered | Evidence snippet | Reliability / evidence / relevance | Status |", "|---|---|---|---|---|---|---|---|"]
    for r in appendix:
        snip = (db.jl(r["analysis"], {}).get("what_happened") or r["snippet"] or "")[:160].replace("|", "/").replace("\n", " ")
        L.append(f"| S{r['id']} | {r['title'][:90].replace('|','/')} | [{r['publisher'] or 'link'}]({r['url']}) | {(r['published'] or 'n/a')[:10]} | {r['discovered'][:10]} | {snip} | "
                 f"{r['credibility']:.2f} / {r['evidence_strength']:.0f}/10 / {r['relevance']:.0f}/10 | {r['verified']} |")
    L += ["", "_Note: links on news.google.com are Google News redirects that open the original publisher article._"]
    L += ["", "### Limitations"] + [f"- {x}" for x in d["limitations"]] if d["limitations"] else []
    return "\n".join(L)


def render_html(c, d, appendix):
    import markdown_lite
    return markdown_lite.to_html(render_md(c, d, appendix), c["name"])
