#!/usr/bin/env python3
"""Earnings reader for trader-analyst (Indian market). Standard library only (curl_cffi
is used for the download when it is installed).

Pulls a company's quarterly numbers from Yahoo Finance's fundamentals-timeseries
endpoint (unofficial, keyless, can lag or change without notice) and lays out
what changed, quarter by quarter: revenue, gross, operating and net margins,
net income, diluted EPS, and growth against the same quarter a year earlier,
plus the last four quarters (TTM). Money is in rupee crore (1 crore = 1e7), EPS
in rupees. The request goes through curl_cffi (pip install curl_cffi) with a
Chrome TLS fingerprint and browser headers, because Yahoo blocks plain Python
clients; without it urllib is used with the same headers and may be blocked.

    python3 facts.py RELIANCE
    python3 facts.py RELIANCE TCS --quarters 8
    python3 facts.py RELIANCE --json
    python3 facts.py RELIANCE TCS HDFCBANK INFY ITC --metrics trader/metrics.json

Source, one request per symbol (plain NSE symbols; a trailing ".NS" is stripped):
  https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/<SYM>.NS
      ?symbol=<SYM>.NS&type=quarterlyTotalRevenue,quarterlyGrossProfit,
      quarterlyOperatingIncome,quarterlyNetIncome,quarterlyDilutedEPS
      &period1=<unix>&period2=<unix>&merge=false

How the quarters are built:
  - Only periods Yahoo marks "3M" count. Each type is a list of
    {"asOfDate", "periodType", "currencyCode", "reportedValue": {"raw"}}.
  - The fiscal label comes from the period end: Apr-Jun is Q1, Jul-Sep Q2,
    Oct-Dec Q3, Jan-Mar Q4 of the fiscal year that ends the next 31 March
    (quarter ending 2026-06-30 is "Q1 FY27"; ending 2026-03-31 is "Q4 FY26").
  - Indian companies file their own Q4 results, so nothing is derived.
  - Banks and NBFCs report revenue differently and have no gross or operating
    profit in Yahoo's data: those columns stay blank, not zero.
  - These are an aggregator's numbers, usually consolidated. Check consolidated
    against standalone and one-off (exceptional) items in the filed result.

If Yahoo does not answer (an error, an empty reply) the tool says so and points
to the NSE and BSE financial-results pages. It never invents a number.

--metrics FILE merges one row per symbol into a JSON file (for notes or a
sheet). Offline: --offline DIR reads fundamentals-<SYM>.json from DIR. --today
YYYY-MM-DD pins "today" for the stale-data check (the tests use both).

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import calendar
import datetime as _dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from curl_cffi import requests as _cffi   # Chrome's TLS fingerprint: Yahoo blocks plain Python clients
except ImportError:                           # urllib is the fallback (same headers, no fingerprint)
    _cffi = None

IMPERSONATE = "chrome"   # curl_cffi's name for the newest Chrome profile it ships (it also sets the User-Agent)
FACTS_URL = ("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
             "?symbol=%s&type=%s&period1=%d&period2=%d&merge=false")
NSE_RESULTS = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
BSE_RESULTS = "https://www.bseindia.com/corporates/Comp_Resultsnew.aspx"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/136.0.0.0 Safari/537.36")   # the urllib fallback only
YAHOO_HEADERS = {"Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
                 "Referer": "https://finance.yahoo.com/"}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # a plain NSE symbol
CRORE = 1e7
STALE_DAYS = 135       # newest quarter older than this: Yahoo may lag the filed result
RUPEE = "₹"
SOURCE = "Yahoo Finance fundamentals-timeseries"

# our key -> Yahoo timeseries type
TYPES = {
    "revenue": "quarterlyTotalRevenue",
    "gross_profit": "quarterlyGrossProfit",
    "operating_income": "quarterlyOperatingIncome",
    "net_income": "quarterlyNetIncome",
    "eps_diluted": "quarterlyDilutedEPS",
}
FILED_HELP = ("No numbers were guessed. Read the filed result: NSE %s  BSE %s" % (NSE_RESULTS, BSE_RESULTS))


class FactsError(Exception):
    pass


def _utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------- symbols and money

def yahoo_symbol(sym):
    s = (sym or "").strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def plain_symbol(sym):
    s = (sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def group_in(x, dec=0):
    """Indian digit grouping without a currency sign: 1234567.8 -> '12,34,567.8' (dec=1)."""
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
    return neg + whole + ("." + frac if frac else "")


def inr(x, dec=2):
    """Rupees with Indian grouping: 123456.78 -> '₹1,23,456.78'."""
    if x is None:
        return "n/a"
    neg = float("%.*f" % (dec, abs(float(x)))) != 0 and float(x) < 0
    return ("-" if neg else "") + RUPEE + group_in(abs(float(x)), dec)


def facts_url(sym, today=None):
    """The request for one plain symbol: about five years of quarters up to tomorrow."""
    today = today or _dt.date.today()
    p1 = calendar.timegm((today - _dt.timedelta(days=5 * 366)).timetuple())
    p2 = calendar.timegm((today + _dt.timedelta(days=1)).timetuple())
    ysym = urllib.parse.quote(yahoo_symbol(sym), safe="")
    return FACTS_URL % (ysym, ysym, ",".join(TYPES.values()), p1, p2)


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline, self.pause, self._last = offline, pause, 0.0

    def json(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        data = self._fetch(url)
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise FactsError("Yahoo's unofficial endpoint answered with something that is not JSON")

    def _fetch(self, url):
        """The body as bytes. curl_cffi (Chrome's TLS fingerprint) when it is installed, urllib otherwise."""
        if _cffi is not None:
            try:
                r = _cffi.get(url, headers=dict(YAHOO_HEADERS), impersonate=IMPERSONATE, timeout=60)
            except Exception as exc:          # curl_cffi's RequestsError: timeout, DNS, TLS
                raise FactsError("Yahoo's unofficial endpoint did not answer (could not reach it: %s)" % exc)
            if r.status_code >= 400:
                raise FactsError("Yahoo's unofficial endpoint did not answer (HTTP %s)" % r.status_code)
            return r.content
        req = urllib.request.Request(url, headers=dict(YAHOO_HEADERS, **{"User-Agent": USER_AGENT,
                                                                          "Accept-Encoding": "gzip"}))
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise FactsError("Yahoo's unofficial endpoint did not answer (HTTP %s)" % exc.code)
        except urllib.error.URLError as exc:
            raise FactsError("Yahoo's unofficial endpoint did not answer (could not reach it: %s)"
                             % exc.reason)


# ---------------------------------------------------------------- quarters

def _date(s):
    return _dt.date.fromisoformat(s)


def fiscal_label(end):
    """'2026-06-30' -> 'Q1 FY27' (fiscal year 1 Apr to 31 Mar, named for the year it ends)."""
    d = _date(end)
    q = {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[d.month]
    fy = d.year if d.month <= 3 else d.year + 1
    return "Q%d FY%02d" % (q, fy % 100)


def parse_timeseries(doc):
    """Yahoo's answer -> ({key: {end: {"value", "end", "currency"}}}, {key: type name}).

    Money is converted to rupee crore (value / 1e7); EPS stays in rupees. Entries that
    are not 3M periods, or have no number, are skipped. A type may be absent."""
    if not isinstance(doc, dict):
        raise FactsError("Yahoo's unofficial endpoint answered with an unexpected document")
    ts = doc.get("timeseries")
    if not isinstance(ts, dict):
        raise FactsError("Yahoo's unofficial endpoint answered with an unexpected document")
    err = ts.get("error")
    if err:
        text = (err.get("description") or err.get("code")) if isinstance(err, dict) else str(err)
        raise FactsError("Yahoo's unofficial endpoint answered with an error: %s" % text)
    results = ts.get("result")
    if not results:
        raise FactsError("Yahoo's unofficial endpoint answered without data for this symbol")
    out = {k: {} for k in TYPES}
    found = {}
    for res in results:
        if not isinstance(res, dict):
            continue
        for key, typ in TYPES.items():
            entries = res.get(typ)
            if not isinstance(entries, list):
                continue
            found[key] = typ
            for e in entries:
                if not isinstance(e, dict) or e.get("periodType") != "3M":
                    continue
                rv = e.get("reportedValue")
                raw = rv.get("raw") if isinstance(rv, dict) else None
                end = e.get("asOfDate")
                if raw is None or not end:
                    continue
                try:
                    _date(end)
                    val = float(raw)
                except (TypeError, ValueError):
                    continue
                out[key][end] = {"value": val if key == "eps_diluted" else val / CRORE,
                                 "end": end, "currency": e.get("currencyCode")}
    return out, found


def _yoy_match(series_, end, days=365, slack=10):
    target = _date(end) - _dt.timedelta(days=days)
    best = None
    for e in series_:
        gap = abs((_date(e) - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, e)
    return series_[best[1]] if best else None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def _r(v, n=2):
    return None if v is None else round(v, n)


def analyse(doc, quarters=6, name=None, symbol=None):
    """Every number in the report, as a dict. Money in rupee crore."""
    s, found = parse_timeseries(doc)
    rev = s["revenue"]
    if not rev and not s["net_income"]:
        raise FactsError("Yahoo's unofficial endpoint answered without quarterly revenue or net income")
    ccys = [v["currency"] for k in s for v in s[k].values() if v.get("currency")]
    currency = max(set(ccys), key=ccys.count) if ccys else "INR"
    ends = sorted(set(rev) | set(s["net_income"]), reverse=True)[:quarters]
    rows = []
    for end in ends:
        r = rev.get(end, {}).get("value")
        gp = s["gross_profit"].get(end, {}).get("value")
        oi = s["operating_income"].get(end, {}).get("value")
        ni = s["net_income"].get(end, {}).get("value")
        eps = s["eps_diluted"].get(end, {}).get("value")
        prior = {k: _yoy_match(s[k], end) for k in ("revenue", "net_income", "eps_diluted")}
        rows.append({
            "end": end, "fiscal": fiscal_label(end),
            "revenue": _r(r), "gross_profit": _r(gp), "operating_income": _r(oi),
            "net_income": _r(ni), "eps_diluted": _r(eps),
            "gross_margin": margin(gp, r), "operating_margin": margin(oi, r),
            "net_margin": margin(ni, r),
            "revenue_yoy": pct(r, (prior["revenue"] or {}).get("value")),
            "net_income_yoy": pct(ni, (prior["net_income"] or {}).get("value")),
            "eps_yoy": pct(eps, (prior["eps_diluted"] or {}).get("value")),
        })
    ttm = {}
    for key in ("revenue", "net_income", "eps_diluted"):
        last4 = sorted(s[key])[-4:]
        if len(last4) == 4 and (_date(last4[-1]) - _date(last4[0])).days < 300:
            ttm[key] = round(sum(s[key][e]["value"] for e in last4), 2)
    return {"entity": name or symbol, "symbol": symbol, "currency": currency,
            "rows": rows, "ttm": ttm, "types": found}


def stale_note(result, today=None):
    """A note when the newest quarter is old enough that Yahoo may be behind the filed result."""
    if not result["rows"]:
        return None
    today = today or _dt.date.today()
    latest = result["rows"][0]["end"]
    age = (today - _date(latest)).days
    if age > STALE_DAYS:
        return ("Yahoo's newest quarter ends %s, %d days ago. Yahoo may lag: a later result "
                "may already be filed. Read it directly: NSE %s  BSE %s" % (latest, age, NSE_RESULTS, BSE_RESULTS))
    return None


def _unit(result):
    return RUPEE if result.get("currency", "INR") == "INR" else "%s " % result["currency"]


def changes(result):
    """Plain sentences: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    unit = _unit(result)
    out = []

    def money(v):
        if v is None:
            return "n/a"
        return "%s%s crore" % (unit, group_in(v, 0 if abs(v) >= 1000 else 1)) if v >= 0 \
            else "-%s%s crore" % (unit, group_in(abs(v), 0 if abs(v) >= 1000 else 1))

    line = "Quarter ending %s (%s): revenue %s" % (cur["end"], cur["fiscal"], money(cur["revenue"]))
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% vs a year earlier)" % cur["revenue_yoy"]
    line += ", net income %s" % money(cur["net_income"])
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_income_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", diluted EPS %s%s" % (unit, group_in(cur["eps_diluted"], 2))
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line + ".")
    for key, name in (("gross_margin", "Gross margin"), ("operating_margin", "Operating margin"),
                      ("net_margin", "Net margin")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (name, cur[key])
        if prev and prev[key] is not None:
            s += " (%+.1f points vs the quarter before)" % (cur[key] - prev[key])
        out.append(s + ".")
    if cur["net_income"] is not None and cur["operating_income"] is not None \
            and cur["net_income"] > cur["operating_income"]:
        out.append("Net income is above operating income: it includes gains outside the "
                   "business (other income, exceptional or tax items). Check the filed result "
                   "before reading it as growth.")
    if prev and cur["revenue"] is not None and prev["revenue"]:
        out.append("Revenue %+.1f%% vs the quarter before (many businesses are seasonal: "
                   "compare with a year earlier first)." % pct(cur["revenue"], prev["revenue"]))
    return out


# ---------------------------------------------------------------- output

def _m(v):
    return "-" if v is None else group_in(v, 0)


def _p(v):
    return "-" if v is None else "%+.1f%%" % v if v < 0 or v > 0 else "0.0%"


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(ticker, result, today=None):
    unit = _unit(result)
    money = (RUPEE + " crore") if unit == RUPEE else "%s crore (not rupees)" % result["currency"]
    out = ["%s (%s). %s except EPS (%s). Source: %s (unofficial, an aggregator's numbers)." % (
        result["entity"] or ticker, yahoo_symbol(ticker), money,
        unit.strip(), SOURCE), ""]
    out.append("%-11s %-8s %11s %8s %7s %7s %7s %10s %8s %7s" % (
        "quarter end", "fiscal", "revenue", "rev yoy", "gross", "oper", "net",
        "net inc", "EPS dil", "EPS yoy"))
    for r in result["rows"]:
        out.append("%-11s %-8s %11s %8s %7s %7s %7s %10s %8s %7s" % (
            r["end"], r["fiscal"], _m(r["revenue"]), _p(r["revenue_yoy"]),
            _pm(r["gross_margin"]), _pm(r["operating_margin"]), _pm(r["net_margin"]),
            _m(r["net_income"]), "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"],
            _p(r["eps_yoy"])))
    t = result["ttm"]
    if t:
        out.append("")
        out.append("Last 4 quarters: revenue %s, net income %s, diluted EPS %s." % (
            "%s%s crore" % (unit, _m(t["revenue"])) if t.get("revenue") is not None else "-",
            "%s%s crore" % (unit, _m(t["net_income"])) if t.get("net_income") is not None else "-",
            "%s%s" % (unit, group_in(t["eps_diluted"], 2)) if t.get("eps_diluted") is not None else "-"))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    note = stale_note(result, today)
    if note:
        out.append("")
        out.append("NOTE: " + note)
    if result.get("currency", "INR") != "INR":
        out.append("")
        out.append("NOTE: Yahoo reports this company in %s, so the table is NOT in rupees." % result["currency"])
    out.append("")
    out.append("Read the filed result: NSE %s  BSE %s" % (NSE_RESULTS, BSE_RESULTS))
    out.append("These are Yahoo's aggregator numbers, usually consolidated: check consolidated vs "
               "standalone and exceptional items in the filed result. Banks and NBFCs report revenue "
               "differently, so their margins may be blank.")
    return "\n".join(out) + "\n"


def metrics_row(ticker, result):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"ticker": ticker, "period_end": cur.get("end"), "fiscal": cur.get("fiscal"),
            "revenue_ttm_cr": t.get("revenue"), "net_income_ttm_cr": t.get("net_income"),
            "eps_ttm": t.get("eps_diluted"),
            "revenue_growth_yoy": cur.get("revenue_yoy"), "eps_growth_yoy": cur.get("eps_yoy"),
            "net_income_growth_yoy": cur.get("net_income_yoy"),
            "gross_margin": cur.get("gross_margin"), "operating_margin": cur.get("operating_margin"),
            "net_margin": cur.get("net_margin"), "source": SOURCE}


def main(argv=None):
    _utf8_console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="*", help="plain NSE symbols, e.g. RELIANCE TCS")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per symbol into this JSON")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--today", help="YYYY-MM-DD, pins today for the stale-data check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.tickers:
        ap.print_help()
        return 2
    try:
        today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
    except ValueError:
        print("facts: --today must be YYYY-MM-DD", file=sys.stderr)
        return 2
    fetcher = Fetcher(offline=args.offline)
    results, rows, failed = [], [], 0
    for t in args.tickers:
        sym = plain_symbol(t)
        if not SYMBOL_RE.match(sym):
            print("facts: %s is not a plain NSE symbol (RELIANCE, M&M, BAJAJ-AUTO)" % t, file=sys.stderr)
            failed += 1
            continue
        try:
            doc = fetcher.json(facts_url(sym, today), "fundamentals-%s.json" % sym)
            result = analyse(doc, quarters=args.quarters, symbol=sym)
        except FactsError as exc:
            print("facts: %s: %s" % (sym, exc), file=sys.stderr)
            print("facts: %s" % FILED_HELP, file=sys.stderr)
            failed += 1
            continue
        results.append({"ticker": sym, "result": result, "changes": changes(result),
                        "note": stale_note(result, today),
                        "links": {"nse_results": NSE_RESULTS, "bse_results": BSE_RESULTS}})
        rows.append(metrics_row(sym, result))
        if not args.json:
            sys.stdout.write(render(sym, result, today))
            sys.stdout.write("\n")
    if args.json:
        print(json.dumps(results, indent=2))
    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as fh:
                existing = {r["ticker"]: r for r in json.load(fh).get("companies", [])}
        for r in rows:
            existing[r["ticker"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": today.isoformat(),
                       "companies": sorted(existing.values(), key=lambda r: r["ticker"])},
                      fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())
