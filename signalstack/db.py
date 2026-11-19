import sqlite3, os, json

DB = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "signalstack.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies(
  id INTEGER PRIMARY KEY, name TEXT, url TEXT, research TEXT, profile TEXT, context_note TEXT,
  settings TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS qa(
  id INTEGER PRIMARY KEY, company_id INTEGER, seq INTEGER, kind TEXT, area TEXT, question TEXT,
  answer TEXT, skipped INTEGER DEFAULT 0, assumed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, company_id INTEGER, category TEXT, kind TEXT, name TEXT, url TEXT,
  query TEXT, reason TEXT, status TEXT DEFAULT 'pending', origin TEXT DEFAULT 'discovered',
  last_checked TEXT, last_http TEXT, fail_count INTEGER DEFAULT 0, last_text TEXT, last_hash TEXT,
  competitor TEXT, UNIQUE(company_id, url));
CREATE TABLE IF NOT EXISTS records(
  id INTEGER PRIMARY KEY, company_id INTEGER, source_id INTEGER, category TEXT, competitor TEXT,
  title TEXT, url TEXT, publisher TEXT, published TEXT, discovered TEXT, snippet TEXT, hash TEXT,
  analysis TEXT, relevance REAL, impact REAL, urgency REAL, credibility REAL, recency REAL,
  novelty REAL, evidence_strength REAL, score REAL, verified TEXT, kind TEXT,
  UNIQUE(company_id, hash));
CREATE TABLE IF NOT EXISTS briefs(
  id INTEGER PRIMARY KEY, company_id INTEGER, kind TEXT, created TEXT, markdown TEXT, html TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY, company_id INTEGER, brief_id INTEGER, text TEXT, reason TEXT, owner TEXT,
  priority TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, company_id INTEGER, kind TEXT, trigger TEXT, started TEXT, finished TEXT,
  status TEXT, log TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY, company_id INTEGER, created TEXT DEFAULT CURRENT_TIMESTAMP, summary TEXT);
"""


def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def q(sql, args=(), one=False):
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    rows = [dict(r) for r in rows]
    if one:
        return rows[0] if rows else None
    return rows


def x(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def jl(s, default=None):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


DEFAULT_SETTINGS = {
    "brief_day": "mon", "brief_time": "08:00", "timezone": "Asia/Kolkata",
    "monitor_hours": 24, "audit_day": 1, "lookback_days": 30, "max_analyze": 40,
}


def settings(cid):
    c = q("select settings from companies where id=?", (cid,), one=True)
    s = dict(DEFAULT_SETTINGS)
    s.update(jl(c["settings"], {}) if c else {})
    return s
