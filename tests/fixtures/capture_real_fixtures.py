"""Captures the REAL fixtures: live answers from Yahoo Finance, NSE and Google News, saved byte for byte
(a few are trimmed, see below). Needs curl_cffi (pip install curl_cffi) and a network connection.

    python tests/fixtures/capture_real_fixtures.py

The committed files were captured on Wed 2026-11-04 shortly after midnight IST, about nine hours after
the Tuesday 2026-11-03 session closed. That session is the first one with every real feature of the
Closing Auction Session (CAS, since 3 Aug 2026): continuous trading of F&O stocks ends at 15:15, Yahoo's 5-minute
bars show a stub at 15:15, a null bar at 15:20 and the auction at 15:25.

    trader-timemachine/RELIANCE-60d5m-real.json.gz    Yahoo v8 chart range=60d interval=5m (60 sessions)
    trader-entry/RELIANCE-1d5m-real-2026-11-03.json   Yahoo v8 chart range=1d interval=5m (one session)
    trader-scanner/real-spark-3mo-2026-11-03.json     Yahoo spark range=3mo interval=1d, 8 symbols
    trader-scanner/real-spark-5m-2026-11-03.json      Yahoo spark range=1d interval=5m, 8 symbols
    trader-news/nse-RELIANCE.json, nse-M&M.json       NSE corporate announcements, 2026-10-21 to 2026-11-04
    trader-news/gnews-RELIANCE.xml, gnews-M&M.xml     Google News RSS (newest 12 items kept)
    trader-news/chart-RELIANCE.json, chart-M&M.json   Yahoo v8 chart range=1d interval=5m
    trader-analyst/fundamentals-RELIANCE-real.json    Yahoo fundamentals-timeseries (the synthetic
                                                      fundamentals-RELIANCE.json is a different file)
    trader-deals/full-day/bulk.csv, block.csv         NSE's two complete deal files (nsearchives), 2026-11-03.
                                                      The 30-row bulk excerpt next to them was cut by hand
                                                      from the first rows of the same file.

Re-running it overwrites them with whatever the live sources say today, which breaks the numbers the tests
pin: do not run it unless you mean to refresh the fixtures and then update the tests.
"""

import calendar
import datetime
import gzip
import json
import os
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

from curl_cffi import requests

HERE = os.path.dirname(os.path.abspath(__file__))
YAHOO = {"Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
         "Referer": "https://finance.yahoo.com/"}
NSE = {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
       "Referer": "https://www.nseindia.com/"}
RSS = {"Accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "en-IN,en;q=0.9"}
SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC", "SBIN", "M&M", "BAJAJ-AUTO"]


def get(url, headers):
    time.sleep(0.5)
    r = requests.get(url, headers=headers, impersonate="chrome", timeout=60)
    if r.status_code != 200:
        sys.exit("HTTP %d for %s" % (r.status_code, url))
    return r.content


def put(folder, name, data, gz=False):
    path = os.path.join(HERE, folder, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if gz:
        with gzip.GzipFile(path, "wb", mtime=0) as fh:
            fh.write(data)
    else:
        with open(path, "wb") as fh:
            fh.write(data)
    print("wrote", os.path.relpath(path, HERE), len(data), "bytes")


def y(sym):
    return urllib.parse.quote(sym + ".NS", safe="")


def main():
    base = "https://query2.finance.yahoo.com"
    put("trader-timemachine", "RELIANCE-60d5m-real.json.gz",
        get(base + "/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m", YAHOO), gz=True)
    put("trader-entry", "RELIANCE-1d5m-real-2026-11-03.json",
        get(base + "/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m", YAHOO))
    syms = ",".join(y(s) for s in SYMBOLS)
    put("trader-scanner", "real-spark-3mo-2026-11-03.json",
        get(base + "/v7/finance/spark?symbols=%s&range=3mo&interval=1d" % syms, YAHOO))
    put("trader-scanner", "real-spark-5m-2026-11-03.json",
        get(base + "/v7/finance/spark?symbols=%s&range=1d&interval=5m" % syms, YAHOO))
    for sym in ("RELIANCE", "M&M"):
        url = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
               "&from_date=21-10-2026&to_date=04-11-2026" % urllib.parse.quote(sym, safe=""))
        put("trader-news", "nse-%s.json" % sym, get(url, NSE))
        put("trader-news", "chart-%s.json" % sym, get(base + "/v8/finance/chart/%s?range=1d&interval=5m" % y(sym), YAHOO))
    queries = {"RELIANCE": '"Reliance Industries" when:3d', "M&M": '"Mahindra & Mahindra" when:3d'}
    for sym, q in queries.items():
        data = get("https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(q), RSS)
        root = ET.fromstring(data)
        chan = root.find("channel")
        items = chan.findall("item")
        for extra in items[12:]:
            chan.remove(extra)
        put("trader-news", "gnews-%s.xml" % sym, ET.tostring(root, encoding="utf-8", xml_declaration=True))
    today = datetime.date(2026, 11, 4)
    p1 = calendar.timegm((today - datetime.timedelta(days=5 * 366)).timetuple())
    p2 = calendar.timegm((today + datetime.timedelta(days=1)).timetuple())
    types = ",".join(("quarterlyTotalRevenue", "quarterlyGrossProfit", "quarterlyOperatingIncome",
                      "quarterlyNetIncome", "quarterlyDilutedEPS"))
    url = ("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/RELIANCE.NS"
           "?symbol=RELIANCE.NS&type=%s&period1=%d&period2=%d&merge=false" % (types, p1, p2))
    put("trader-analyst", "fundamentals-RELIANCE-real.json", get(url, YAHOO))
    csv_headers = {"Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9",
                   "Referer": "https://www.nseindia.com/"}
    for kind in ("bulk", "block"):
        put("trader-deals", "full-day/%s.csv" % kind,
            get("https://nsearchives.nseindia.com/content/equities/%s.csv" % kind, csv_headers))


if __name__ == "__main__":
    main()
