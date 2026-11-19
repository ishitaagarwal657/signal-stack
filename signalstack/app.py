import os, json, threading


def load_env():  # tiny .env loader, no extra dependency
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(f):
        for line in open(f):
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ.setdefault(k, v)


load_env()
from flask import Flask, render_template, request, redirect, url_for, flash, Response, abort
from werkzeug.utils import secure_filename
import db, research, discover, crawler, jobs, internal, audit, llm, analysis

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
UPLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOADS, exist_ok=True)
db.init()
_autofill = {}


@app.template_filter("fromjson")
def fromjson(s):
    return db.jl(s, {})


@app.template_filter("d")
def short_date(s):
    return (s or "")[:10]


def company_or_404(cid):
    c = db.q("select * from companies where id=?", (cid,), one=True)
    if not c:
        abort(404)
    return c


@app.context_processor
def inject():
    return {"companies": db.q("select id,name from companies order by id")}


def safe(fn, *a, **k):
    try:
        return fn(*a, **k), None
    except llm.LLMError as e:
        flash(f"AI error: {e}", "err")
    except Exception as e:
        flash(f"Error: {type(e).__name__}: {e}", "err")
    return None, True


# ---------- setup ----------
@app.route("/")
def index():
    return render_template("index.html", rows=db.q("select * from companies order by id desc"))


@app.route("/company/new", methods=["POST"])
def company_new():
    name, url = request.form["name"].strip(), request.form["url"].strip()
    if not name or not url:
        flash("Company name and website are required.", "err")
        return redirect(url_for("index"))
    cid = db.x("insert into companies(name,url,settings) values(?,?,?)", (name, url, json.dumps(db.DEFAULT_SETTINGS)))
    _, err = safe(research.research_company, cid)
    jobs.schedule_company(cid)
    return redirect(url_for("company", cid=cid))


@app.route("/c/<int:cid>")
def company(cid):
    c = company_or_404(cid)
    qa = research.qa(cid)
    return render_template("company.html", c=c, r=db.jl(c["research"], {}), p=db.jl(c["profile"], {}), qa=qa,
                           changes=db.q("select * from changes where company_id=? order by id desc", (cid,)), tab="profile")


@app.route("/c/<int:cid>/research", methods=["POST"])
def redo_research(cid):
    company_or_404(cid)
    safe(research.research_company, cid)
    return redirect(url_for("company", cid=cid))


@app.route("/c/<int:cid>/profile", methods=["POST"])
def profile_save(cid):
    company_or_404(cid)
    p = {k: v for k, v in request.form.items()}
    db.x("update companies set profile=? where id=?", (json.dumps(p), cid))
    flash("Profile saved.", "ok")
    return redirect(url_for("company", cid=cid))


@app.route("/c/<int:cid>/profile/build", methods=["POST"])
def profile_build(cid):
    company_or_404(cid)
    safe(research.build_profile, cid)
    return redirect(url_for("company", cid=cid))


@app.route("/c/<int:cid>/context", methods=["POST"])
def context_change(cid):
    company_or_404(cid)
    note = request.form["note"].strip()
    if len(note) < 25:
        flash("Describe the change in a full sentence (what changed and why).", "err")
    else:
        res, err = safe(audit.reassess, cid, note)
        if res:
            flash("Signal Stack reassessed. See 'What changed' below.", "ok")
    return redirect(url_for("company", cid=cid) + "#changes")


# ---------- interview ----------
@app.route("/c/<int:cid>/interview", methods=["GET", "POST"])
def interview(cid):
    c = company_or_404(cid)
    if request.method == "POST":
        qid = int(request.form["qid"])
        if request.form.get("skip"):
            db.x("update qa set skipped=1 where id=? and company_id=?", (qid, cid))
        else:
            ans = request.form["answer"].strip()
            if not ans:
                flash("Write an answer or press Skip.", "err")
                return redirect(url_for("interview", cid=cid))
            db.x("update qa set answer=?, skipped=0 where id=? and company_id=?", (ans, qid, cid))
        return redirect(url_for("interview", cid=cid))
    rows = research.qa(cid)
    cur, err = None, None
    if not _autofill.get(cid):
        cur, err = safe(research.next_question, cid)
    done = (cur is None and not err and not _autofill.get(cid))
    return render_template("interview.html", c=c, qa=rows, cur=cur, done=done, n_main=research.main_count(rows),
                           filling=_autofill.get(cid), tab="interview")


@app.route("/c/<int:cid>/interview/edit", methods=["POST"])
def interview_edit(cid):
    company_or_404(cid)
    for k, v in request.form.items():
        if k.startswith("a_"):
            db.x("update qa set answer=?, skipped=?, assumed=0 where id=? and company_id=?", (v.strip() or None, 0 if v.strip() else 1, int(k[2:]), cid))
    safe(research.build_profile, cid)
    flash("Answers updated and profile rebuilt.", "ok")
    return redirect(url_for("company", cid=cid))


@app.route("/c/<int:cid>/interview/autofill", methods=["POST"])
def interview_autofill(cid):
    """DEMO helper for stress-testing other companies: AI drafts answers, clearly flagged as assumptions."""
    company_or_404(cid)

    def work():
        _autofill[cid] = True
        try:
            for _ in range(16):
                cur = research.next_question(cid)
                if cur is None:
                    break
                ans = llm.ask(f"""Company {company_or_404(cid)['name']}. Research: {research.profile_brief(cid)[:2500]}
Draft a plausible, concise (2-3 sentence) founder answer to: {cur['question']}
Base it on public information about the company; where you must guess, say so.""", as_json=False)
                db.x("update qa set answer=?, assumed=1 where id=?", ("[AI-assumed draft] " + ans.strip(), cur["id"]))
            research.build_profile(cid)
        except Exception as e:
            print("autofill failed", e)
        finally:
            _autofill[cid] = False
    threading.Thread(target=work, daemon=True).start()
    flash("Drafting assumed answers in the background (about 2 minutes). Refresh this page. They are flagged as AI-assumed.", "ok")
    return redirect(url_for("interview", cid=cid))


# ---------- stack & sources ----------
@app.route("/c/<int:cid>/stack")
def stack(cid):
    c = company_or_404(cid)
    srcs = db.q("select * from sources where company_id=? and status!='removed' order by category, name", (cid,))
    groups = {k: [s for s in srcs if s["category"] == k] for k in ("customers", "competitors", "market", "internal")}
    return render_template("stack.html", c=c, groups=groups, tab="stack")


@app.route("/c/<int:cid>/discover", methods=["POST"])
def do_discover(cid):
    company_or_404(cid)
    if not db.jl(db.q("select profile from companies where id=?", (cid,), one=True)["profile"]):
        flash("Complete the interview first (or press 'Build profile') so sources are tailored to the company.", "err")
        return redirect(url_for("stack", cid=cid))
    n, err = safe(discover.discover, cid)
    if n is not None:
        flash(f"Discovered {n} new sources. Review and approve them in Source Manager.", "ok")
    return redirect(url_for("sources", cid=cid))


@app.route("/c/<int:cid>/sources")
def sources(cid):
    c = company_or_404(cid)
    srcs = audit.source_stats(cid) + db.q("select *, 0 n_records, 0 n_recent, null avg_rel from sources where company_id=? and status in ('pending','removed')", (cid,))
    seen, dups = {}, set()
    for s in srcs:
        key = (s["kind"], (s["url"] if s["kind"] == "page" else s["query"] or s["name"]).lower())
        if key in seen:
            dups.add(s["id"])
        seen[key] = s["id"]
    order = {"pending": 0, "approved": 1, "inactive": 2, "removed": 3}
    srcs.sort(key=lambda s: (order.get(s["status"], 9), s["category"], s["name"]))
    return render_template("sources.html", c=c, srcs=srcs, dups=dups, tab="sources")


@app.route("/c/<int:cid>/sources/add", methods=["POST"])
def source_add(cid):
    company_or_404(cid)
    f = request.form
    url = f["url"].strip()
    kind = f["kind"]
    if kind == "rss" and not url.startswith("http"):
        url = discover.gnews(url)
    elif kind == "hn" and not url.startswith("http"):
        url = discover.hn(url)
    sid = discover.add_source(cid, f["category"], kind, f["name"].strip() or url, url, f["reason"].strip() or "Added manually by the founder.",
                              query=f.get("query", ""), competitor=f.get("competitor") or None, origin="manual")
    if sid:
        db.x("update sources set status='approved' where id=?", (sid,))
        flash("Source added and approved.", "ok")
    else:
        flash("That source already exists.", "err")
    return redirect(url_for("sources", cid=cid))


@app.route("/c/<int:cid>/sources/<int:sid>/<action>", methods=["POST"])
def source_action(cid, sid, action):
    company_or_404(cid)
    status = {"approve": "approved", "remove": "removed", "restore": "pending"}.get(action)
    if status:
        db.x("update sources set status=? where id=? and company_id=?", (status, sid, cid))
    return redirect(url_for("sources", cid=cid))


@app.route("/c/<int:cid>/sources/approve-all", methods=["POST"])
def approve_all(cid):
    company_or_404(cid)
    db.x("update sources set status='approved' where company_id=? and status='pending'", (cid,))
    return redirect(url_for("sources", cid=cid))


# ---------- feed ----------
@app.route("/c/<int:cid>/feed")
def feed(cid):
    c = company_or_404(cid)
    a = request.args
    sql, args = "select * from records where company_id=?", [cid]
    for col, key in (("category", "category"), ("competitor", "competitor"), ("verified", "verified")):
        if a.get(key):
            sql += f" and {col}=?"
            args.append(a[key])
    if a.get("min_rel"):
        sql += " and relevance>=?"
        args.append(float(a["min_rel"]))
    if a.get("min_impact"):
        sql += " and impact>=?"
        args.append(float(a["min_impact"]))
    if a.get("since"):
        sql += " and coalesce(published,discovered)>=?"
        args.append(a["since"])
    if a.get("type"):
        sql += " and analysis like ?"
        args.append(f'%"type": "{a["type"]}"%')
    order = "score desc" if a.get("sort") == "score" else "coalesce(published,discovered) desc"
    rows = db.q(sql + f" order by {order} limit 300", args)
    comps = [r["competitor"] for r in db.q("select distinct competitor from records where company_id=? and competitor is not null", (cid,))]
    return render_template("feed.html", c=c, rows=rows, comps=comps, a=a, explain=analysis.explain, tab="feed")


# ---------- internal ----------
@app.route("/c/<int:cid>/internal", methods=["GET", "POST"])
def internal_page(cid):
    c = company_or_404(cid)
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a file.", "err")
        elif not f.filename.lower().endswith((".csv", ".tsv", ".xlsx", ".xls", ".pdf", ".txt", ".md")):
            flash("Supported: CSV, Excel, PDF, text.", "err")
        else:
            path = os.path.join(UPLOADS, f"{cid}_{secure_filename(f.filename)}")
            f.save(path)
            res, err = safe(internal.analyze_upload, cid, path, f.filename)
            if res:
                flash(f"Analysed {f.filename}: {res[0]} findings. {res[1]}", "ok")
        return redirect(url_for("internal_page", cid=cid))
    recs = db.q("select * from records where company_id=? and category='internal' order by score desc", (cid,))
    return render_template("internal.html", c=c, recs=recs, tab="internal")


# ---------- briefs ----------
@app.route("/c/<int:cid>/briefs")
def briefs(cid):
    c = company_or_404(cid)
    return render_template("briefs.html", c=c, rows=db.q("select id,kind,created from briefs where company_id=? order by id desc", (cid,)),
                           runs=db.q("select * from runs where company_id=? order by id desc limit 15", (cid,)), tab="briefs")


@app.route("/c/<int:cid>/brief/<int:bid>")
def brief_view(cid, bid):
    c = company_or_404(cid)
    b = db.q("select * from briefs where id=? and company_id=?", (bid, cid), one=True) or abort(404)
    return render_template("brief.html", c=c, b=b, body=b["html"].split("<body>")[1].rsplit("</body>", 1)[0],
                           actions=db.q("select * from actions where brief_id=?", (bid,)), tab="briefs")


@app.route("/c/<int:cid>/brief/<int:bid>/download/<fmt>")
def brief_download(cid, bid, fmt):
    b = db.q("select * from briefs where id=? and company_id=?", (bid, cid), one=True) or abort(404)
    if fmt == "md":
        return Response(b["markdown"], mimetype="text/markdown", headers={"Content-Disposition": f"attachment; filename=brief_{bid}.md"})
    return Response(b["html"], mimetype="text/html", headers={"Content-Disposition": f"attachment; filename=brief_{bid}.html"})


@app.route("/c/<int:cid>/action/<int:aid>/toggle", methods=["POST"])
def action_toggle(cid, aid):
    a = db.q("select * from actions where id=? and company_id=?", (aid, cid), one=True) or abort(404)
    db.x("update actions set done=? where id=?", (0 if a["done"] else 1, aid))
    return redirect(url_for("brief_view", cid=cid, bid=a["brief_id"]))


@app.route("/c/<int:cid>/run/<kind>", methods=["POST"])
def run(cid, kind):
    company_or_404(cid)
    if kind not in ("weekly", "crawl", "audit"):
        abort(400)
    if kind in ("weekly", "crawl") and not db.q("select id from sources where company_id=? and status='approved'", (cid,), one=True):
        flash("Approve some sources first (Source Manager).", "err")
        return redirect(url_for("sources", cid=cid))
    jobs.run_async(cid, kind)
    flash(f"{kind} run started. Progress appears in Run history (refresh in a minute).", "ok")
    return redirect(url_for("runs", cid=cid))


@app.route("/c/<int:cid>/runs")
def runs(cid):
    c = company_or_404(cid)
    return render_template("runs.html", c=c, runs=db.q("select * from runs where company_id=? order by id desc limit 40", (cid,)),
                           jobs=[(j.id, str(j.next_run_time)) for j in jobs.sched.get_jobs() if j.id.endswith(f"_{cid}")], tab="runs")


@app.route("/c/<int:cid>/delete", methods=["POST"])
def company_delete(cid):
    company_or_404(cid)
    for t in ("qa", "sources", "records", "briefs", "actions", "runs", "changes"):
        db.x(f"delete from {t} where company_id=?", (cid,))
    db.x("delete from companies where id=?", (cid,))
    for jid in (f"weekly_{cid}", f"audit_{cid}", f"crawl_{cid}"):
        if jobs.sched.get_job(jid):
            jobs.sched.remove_job(jid)
    flash("Company deleted.", "ok")
    return redirect(url_for("index"))


# ---------- settings ----------
@app.route("/c/<int:cid>/settings", methods=["GET", "POST"])
def settings(cid):
    c = company_or_404(cid)
    if request.method == "POST":
        s = db.settings(cid)
        for k in ("brief_day", "brief_time", "timezone"):
            s[k] = request.form[k]
        for k in ("monitor_hours", "audit_day", "lookback_days", "max_analyze"):
            s[k] = int(request.form[k])
        db.x("update companies set settings=?, name=?, url=? where id=?", (json.dumps(s), request.form["name"], request.form["url"], cid))
        jobs.schedule_company(cid)
        flash("Settings saved and schedules updated.", "ok")
        return redirect(url_for("settings", cid=cid))
    return render_template("settings.html", c=c, s=db.settings(cid), tab="settings")


if __name__ == "__main__":
    jobs.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, use_reloader=False)
else:
    jobs.start()
