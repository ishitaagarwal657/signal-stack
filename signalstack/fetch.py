"""Polite fetching: robots.txt for web pages, per-host rate limit, identifiable user agent."""
import time, threading, hashlib, re
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup

UA = "SignalStackBot/1.0 (company intelligence research; respects robots.txt)"
HEADERS = {"User-Agent": UA, "Accept-Language": "en"}
_robots = {}
_host_last = {}
_lock = threading.Lock()
HOST_GAP = 1.5
SLOW_HOSTS = {"news.google.com": 4.0}  # Google News rate-limits aggressively


def norm_url(u):
    p = urlparse(u.strip())
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/")
    return urlunparse((p.scheme or "https", host, path, "", p.query if "news.google" in host or "algolia" in host else "", ""))


def host(u):
    return urlparse(u).netloc.lower().removeprefix("www.")


def robots_ok(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=10)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            elif r.status_code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except requests.RequestException:
            rp.allow_all = True
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def get(url, check_robots=True, timeout=20, **kw):
    """Returns (status, text, note). status is an int HTTP code, or a string such as 'robots_blocked'/'error: ...'."""
    if check_robots and not robots_ok(url):
        return "robots_blocked", "", "Disallowed by robots.txt"
    h = host(url)
    with _lock:
        wait = SLOW_HOSTS.get(h, HOST_GAP) - (time.time() - _host_last.get(h, 0))
        if wait > 0:
            time.sleep(wait)
        _host_last[h] = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, **kw)
        for wait_s in (15, 30):  # polite back-off when rate limited
            if r.status_code != 429:
                break
            time.sleep(wait_s)
            r = requests.get(url, headers=HEADERS, timeout=timeout, **kw)
        return r.status_code, r.text, ""
    except requests.RequestException as e:
        return f"error: {type(e).__name__}", "", str(e)[:200]


def clean_text(html, limit=8000):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]):
        t.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if len(l) > 25]
    return "\n".join(lines)[:limit]


def strip_html(s):
    return re.sub(r"\s+", " ", BeautifulSoup(s or "", "html.parser").get_text(" ")).strip()


def h(s):
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()
