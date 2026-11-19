"""Monitoring engine: visits approved sources, detects new/changed content, de-duplicates, stores evidence."""
import json, re, difflib
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import feedparser
import db, fetch

CUTOFF_DAYS = 90  # ignore feed items older than this


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date(entry):
    for k in ("published_parsed", "updated_parsed"):
        t = entry.get(k)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
    return None


def _store(src, title, url, publisher, published, snippet, kind):
    title = (title or "").strip()
    if not title or not url:
        return False
    hsh = fetch.h(fetch.norm_url(url) + "|" + re.sub(r"\W+", "", title.lower())[:120])
    # near-duplicate title check (same story syndicated across publishers)
    key = re.sub(r"\W+", " ", title.lower()).strip()
    dup = db.q("select id from records where company_id=? and hash=?", (src["company_id"], hsh), one=True)
    if dup:
        return False
    try:
        db.x("""insert into records(company_id,source_id,category,competitor,title,url,publisher,published,discovered,snippet,hash,kind)
                values(?,?,?,?,?,?,?,?,?,?,?,?)""",
             (src["company_id"], src["id"], src["category"], src["competitor"], title[:300], url, publisher, published,
              now(), (snippet or "")[:1200], hsh, kind))
        return True
    except Exception:
        return False


def crawl_source(src):
    """Returns (new_records, status_string)."""
    new = 0
    status_note = ""
    if src["kind"] == "upload":
        return 0, "n/a"
    if src["kind"] in ("rss", "hn"):
        feed_url = src["url"]
        if "news.google.com/rss/search" in feed_url and "when%3A" not in feed_url:
            feed_url += "%20when%3A90d"  # only the last 90 days
        st, body, note = fetch.get(feed_url, check_robots=False)  # published machine-readable feeds / public APIs
        if st != 200:
            return 0, str(st)
        if src["kind"] == "rss":
            feed = feedparser.parse(body)
            limit = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
            for e in feed.entries[:20]:
                pub = _date(e)
                if pub and datetime.fromisoformat(pub) < limit:
                    continue
                publisher = (e.get("source") or {}).get("title") or fetch.host(e.get("link", ""))
                snippet = fetch.strip_html(e.get("summary", ""))
                if snippet.strip().lower().startswith(e.get("title", "").strip().lower()[:40]) and len(snippet) < len(e.get("title", "")) + 40:
                    snippet = ""  # Google News summaries often just repeat the title + publisher
                new += _store(src, e.get("title"), e.get("link"), publisher, pub, snippet, "article")
        else:
            try:
                hits = json.loads(body).get("hits", [])
            except Exception:
                return 0, "bad_json"
            for hit in hits:
                title = hit.get("title") or hit.get("story_title")
                if not title:
                    continue
                link = f"https://news.ycombinator.com/item?id={hit['objectID']}"
                new += _store(src, title, link, "Hacker News", hit.get("created_at"),
                              f"{hit.get('points', 0)} points, {hit.get('num_comments', 0)} comments. Linked: {hit.get('url') or ''}", "discussion")
        return new, "200"
    # kind == page : change detection
    st, html, note = fetch.get(src["url"])
    if st != 200:
        return 0, str(st)
    text = fetch.clean_text(html, 8000)
    hsh = fetch.h(text)
    if src["last_hash"] and hsh != src["last_hash"]:
        old = set((src["last_text"] or "").splitlines())
        added = [l for l in text.splitlines() if l not in old]
        if added:
            snippet = " | ".join(added)[:900]
            title = f"Page changed: {src['name']}"
            # key on content hash so the same change is only recorded once
            new += _store(src, title + f" ({hsh[:6]})", src["url"], fetch.host(src["url"]), now(), snippet, "page_change")
    db.x("update sources set last_text=?, last_hash=? where id=?", (text, hsh, src["id"]))
    return new, "200"


def crawl_company(cid, log=print):
    srcs = db.q("select * from sources where company_id=? and status='approved' and kind!='upload'", (cid,))
    total = 0
    for s in srcs:
        try:
            n, st = crawl_source(s)
        except Exception as e:
            n, st = 0, f"error: {type(e).__name__}"
        failed = not str(st).startswith("200")
        db.x("update sources set last_checked=?, last_http=?, fail_count=? where id=?",
             (now(), str(st), (s["fail_count"] + 1) if failed else 0, s["id"]))
        log(f"{s['name']}: {st}, {n} new")
        total += n
    return total, len(srcs)
