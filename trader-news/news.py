#!/usr/bin/env python3
"""News monitor for trader-news (Indian market). Standard library only (curl_cffi is
used for every download when it is installed).

Watches NSE exchange filings and news headlines for the NSE stocks you watch or
hold and prints only what is new since the last check, newest first, with how far
each stock has moved today and a flag on anything that touches your tickers. For
every ticker it also prints plain links to the exchange announcement pages.

    python3 news.py                                  # watchlist + plan + portfolio tickers
    python3 news.py --tickers RELIANCE TCS HDFCBANK
    python3 news.py --tickers RELIANCE --all --hours 72   # everything in the last 3 days, seen or not
    python3 news.py --no-moves                       # skip the price line per ticker

Your tickers, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json. Symbols are plain NSE symbols (RELIANCE,
M&M, BAJAJ-AUTO); a trailing ".NS" is accepted and stripped. Company names come
from the files above and from trader/universe.txt (scan.py universe).

Sources, one request each per ticker (free, no key):
  - NSE corporate announcements: the exchange's own filings (results and board
    meeting outcomes, orders won, acquisitions, dividends and record dates, credit
    ratings, management changes, clarifications NSE asked for ...), with the PDF:
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYMBOL>&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
  - Google News headlines (RSS, India edition), searched by the company name
    ("Reliance Industries" when:2d), newest first, at most --max-headlines a stock:
    https://news.google.com/rss/search?q=<query>&hl=en-IN&gl=IN&ceid=IN:en
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m

Every request goes through curl_cffi (pip install curl_cffi) with a Chrome TLS
fingerprint and browser headers, because NSE and Yahoo block plain Python
clients; without it urllib is used with the same headers and may be blocked. NSE
gets one visit to its home page for cookies if it answers 401 or 403.

Times print in IST (UTC+5:30, no daylight saving). State: trader/state/news.json
remembers what you have seen (30 days), so an item shows once. Offline:
--offline DIR reads nse-<SYMBOL>.json, gnews-<SYMBOL>.xml and chart-<SYMBOL>.json
from DIR (plain symbols). The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    from curl_cffi import requests as _cffi   # Chrome's TLS fingerprint: NSE and Yahoo block plain Python clients
except ImportError:                           # urllib is the fallback (same headers, no fingerprint)
    _cffi = None

IMPERSONATE = "chrome"   # curl_cffi's name for the newest Chrome profile it ships (it also sets the User-Agent)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/136.0.0.0 Safari/537.36")   # the urllib fallback only
NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s&from_date=%s&to_date=%s"
GNEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_ANNOUNCEMENTS = "https://www.nseindia.com/get-quotes/equity?symbol=%s"
BSE_ANNOUNCEMENTS = "https://www.bseindia.com/corporates/ann.html"
RUPEE = "₹"
IST = _dt.timedelta(hours=5, minutes=30)   # no daylight saving
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving
MAX_HEADLINES = 12   # Google News headlines kept per ticker (newest first)
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # a plain NSE symbol
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"), 1)}

KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults?\b|\bQ[1-4]\b|\bEPS\b|\bguidance\b|\bforecast\b"
                 r"|\bboard meeting\b|\bnet profit\b|\bintegrated filing\b|\bcon\. call\b|\bconcall\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|\btarget price\b|\bprice target\b"
                r"|\bbrokerages?\b|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bSEBI\b|(?-i:\bED\b)|\bCBI\b"
              r"|\bNCLT\b|\bantitrust\b|\bjudge\b|\bcourt\b|\binsolvency\b|\blitigation\b"
              r"|\bshow cause\b|\bpenalty\b|\bsearch and seizure\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bamalgamation\b|\bbuyout\b|\btakeover\b|\bdeal\b"
             r"|\border wins?\b|\bwins? (?:\w+ ){0,3}(?:order|contract)s?\b"
             r"|\bbag(?:s|ging)? (?:\w+ ){0,3}orders?\b|\bcontracts?\b|\bblock deals?\b|\bbulk deals?\b"
             r"|\bawarding of orders?\b"),
    ("capital", r"\bdividend\b|\bbonus\b|\bsplit\b|\bbuyback\b|\brepurchase\b|\brecord date\b"
                r"|\brights issue\b|\bQIP\b|\bOFS\b|\bIPO\b|\boffering\b|\bpreferential\b|\bwarrants?\b"),
    ("ownership", r"\bpromoters?\b|\bpledg\w*|\bstake\b|\bshareholding\b"),
    ("people", r"\bCEO\b|\bCFO\b|(?-i:\bMD\b)|\bresign|\bsteps down\b|\bappoint|\bchange in (?:management|directors?)\b"
              r"|\bcessation\b"),
    ("rating", r"\bcredit rating\b|\bCRISIL\b|\bICRA\b|\bCARE Ratings\b|\bIndia Ratings\b"),
    ("risk", r"\brecall\b|\bbankrupt|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b|\bfire\b"
             r"|\bstrikes?\b|\block-?outs?\b|\bdisruption of operations\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\bcircuit\b|\bASM\b|\bGSM\b"
             r"|\bF&O ban\b"),
    ("clarification", r"\bclarification\b|\bnews verification\b|\brumou?r verification\b|\bspurt in volume\b"
                      r"|\bprice movement\b"),
    ("regulatory", r"\bFDA\b|\bUSFDA\b|\bapproval\b|\bapproves\b|\bclinical\b|\bCCI\b|\bIRDAI\b"),
    ("macro", r"\bRBI\b|\brepo\b|\brate (?:cut|hike)\b|\binflation\b|\bCPI\b|\bGDP\b|\bbudget\b"
              r"|\bGST\b|\btariffs?\b|\bcrude\b|\brupee\b|\bfutures\b"),
    ("market", r"\bNifty\b|\bSensex\b|\bFII\b|\bFPI\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time and money

def to_ist(utc):
    """Aware or naive UTC -> naive IST wall-clock time (UTC+5:30, no daylight saving)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST


def inr(x, dec=2):
    """Rupees with Indian digit grouping: 123456.78 -> '₹1,23,456.78'.
    dec=None: no decimals for a whole number, else two."""
    if x is None:
        return "n/a"
    if dec is None:
        dec = 0 if float(x) == int(x) else 2
    text = "%.*f" % (dec, abs(float(x)))
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    neg = "-" if float(x) < 0 and float(text) != 0 else ""
    return neg + RUPEE + whole + ("." + frac if frac else "")


def _utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------- symbols and names

def yahoo_symbol(sym):
    """Plain NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS. Indices (^NSEI) and
    .NS/.BO symbols are kept."""
    s = (sym or "").strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def plain_symbol(sym):
    """RELIANCE.NS -> RELIANCE (what our files use)."""
    s = (sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def _q(s):
    return urllib.parse.quote(s, safe="")


def short_name(name):
    """'Reliance Industries Limited' -> 'Reliance Industries' (what a headline calls the company)."""
    return re.sub(r"[\s,.]+(?:limited|ltd)\.?$", "", (name or "").strip(), flags=re.I).strip()


def gnews_query(sym, name=None, hours=48):
    """The Google News search for one stock: the quoted company name, else the symbol with NSE (a bare
    symbol like ITC or M&M is ambiguous), limited to the window (when:Nd)."""
    days = min(30, max(1, int(math.ceil(hours / 24.0))))
    who = short_name(name)
    return '"%s" when:%dd' % (who, days) if who else '"%s" NSE when:%dd' % (sym, days)


def links_for(sym):
    """Plain links to the exchange announcement pages (BSE's is not fetched by this tool)."""
    nse = NSE_ANNOUNCEMENTS % _q(sym) if SYMBOL_RE.match(sym) else None
    return {"nse": nse, "bse": BSE_ANNOUNCEMENTS}


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[plain_symbol(sym)] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(plain_symbol(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[plain_symbol(sym)] = p.get("name") or out.get(plain_symbol(sym))
    return out


def universe_names(path="trader/universe.txt"):
    """{symbol: company name} from trader/universe.txt (one SYMBOL|Name a line, scan.py universe)."""
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                sym, _, name = line.strip().partition("|")
                if sym and name and not sym.startswith("#"):
                    out[plain_symbol(sym)] = name.strip()
    return out


# ---------------------------------------------------------------- fetching

def browser_headers(url):
    """The headers a browser sends to that site."""
    host = urllib.parse.urlsplit(url).netloc
    if host.endswith("nseindia.com"):
        return {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                "Referer": NSE_HOME}
    if host.endswith("yahoo.com"):
        return {"Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://finance.yahoo.com/"}
    return {"Accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9"}


class Fetcher(object):
    def __init__(self, offline=None, pause=0.3):
        self.offline = offline
        self.pause = pause
        self._last = 0.0
        self._session = None
        self._primed = False

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        return self._fetch(url)

    def _fetch(self, url):
        """The body as bytes. curl_cffi (Chrome's TLS fingerprint) when it is installed, urllib otherwise."""
        headers = browser_headers(url)
        if _cffi is not None:
            if self._session is None:
                self._session = _cffi.Session(impersonate=IMPERSONATE)
            try:
                r = self._session.get(url, headers=headers, timeout=30)
                if r.status_code in (401, 403) and "nseindia.com" in url and not self._primed:
                    self._primed = True    # NSE hands out its cookies on the home page: visit it once, retry once
                    self._session.get(NSE_HOME, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                                         "Accept-Language": "en-US,en;q=0.9"}, timeout=30)
                    r = self._session.get(url, headers=headers, timeout=30)
            except Exception as exc:       # curl_cffi's RequestsError: timeout, DNS, TLS
                raise NewsError("could not reach %s: %s" % (url.split("/")[2], exc))
            if r.status_code >= 400:
                raise NewsError("HTTP %s for %s" % (r.status_code, url))
            return r.content
        req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": BROWSER_UA,
                                                                    "Accept-Encoding": "gzip"}))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_nse_time(value):
    """NSE's '03-Nov-2026 16:07:13' (IST wall clock) -> aware UTC datetime, or None."""
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})(?: (\d{1,2}):(\d{2})(?::(\d{2}))?)?$", (value or "").strip())
    if not m or m.group(2).lower() not in MONTHS:
        return None
    try:
        local = _dt.datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)),
                             int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0))
    except ValueError:
        return None
    return (local - IST).replace(tzinfo=_dt.timezone.utc)


def parse_announcements(data, ticker):
    """NSE corporate-announcements JSON -> items (one per filing)."""
    try:
        rows = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE announcements for %s are not valid JSON" % ticker)
    if not isinstance(rows, list):
        raise NewsError("NSE announcements for %s are not a list: %s" % (ticker, str(rows)[:120]))
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = plain_symbol(str(r.get("symbol") or ticker))
        desc = " ".join(str(r.get("desc") or "").split())
        text = " ".join(str(r.get("attchmntText") or "").split())
        title = "%s: %s" % (desc, text) if desc and text else (desc or text or "(no text)")
        if len(title) > 260:
            title = title[:257].rstrip() + "..."
        when = parse_nse_time(r.get("an_dt"))
        key = str(r.get("seq_id") or "") or hashlib.sha1(
            ("%s|%s|%s" % (sym, r.get("an_dt"), title)).encode("utf-8")).hexdigest()[:16]
        out.append({"id": "nse:" + key, "source": "NSE filing", "kind": "announcement", "ticker": sym,
                    "title": title, "summary": text, "category": desc,
                    "link": r.get("attchmntFile") or NSE_ANNOUNCEMENTS % _q(sym), "time": when})
    return out


def parse_rss(data, ticker):
    """Google News RSS -> items. The title ends with ' - <publisher>': the publisher becomes the source."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        publisher = _text(it, "source")
        if publisher and title.endswith(" - " + publisher):
            title = title[:-len(publisher) - 3].rstrip()
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": publisher or "Google News", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": "",
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        ysym = _q(yahoo_symbol(t))
        try:
            try:
                data = fetcher.get(CHART % ("query2", ysym), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", ysym), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    """How a headline names one of your stocks: (NSE: X), (BSE: X), NSE: X, $X, (X) or the company name."""
    pats = {}
    for t, name in watch.items():
        end = r"(?![A-Za-z0-9&\-])"
        alts = [r"\(%s\)" % re.escape(t), r"\$%s%s" % (re.escape(t), end),
                r"\b(?:NSE|BSE)\s*:\s*%s%s" % (re.escape(t), end)]
        who = short_name(name)
        if who and len(who) >= 4:
            alts.append(r"\b%s\b" % re.escape(who).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your tickers each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, errors=None, now=None, hours=48, max_headlines=MAX_HEADLINES):
    """NSE filings and Google News headlines for every ticker. now: aware UTC (default: the real clock)."""
    errors = [] if errors is None else errors
    now = now or _dt.datetime.now(_dt.timezone.utc)
    today = to_ist(now)
    first = today - _dt.timedelta(hours=hours)
    items = []
    for t in sorted(watch):
        try:
            url = NSE_API % (_q(t), first.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y"))
            items += parse_announcements(fetcher.get(url, "nse-%s.json" % t), t)
        except NewsError as exc:
            errors.append("NSE filings for %s: %s" % (t, exc))
        try:
            if not short_name(watch.get(t)):
                errors.append("headlines for %s: the company name is unknown, so Google News was searched by "
                              "the symbol and may bring noise (scan.py universe writes the names to "
                              "trader/universe.txt)" % t)
            url = GNEWS % _q(gnews_query(t, watch.get(t), hours))
            heads = parse_rss(fetcher.get(url, "gnews-%s.xml" % t), t)
            heads.sort(key=lambda i: i["time"] or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc), reverse=True)
            items += heads[:max_headlines]
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-10s %s %+.2f%% vs yesterday's close %s%s  %s" % (
            m["ticker"], inr(m["price"]), m["pct"], inr(m["prev_close"]), when, flag))
    return out


def render_links(links):
    if not links:
        return []
    out = ["Every filing and the BSE side are on the exchange pages (BSE's is not fetched here):"]
    for t in sorted(links):
        nse = links[t].get("nse")
        out.append("  %-12s %sBSE %s" % (t, ("NSE %s  " % nse) if nse else "", links[t]["bse"]))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT, links=None):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("  %s  %-11s %-13s %s" % (when, who, it["source"][:13], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    lines = render_links(links)
    if lines:
        out.append("")
        out.extend(lines)
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Filings are the company's own words and headlines are leads, not facts; "
               "open the source before acting. Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    _utf8_console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="plain NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--universe", default="trader/universe.txt", help="SYMBOL|Name lines (scan.py universe)")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--max-headlines", type=int, default=MAX_HEADLINES,
                    help="Google News headlines kept per ticker (default %d)" % MAX_HEADLINES)
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time, for reproducible runs (a naive time is UTC)")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        watch = {plain_symbol(t): None for t in args.tickers}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    names = universe_names(args.universe)
    for t in watch:
        if not watch[t] and names.get(t):
            watch[t] = names[t]
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, errors=errors, now=now, hours=args.hours, max_headlines=args.max_headlines)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    links = {t: links_for(t) for t in sorted(watch)}
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "links": links, "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct, links))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
