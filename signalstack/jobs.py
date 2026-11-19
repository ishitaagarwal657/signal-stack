"""Pipelines + scheduler. Two independent automated processes per company:
  weekly brief  : crawl -> analyse -> brief          (cron: day/time/timezone from Settings)
  monthly audit : source-health audit                (cron: day of month)
Plus an optional frequent crawl-only job (monitoring frequency). Every run is logged; failures are shown in the dashboard."""
import threading, traceback
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import db, crawler, analysis, brief, audit

sched = BackgroundScheduler(timezone="UTC")
_locks = {}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tracked(cid, kind, trigger, fn):
    lock = _locks.setdefault((cid, kind), threading.Lock())
    if not lock.acquire(blocking=False):
        return None
    rid = db.x("insert into runs(company_id,kind,trigger,started,status,log) values(?,?,?,?,?,?)", (cid, kind, trigger, _now(), "running", ""))
    lines = []

    def log(m):
        lines.append(str(m))
        db.x("update runs set log=? where id=?", ("\n".join(lines[-60:]), rid))
    try:
        fn(log)
        db.x("update runs set finished=?, status='ok', log=? where id=?", (_now(), "\n".join(lines[-60:]), rid))
    except Exception as e:
        db.x("update runs set finished=?, status='failed', error=?, log=? where id=?",
             (_now(), f"{type(e).__name__}: {e}", "\n".join(lines[-60:]) + "\n" + traceback.format_exc()[-800:], rid))
    finally:
        lock.release()
    return rid


def _weekly(cid):
    def fn(log):
        n, total = crawler.crawl_company(cid, log)
        log(f"crawled {total} sources, {n} new records")
        analysis.analyze_company(cid, log)
        bid = brief.generate(cid, log)
        log(f"brief #{bid} saved")
    return fn


def _crawl_only(cid):
    def fn(log):
        n, total = crawler.crawl_company(cid, log)
        analysis.analyze_company(cid, log)
        log(f"crawled {total} sources, {n} new records")
    return fn


def run_async(cid, kind, trigger="manual"):
    fn = {"weekly": _weekly, "crawl": _crawl_only, "audit": lambda c: (lambda log: audit.run_audit(c, log))}[kind](cid)
    t = threading.Thread(target=tracked, args=(cid, kind, trigger, fn), daemon=True)
    t.start()


def schedule_company(cid):
    st = db.settings(cid)
    h, m = (st["brief_time"].split(":") + ["0"])[:2]
    for jid in (f"weekly_{cid}", f"audit_{cid}", f"crawl_{cid}"):
        if sched.get_job(jid):
            sched.remove_job(jid)
    tz = st["timezone"]
    sched.add_job(tracked, CronTrigger(day_of_week=st["brief_day"], hour=int(h), minute=int(m), timezone=tz),
                  args=[cid, "weekly", "schedule", _weekly(cid)], id=f"weekly_{cid}")
    sched.add_job(tracked, CronTrigger(day=int(st["audit_day"]), hour=6, minute=0, timezone=tz),
                  args=[cid, "audit", "schedule", lambda log, c=cid: audit.run_audit(c, log)], id=f"audit_{cid}")
    if int(st["monitor_hours"]) > 0:
        sched.add_job(tracked, IntervalTrigger(hours=int(st["monitor_hours"])),
                      args=[cid, "crawl", "schedule", _crawl_only(cid)], id=f"crawl_{cid}")


def start():
    if not sched.running:
        sched.start()
    for c in db.q("select id from companies"):
        schedule_company(c["id"])
