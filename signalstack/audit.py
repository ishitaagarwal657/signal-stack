"""Monthly Signal Stack audit + context-change reassessment."""
import json
from datetime import datetime, timezone, timedelta
import db, fetch, llm, research, discover


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_stats(cid):
    out = []
    since = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    for s in db.q("select * from sources where company_id=? and status in ('approved','inactive')", (cid,)):
        recs = db.q("select relevance, discovered from records where company_id=? and source_id=?", (cid, s["id"]))
        recent = [r for r in recs if r["discovered"] >= since]
        rated = [r["relevance"] for r in recs if r["relevance"] is not None]
        out.append({**s, "n_records": len(recs), "n_recent": len(recent),
                    "avg_rel": round(sum(rated) / len(rated), 1) if rated else None})
    return out


def run_audit(cid, log=print):
    stats = source_stats(cid)
    findings = {"dead": [], "inactive": [], "duplicates": [], "declining": [], "healthy": 0}
    seen = {}
    for s in stats:
        if s["kind"] == "upload":
            continue
        # dead URL check (fresh)
        if s["kind"] == "page":
            st, _, _ = fetch.get(s["url"])
        else:
            st, _, _ = fetch.get(s["url"], check_robots=False)
        db.x("update sources set last_checked=?, last_http=?, fail_count=? where id=?",
             (_now(), str(st), 0 if st == 200 else s["fail_count"] + 1, s["id"]))
        ok = st == 200
        if not ok:
            findings["dead"].append({"id": s["id"], "name": s["name"], "status": str(st)})
        key = (fetch.host(s["url"]), (s.get("query") or s["name"]).lower())
        if key in seen:
            findings["duplicates"].append({"id": s["id"], "name": s["name"], "duplicate_of": seen[key]})
        seen[key] = s["name"]
        if ok and s["n_recent"] == 0:
            findings["inactive"].append({"id": s["id"], "name": s["name"], "records_total": s["n_records"]})
        if s["avg_rel"] is not None and s["n_records"] >= 3 and s["avg_rel"] < 3.5:
            findings["declining"].append({"id": s["id"], "name": s["name"], "avg_relevance": s["avg_rel"]})
        if ok and s["id"] not in [d["id"] for d in findings["duplicates"]]:
            findings["healthy"] += 1
    before = db.q("select count(*) n from sources where company_id=? and status='pending'", (cid,), one=True)["n"]
    try:
        added = discover.discover(cid, instruction="This is the monthly audit: propose NEW sources that cover gaps or newly important topics.")
    except llm.LLMError as e:
        added = 0
        log(f"discovery during audit failed: {e}")
    rec_remove = [d["name"] for d in findings["dead"] + findings["declining"]]
    lines = [f"# Monthly Signal Stack Audit - {datetime.now().strftime('%d %b %Y')}", "",
             f"- Sources checked: {len(stats)}; healthy: {findings['healthy']}",
             f"- Dead / failing URLs: {len(findings['dead'])}", f"- Inactive (no new records in 45 days): {len(findings['inactive'])}",
             f"- Duplicates: {len(findings['duplicates'])}", f"- Declining relevance (avg < 3.5/10): {len(findings['declining'])}",
             f"- Newly discovered sources awaiting approval: {added}", ""]
    for title, key, fmt in (("Dead URLs", "dead", lambda d: f"{d['name']} ({d['status']})"),
                            ("Inactive sources", "inactive", lambda d: f"{d['name']} ({d['records_total']} records total)"),
                            ("Duplicates", "duplicates", lambda d: f"{d['name']} duplicates {d['duplicate_of']}"),
                            ("Declining relevance", "declining", lambda d: f"{d['name']} (avg relevance {d['avg_relevance']})")):
        lines.append(f"## {title}")
        lines += [f"- {fmt(d)}" for d in findings[key]] or ["- None"]
        lines.append("")
    lines += ["## Recommendations", f"- Remove or replace: {', '.join(rec_remove) or 'nothing'}",
              f"- Review and approve {added} newly discovered sources in Source Manager."]
    md = "\n".join(lines)
    import markdown_lite
    bid = db.x("insert into briefs(company_id,kind,created,markdown,html,data) values(?,?,?,?,?,?)",
               (cid, "audit", _now(), md, markdown_lite.to_html(md, "Source audit"), json.dumps(findings)))
    return bid


def reassess(cid, new_context, log=print):
    """Business context changed: re-score every source for relevance, deactivate stale ones, discover new ones, log what changed."""
    db.x("update companies set context_note=? where id=?", (new_context, cid))
    prof = research.profile_brief(cid)
    srcs = db.q("select id,category,name,reason,competitor from sources where company_id=? and status in ('approved','pending') and kind!='upload'", (cid,))
    out = llm.ask(f"""The company's business context has changed. Reassess each monitored source.
{prof}

Sources: {json.dumps([{k: s[k] for k in ('id', 'category', 'name', 'reason')} for s in srcs])[:9000]}
Return JSON {{"decisions":[{{"id": int, "keep": bool, "new_reason": str (updated reason if kept, otherwise why it is no longer useful)}}], "summary": str (2-3 sentences: what changed in our monitoring priorities and why)}}""", temperature=0.2)
    dropped, kept = [], 0
    for d in out.get("decisions", []):
        s = next((x for x in srcs if x["id"] == d.get("id")), None)
        if not s:
            continue
        if d.get("keep"):
            db.x("update sources set reason=? where id=?", (d.get("new_reason") or s["reason"], s["id"]))
            kept += 1
        else:
            db.x("update sources set status='inactive', reason=? where id=?", ("No longer relevant after context change: " + (d.get("new_reason") or ""), s["id"]))
            dropped.append(s["name"])
    added = discover.discover(cid, instruction=f"The business context just changed: {new_context}. Propose sources that fit the NEW situation.")
    summary = (f"Context update: {new_context}\nKept {kept} sources, deactivated {len(dropped)}: {', '.join(dropped[:12]) or 'none'}. "
               f"Added {added} new sources (pending approval).\n{out.get('summary','')}")
    db.x("insert into changes(company_id,summary) values(?,?)", (cid, summary))
    return summary
