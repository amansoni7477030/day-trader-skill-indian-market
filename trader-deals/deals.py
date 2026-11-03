#!/usr/bin/env python3
"""Bulk and block deals tracker for trader-deals. Standard library only (curl_cffi is
used for the download when it is installed).

Reads the two public files NSE publishes after the close every trading day:
the BULK DEALS file and the BLOCK DEALS file.

    python3 deals.py latest                              # both files, latest trading day
    python3 deals.py latest --symbol RELIANCE --kind bulk
    python3 deals.py latest --client "GOLDMAN" --min-value-cr 25
    python3 deals.py new                                 # only deals not shown before
    python3 deals.py history --symbol BLSPOLY --days 30  # what the saved days show
    python3 deals.py parse saved-bulk.csv --kind bulk    # read a saved CSV

Where the data comes from (a plain GET, no login, no cookies):
  - https://nsearchives.nseindia.com/content/equities/bulk.csv
  - https://nsearchives.nseindia.com/content/equities/block.csv
  Each file holds the LATEST trading day only. To build a history, run `new`
  once every evening after the market: it appends each fetched day to
  trader/state/deals-history.json. The download goes through curl_cffi
  (pip install curl_cffi) with a Chrome TLS fingerprint and browser headers,
  because NSE drops requests that look like scripts; without it urllib is used
  with the same headers and may time out.

What these deals are, and are not (verify the current thresholds with the
exchange; the rules change):
  - A bulk deal: a client's total quantity bought or sold in one stock in one
    day is more than 0.5% of the company's listed equity shares.
  - A block deal: one large trade (minimum order size ₹25 crore since 8
    December 2025, NSE circular NSE/CMTR/71394; it was ₹10 crore before)
    executed in a separate trading window of the exchange (08:45 to 09:00 and
    14:05 to 14:20 IST), within 3% of the reference price, delivery-based.
  - Both are published by the exchange after the close. They are public, not
    inside information, and you read them AFTER the fact. The price in the
    file is a weighted average price. One big buyer is not a signal.
  - A block deal that is also over the bulk threshold is listed in BOTH files
    (the same buyer, seller, quantity and price). Reading both files, this
    tool counts that trade once, as a block deal, and says so in a note.

State: `new` remembers the deals it has shown (30 days) in
trader/state/deals.json, so each deal alerts once. Offline: --offline DIR reads
bulk.csv / block.csv (or bulk-YYYY-MM-DD.csv / block-YYYY-MM-DD.csv) from a
folder instead of the network. The tests use it.

Exit codes: 0 = found something to show, 1 = nothing found or an error.
"""

import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

try:
    from curl_cffi import requests as _cffi   # Chrome's TLS fingerprint: NSE drops plain script clients
except ImportError:                           # urllib is the fallback (same headers, no fingerprint)
    _cffi = None

IMPERSONATE = "chrome"   # curl_cffi's name for the newest Chrome profile it ships (it also sets the User-Agent)
BASE = "https://nsearchives.nseindia.com/content/equities/"
URLS = {"bulk": BASE + "bulk.csv", "block": BASE + "block.csv"}
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/136.0.0.0 Safari/537.36")   # the urllib fallback only: NSE drops "bot" User-Agents
HEADERS = {"Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9",
           "Referer": "https://www.nseindia.com/"}
INSIDER_URL = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
KINDS = ("bulk", "block")
KEEP_DAYS = 30
PAUSE = 0.5          # seconds between the two downloads
CRORE = Decimal(10) ** 7
MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
NOTE = ("Public data the exchange publishes after the close: you are reading it after the "
        "fact, it is not inside information, and one big buyer is not a signal. Prices are "
        "weighted averages. Not financial advice.")


class DealsError(Exception):
    pass


def utf8_console():
    """So the rupee sign never raises UnicodeEncodeError on a Windows console."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------- rupee formatting

def _indian(digits):
    """'1234567' -> '12,34,567' (last three digits, then pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs + [tail])


def indian_number(x, dec=0):
    """1234567.5 -> '12,34,567' (dec=0) or '12,34,567.50' (dec=2). Keeps the sign."""
    d = Decimal(str(x))
    neg = d < 0
    text = "{:.{n}f}".format(abs(d), n=dec)
    whole, _, frac = text.partition(".")
    out = _indian(whole) + ("." + frac if frac else "")
    return ("-" if neg and Decimal(text) != 0 else "") + out


def inr(x, dec=2):
    """Rupees with Indian grouping: inr(123456.7) -> '₹1,23,456.70'.
    dec=None: no decimals when the amount is whole, else two."""
    d = Decimal(str(x))
    if dec is None:
        dec = 0 if d == d.to_integral_value() else 2
    s = indian_number(abs(d), dec)
    return ("-" if d < 0 and Decimal(s.replace(",", "")) != 0 else "") + "₹" + s


def money_cr(cr):
    """A value given in crore, written the way it reads best: crore for big
    values, lakh below 1 crore, plain rupees below 1 lakh."""
    d = Decimal(str(cr))
    rupees = abs(d) * CRORE
    sign = "-" if d < 0 else ""
    if rupees >= CRORE:
        return "%s₹%s cr" % (sign, indian_number(abs(d), 2))
    if rupees >= 100000:
        return "%s₹%s lakh" % (sign, indian_number(rupees / 100000, 2))
    return "%s%s" % (sign, inr(rupees, 0))


def signed_cr(cr):
    text = money_cr(cr)
    return text if text.startswith("-") or Decimal(str(cr)) == 0 else "+" + text


# ---------------------------------------------------------------- reading the CSV

def parse_date(text):
    """'03-NOV-2026' -> '2026-11-03'. Also accepts 2026-11-03 and 03/11/2026."""
    t = (text or "").strip()
    m = re.match(r"^(\d{1,2})[-\s]([A-Za-z]{3})[A-Za-z]*[-\s](\d{4})$", t)
    try:
        if m and m.group(2).upper() in MONTHS:
            return _dt.date(int(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1))).isoformat()
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
        if m:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", t)
        if m:
            return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None
    return None


def _norm(header):
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def _columns(header):
    """Column index for each field we need, from the header row."""
    idx = {}
    for i, h in enumerate(header):
        n = _norm(h)
        if n == "date":
            idx.setdefault("date", i)
        elif n == "symbol":
            idx.setdefault("symbol", i)
        elif n == "securityname":
            idx.setdefault("name", i)
        elif n == "clientname":
            idx.setdefault("client", i)
        elif n == "buysell":
            idx.setdefault("side", i)
        elif n.startswith("quantity"):
            idx.setdefault("qty", i)
        elif n.startswith("tradeprice") or n.startswith("price"):
            idx.setdefault("price", i)
        elif n == "remarks":
            idx.setdefault("remarks", i)
    missing = [k for k in ("date", "symbol", "client", "side", "qty", "price") if k not in idx]
    if missing:
        raise DealsError("this does not look like an NSE deals file: no column for %s "
                         "(header was: %s)" % (", ".join(missing), ", ".join(header)[:120]))
    return idx


def _number(text):
    try:
        return Decimal(re.sub(r"[,\s]", "", text))
    except InvalidOperation:
        return None


def value_cr(qty, price):
    """Value in rupees crore: quantity x price / 1e7."""
    return float(Decimal(qty) * Decimal(str(price)) / CRORE)


def decode(data):
    if isinstance(data, str):
        return data.lstrip("﻿")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def parse_csv(text, kind, problems=None):
    """Deals from an NSE bulk.csv or block.csv text.

    Each deal: kind, date (YYYY-MM-DD), symbol, name, client, side (BUY/SELL),
    qty (int), price (float, weighted average), value_cr, remarks.
    Rows that cannot be read are skipped and described in `problems` (a list)."""
    if kind not in KINDS:
        raise DealsError("kind must be bulk or block, not %r" % kind)
    problems = problems if problems is not None else []
    text = decode(text)
    if text.lstrip()[:1] == "<":
        raise DealsError("got a web page instead of a CSV file (the exchange may be blocking "
                         "automated requests or the file moved)")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return []
    cols = _columns([c.strip().lstrip("﻿") for c in rows[0]])
    need = max(cols.values())
    out = []
    for n, r in enumerate(rows[1:], start=2):
        r = [c.strip() for c in r]
        if len(r) <= need:
            problems.append("row %d has %d columns, skipped" % (n, len(r)))
            continue
        date = parse_date(r[cols["date"]])
        qty = _number(r[cols["qty"]])
        price = _number(r[cols["price"]])
        side = r[cols["side"]].upper()
        side = {"B": "BUY", "S": "SELL"}.get(side, side)
        if date is None or qty is None or price is None or side not in ("BUY", "SELL") \
                or qty != qty.to_integral_value() or qty <= 0 or price <= 0:
            problems.append("row %d is not a deal (%s), skipped" % (n, ",".join(r)[:80]))
            continue
        remarks = r[cols["remarks"]] if "remarks" in cols and len(r) > cols["remarks"] else ""
        out.append({
            "kind": kind, "date": date, "symbol": r[cols["symbol"]].upper(),
            "name": r[cols["name"]] if "name" in cols else "",
            "client": r[cols["client"]], "side": side, "qty": int(qty),
            "price": float(price), "value_cr": value_cr(int(qty), float(price)),
            "remarks": "" if remarks == "-" else remarks})
    return out


def deal_key(d):
    """Fingerprint of a deal: sha1 of date|kind|symbol|client|side|qty|price."""
    raw = "|".join(str(x) for x in (d["date"], d["kind"], d["symbol"], d["client"], d["side"],
                                    d["qty"], d["price"]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- fetching

def download(url, timeout=60):
    """The body as bytes. curl_cffi with Chrome's TLS fingerprint when it is installed, urllib otherwise."""
    if _cffi is not None:
        try:
            r = _cffi.get(url, headers=dict(HEADERS), impersonate=IMPERSONATE, timeout=timeout)
        except Exception as exc:          # curl_cffi's RequestsError: timeout, DNS, TLS
            raise DealsError("could not reach %s: %s" % (url, exc))
        if r.status_code >= 400:
            raise DealsError("HTTP %s for %s" % (r.status_code, url))
        return r.content
    req = urllib.request.Request(url, headers=dict(HEADERS, **{"User-Agent": BROWSER_UA}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise DealsError("HTTP %s for %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise DealsError("could not reach %s: %s" % (url, exc.reason))
    except (socket.timeout, OSError) as exc:
        raise DealsError("could not read %s: %s" % (url, exc))


def _offline_path(folder, kind):
    if not os.path.isdir(folder):
        raise DealsError("--offline wants a folder, %s is not one" % folder)
    plain = os.path.join(folder, kind + ".csv")
    if os.path.isfile(plain):
        return plain
    dated = sorted(f for f in os.listdir(folder)
                   if re.match(r"^%s-\d{4}-\d{2}-\d{2}\.csv$" % kind, f))
    if dated:
        return os.path.join(folder, dated[-1])
    raise DealsError("offline: no %s.csv (or %s-YYYY-MM-DD.csv) in %s" % (kind, kind, folder))


def fetch_kind(kind, offline=None):
    """Deals of one kind from the network or the offline folder."""
    if offline:
        with open(_offline_path(offline, kind), "rb") as fh:
            data = fh.read()
    else:
        data = download(URLS[kind])
    problems = []
    return parse_csv(data, kind, problems), problems


def trade_key(d):
    """What makes two rows the same trade, whichever file they came from."""
    return (d["date"], d["symbol"], d["client"], d["side"], d["qty"], d["price"])


def drop_double_listed(deals):
    """(deals, dropped). NSE lists a block deal that is also over the bulk-deal threshold (0.5% of the
    company's shares) in BOTH files: the same buyer, seller, quantity and price twice. Counting both
    would double the money, so each bulk row that repeats a block deal is dropped (one for one) and the
    trade stays as the block deal."""
    pending = {}
    for d in deals:
        if d["kind"] == "block":
            pending[trade_key(d)] = pending.get(trade_key(d), 0) + 1
    kept, dropped = [], 0
    for d in deals:
        k = trade_key(d)
        if d["kind"] == "bulk" and pending.get(k, 0) > 0:
            pending[k] -= 1
            dropped += 1
        else:
            kept.append(d)
    return kept, dropped


def fetch(kinds, offline=None, warn=None):
    """(deals, problems) for the kinds asked. With two kinds, one failing is a
    warning; both failing is an error. With both files read, a block deal that NSE
    also lists in the bulk file is counted once (a note says how many)."""
    deals, problems, errors = [], [], []
    for i, kind in enumerate(kinds):
        if i and not offline and PAUSE:
            time.sleep(PAUSE)
        try:
            d, p = fetch_kind(kind, offline)
        except DealsError as exc:
            errors.append("%s: %s" % (kind, exc))
            continue
        deals += d
        problems += ["%s: %s" % (kind, x) for x in p]
    if errors and len(errors) == len(kinds):
        raise DealsError("; ".join(errors))
    for e in errors:
        problems.append("could not read " + e)
    if len(kinds) == 2:
        deals, twice = drop_double_listed(deals)
        if twice:
            problems.append("%d row(s) of the bulk file repeat a block deal (NSE lists a block deal that is "
                            "also over the bulk threshold in both files): each trade is counted once, as a "
                            "block deal" % twice)
    return deals, problems


# ---------------------------------------------------------------- your list

def _load_json(path):
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError as exc:
            raise DealsError("%s is not valid JSON: %s" % (path, exc))
    return None


def plain(sym):
    sym = str(sym).strip().upper()
    return sym[:-3] if sym.endswith(".NS") else sym


def watch_set(watchlist=None, plan=None, portfolio=None):
    """Symbols in the scanner's watchlist, the planned trade and your holdings."""
    out = set()
    for h in (_load_json(watchlist) or {}).get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out.add(plain(sym))
    sym = (_load_json(plan) or {}).get("symbol")
    if sym:
        out.add(plain(sym))
    for p in (_load_json(portfolio) or {}).get("positions", []):
        sym = p.get("symbol") or p.get("ticker")
        if sym:
            out.add(plain(sym))
    return out


# ---------------------------------------------------------------- selecting and grouping

def select(deals, symbols=None, client=None, min_value_cr=None):
    syms = {plain(s) for s in (symbols or [])}
    needle = (client or "").strip().lower()
    out = []
    for d in deals:
        if syms and d["symbol"] not in syms:
            continue
        if needle and needle not in d["client"].lower():
            continue
        if min_value_cr is not None and d["value_cr"] < min_value_cr:
            continue
        out.append(d)
    return out


def group(deals, watch=()):
    """One row per symbol: deals, clients, bought/sold quantity and value (crore),
    net quantity and net value. Biggest turnover first."""
    by = {}
    for d in deals:
        g = by.setdefault(d["symbol"], {
            "symbol": d["symbol"], "name": d["name"], "kinds": set(), "deals": 0,
            "clients": set(), "buy_qty": 0, "sell_qty": 0, "buy_cr": 0.0, "sell_cr": 0.0})
        g["kinds"].add(d["kind"])
        g["deals"] += 1
        g["clients"].add(d["client"])
        if d["side"] == "BUY":
            g["buy_qty"] += d["qty"]
            g["buy_cr"] += d["value_cr"]
        else:
            g["sell_qty"] += d["qty"]
            g["sell_cr"] += d["value_cr"]
        g["name"] = g["name"] or d["name"]
    out = []
    for g in by.values():
        g["kinds"] = "+".join(k for k in KINDS if k in g["kinds"])
        g["clients"] = len(g["clients"])
        g["net_qty"] = g["buy_qty"] - g["sell_qty"]
        g["net_cr"] = g["buy_cr"] - g["sell_cr"]
        g["on_list"] = g["symbol"] in watch
        out.append(g)
    out.sort(key=lambda g: (-(g["buy_cr"] + g["sell_cr"]), g["symbol"]))
    return out


# ---------------------------------------------------------------- state

def load_state(path):
    doc = _load_json(path)
    if doc is None:
        return {"seen": {}, "last_check": None}
    if not isinstance(doc.get("seen"), dict):
        doc["seen"] = {}
    doc.setdefault("last_check", None)
    return doc


def _write_json(path, doc):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def prune(seen, today, keep=KEEP_DAYS):
    cutoff = (today - _dt.timedelta(days=keep)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


def new_deals(deals, state):
    seen = state.get("seen", {})
    return [d for d in deals if deal_key(d) not in seen]


def load_history(path):
    doc = _load_json(path)
    if doc is None:
        return {"days": {}, "updated": None}
    if not isinstance(doc.get("days"), dict):
        doc["days"] = {}
    return doc


def add_history(doc, deals):
    """Put each fetched day's raw rows under its date (replacing that day and
    kind, so running twice does not duplicate anything)."""
    fetched = {}
    for d in deals:
        fetched.setdefault((d["date"], d["kind"]), []).append(
            {k: d[k] for k in ("symbol", "name", "client", "side", "qty", "price", "remarks")})
    for (date, kind), rows in fetched.items():
        doc["days"].setdefault(date, {})[kind] = rows
    return sorted({date for date, _ in fetched})


def history_deals(doc, symbols, days, today):
    """Saved deals for the symbols over the last `days` days, oldest first."""
    syms = {plain(s) for s in symbols}
    cutoff = (today - _dt.timedelta(days=days)).isoformat()
    out = []
    for date in sorted(doc["days"]):
        if date < cutoff:
            continue
        for kind in KINDS:
            for r in doc["days"][date].get(kind, []):
                if r["symbol"].upper() in syms:
                    d = dict(r, kind=kind, date=date)
                    d["value_cr"] = value_cr(d["qty"], d["price"])
                    out.append(d)
    return out


# ---------------------------------------------------------------- output

def _table(head, rows, align):
    """Plain text table. align: 'l' or 'r' per column."""
    widths = [max(len(str(x)) for x in col) for col in zip(head, *rows)] if rows else [len(h) for h in head]
    def fmt(cells):
        return "  ".join(str(c).ljust(w) if a == "l" else str(c).rjust(w)
                         for c, w, a in zip(cells, widths, align)).rstrip()
    return [fmt(head)] + [fmt(r) for r in rows]


def render_summary(groups):
    rows = []
    for g in groups:
        rows.append((g["symbol"] + (" *" if g["on_list"] else ""), g["name"][:26], g["kinds"],
                     g["deals"], g["clients"], indian_number(g["net_qty"]),
                     signed_cr(g["net_cr"]), money_cr(g["buy_cr"]), money_cr(g["sell_cr"])))
    head = ("SYMBOL", "COMPANY", "KIND", "DEALS", "CLIENTS", "NET QTY", "NET VALUE", "BOUGHT", "SOLD")
    return _table(head, rows, "lllrrrrrr")


def render_deals(deals, watch=()):
    rows = []
    for d in sorted(deals, key=lambda d: (d["symbol"], d["kind"], -d["value_cr"])):
        rows.append((d["kind"], d["symbol"] + (" *" if d["symbol"] in watch else ""), d["side"],
                     indian_number(d["qty"]), inr(d["price"]), money_cr(d["value_cr"]),
                     d["client"][:46]))
    head = ("KIND", "SYMBOL", "SIDE", "QUANTITY", "WGHT AVG PRICE", "VALUE", "CLIENT")
    return _table(head, rows, "lllrrrl")


def describe_filters(args):
    bits = []
    if getattr(args, "symbol", None):
        bits.append("symbol " + ", ".join(args.symbol))
    if getattr(args, "client", None):
        bits.append("client contains %r" % args.client)
    if getattr(args, "min_value_cr", None) is not None:
        bits.append("each deal at least %s" % money_cr(args.min_value_cr))
    return bits


def render_report(deals, watch=(), title="NSE bulk and block deals", filters=(), summary_only=False,
                  problems=()):
    dates = sorted({d["date"] for d in deals})
    count = {k: sum(1 for d in deals if d["kind"] == k) for k in KINDS}
    out = ["%s for %s: %d bulk, %d block deals" % (
        title, ", ".join(_nice_date(x) for x in dates) or "?", count["bulk"], count["block"])]
    if filters:
        out.append("Filter: %s. Net figures cover only the deals shown." % "; ".join(filters))
    out.append("")
    out.append("By symbol (net = bought minus sold, across the clients shown):")
    out += render_summary(group(deals, watch))
    if not summary_only:
        out.append("")
        out.append("Every deal:")
        out += render_deals(deals, watch)
    if any(d["symbol"] in watch for d in deals):
        out.append("")
        out.append("* on your watchlist, plan or portfolio")
    for p in problems:
        out.append("note: %s" % p)
    out.append("")
    out.append(NOTE)
    return "\n".join(out) + "\n"


def _nice_date(iso_date):
    d = _dt.date.fromisoformat(iso_date)
    return "%02d-%s-%d" % (d.day, d.strftime("%b"), d.year)


def render_history(deals, symbols, days):
    out = []
    by_day = {}
    for d in deals:
        by_day.setdefault(d["date"], []).append(d)
    tot_qty, tot_cr = 0, 0.0
    for date in sorted(by_day, reverse=True):
        rows = by_day[date]
        net_qty = sum(d["qty"] if d["side"] == "BUY" else -d["qty"] for d in rows)
        net_cr = sum(d["value_cr"] if d["side"] == "BUY" else -d["value_cr"] for d in rows)
        tot_qty += net_qty
        tot_cr += net_cr
        out.append("%s  %d deal(s), net %s shares, net %s" % (
            _nice_date(date), len(rows), indian_number(net_qty), signed_cr(net_cr)))
        for d in sorted(rows, key=lambda d: (d["symbol"], d["kind"], -d["value_cr"])):
            out.append("    %-5s %-9s %-4s %12s @ %s = %s  %s" % (
                d["kind"], d["symbol"], d["side"], indian_number(d["qty"]), inr(d["price"]),
                money_cr(d["value_cr"]), d["client"][:44]))
    out.append("")
    out.append("%s over the saved days of the last %d: %d day(s) with deals, net %s shares, net %s." % (
        ", ".join(sorted(plain(s) for s in symbols)), days, len(by_day),
        indian_number(tot_qty), signed_cr(tot_cr)))
    out.append(NOTE)
    return "\n".join(out) + "\n"


def public(deals):
    return [dict(d, value_cr=round(d["value_cr"], 6)) for d in deals]


def public_groups(groups):
    return [dict(g, buy_cr=round(g["buy_cr"], 6), sell_cr=round(g["sell_cr"], 6),
                 net_cr=round(g["net_cr"], 6)) for g in groups]


# ---------------------------------------------------------------- CLI

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def filters(p):
        p.add_argument("--symbol", nargs="+", metavar="SYM", help="only these NSE symbols")
        p.add_argument("--client", help="only clients whose name contains this text")
        p.add_argument("--min-value-cr", type=float, metavar="N",
                       help="only deals worth at least N crore rupees each")
        p.add_argument("--watchlist", default="trader/watchlist.json")
        p.add_argument("--plan", default="trader/plan.json")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--summary", action="store_true", help="symbol table only, not every deal")
        p.add_argument("--json", action="store_true")

    def fetching(p):
        p.add_argument("--kind", choices=("bulk", "block", "both"), default="both")
        p.add_argument("--offline", metavar="DIR", help="read bulk.csv / block.csv from a folder")

    p = sub.add_parser("latest", help="the latest trading day's deals")
    fetching(p)
    filters(p)

    p = sub.add_parser("new", help="only deals not shown before; saves history")
    fetching(p)
    filters(p)
    p.add_argument("--state", default="trader/state/deals.json")
    p.add_argument("--history-file", default="trader/state/deals-history.json")
    p.add_argument("--today", help="YYYY-MM-DD, for reproducible runs")
    p.add_argument("--dry-run", action="store_true", help="do not update the state or history files")

    p = sub.add_parser("history", help="saved days for a symbol")
    p.add_argument("--symbol", nargs="+", required=True, metavar="SYM")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--history-file", default="trader/state/deals-history.json")
    p.add_argument("--today", help="YYYY-MM-DD, for reproducible runs")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("parse", help="read a saved bulk or block CSV")
    p.add_argument("file")
    p.add_argument("--kind", choices=KINDS)
    filters(p)
    return ap


def _today(args):
    try:
        return _dt.date.fromisoformat(args.today) if getattr(args, "today", None) else _dt.date.today()
    except ValueError:
        raise DealsError("--today wants YYYY-MM-DD")


def _kinds(args):
    return KINDS if args.kind == "both" else (args.kind,)


def _show(args, deals, problems, title, watch):
    """Print a report or JSON for the (already filtered) deals."""
    if args.json:
        print(json.dumps({"dates": sorted({d["date"] for d in deals}), "deals": public(deals),
                          "symbols": public_groups(group(deals, watch)),
                          "notes": list(problems)}, indent=2))
    else:
        sys.stdout.write(render_report(deals, watch, title, describe_filters(args),
                                       args.summary, problems))


def main(argv=None):
    utf8_console()
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return _run(args)
    except (DealsError, OSError) as exc:
        print("deals: %s" % exc, file=sys.stderr)
        return 1


def _guess_kind(path, header_text):
    name = os.path.basename(path).lower()
    if "block" in name:
        return "block"
    if "bulk" in name:
        return "bulk"
    return "bulk" if "remarks" in header_text.splitlines()[0].lower() else "block"


def _run(args):
    if args.cmd == "history":
        doc = _load_json(args.history_file)
        if doc is None:
            raise DealsError("no history at %s yet: run `deals.py new` once each evening after "
                             "the market and it builds up" % args.history_file)
        hist = load_history(args.history_file)
        deals = history_deals(hist, args.symbol, args.days, _today(args))
        if args.json:
            print(json.dumps(public(deals), indent=2))
        elif deals:
            sys.stdout.write(render_history(deals, args.symbol, args.days))
        else:
            print("No saved deals for %s in the last %d days (%d day(s) saved)." % (
                ", ".join(plain(s) for s in args.symbol), args.days, len(hist["days"])))
        return 0 if deals else 1

    watch = watch_set(args.watchlist, args.plan, args.portfolio)

    if args.cmd == "parse":
        if not os.path.isfile(args.file):
            raise DealsError("no such file: %s" % args.file)
        with open(args.file, "rb") as fh:
            text = decode(fh.read())
        kind = args.kind or _guess_kind(args.file, text.lstrip() or "x")
        problems = []
        deals = select(parse_csv(text, kind, problems), args.symbol, args.client, args.min_value_cr)
        if not deals:
            print("No deals found in %s." % args.file)
            return 1
        _show(args, deals, problems, "Deals in %s" % os.path.basename(args.file), watch)
        return 0

    fetched, problems = fetch(_kinds(args), args.offline)
    deals = select(fetched, args.symbol, args.client, args.min_value_cr)

    if args.cmd == "latest":
        if not deals:
            flt = describe_filters(args)
            print("No %s deals%s (%d deal(s) in the latest file%s)." % (
                "/".join(_kinds(args)), " match: " + "; ".join(flt) if flt else "", len(fetched),
                ""))
            for p in problems:
                print("note: %s" % p)
            return 1
        _show(args, deals, problems, "NSE bulk and block deals", watch)
        return 0

    # new
    today = _today(args)
    state = load_state(args.state)
    fresh = new_deals(deals, state)
    if args.json:
        print(json.dumps({"dates": sorted({d["date"] for d in fresh}), "deals": public(fresh),
                          "symbols": public_groups(group(fresh, watch)), "notes": list(problems)},
                         indent=2))
    elif not fresh:
        print("Nothing new since %s (%d deal(s) in the file%s, all shown before)." % (
            state.get("last_check") or "the last check", len(deals),
            " after filters" if describe_filters(args) else ""))
        for p in problems:
            print("note: %s" % p)
    else:
        sys.stdout.write(render_report(fresh, watch, "NEW NSE bulk and block deals",
                                       describe_filters(args), args.summary, problems))
    if not args.dry_run:
        # remember what was shown (the deals that passed your filters), so each
        # deal alerts once; save every fetched day, unfiltered, to the history
        seen = prune(state["seen"], today)
        for d in deals:
            seen[deal_key(d)] = d["date"]
        state["seen"] = seen
        state["last_check"] = _dt.datetime.now().replace(microsecond=0).isoformat()
        _write_json(args.state, state)
        hist = load_history(args.history_file)
        saved = add_history(hist, fetched)
        hist["updated"] = state["last_check"]
        if saved:
            _write_json(args.history_file, hist)
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
